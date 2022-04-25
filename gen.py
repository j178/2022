import abc
import asyncio
import json
import os
import shutil
import time
import traceback
import typing

import httpx
import pendulum
from github_poster.poster import Poster
from github_poster.drawer import Drawer
from httpx import AsyncClient
from playwright.async_api import async_playwright, Page

TZ = pendulum.timezone("Asia/Shanghai")
DATA_FOLDER = "./data"
OUTPUT_FOLDER = "./output"
DEBUG_FOLDER = "./debug"
DEBUG = True
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.149 Safari/537.36"

pendulum.set_local_timezone(TZ)
if os.environ.get("CI"):
    DEBUG = False


class ImageService:
    name = "sm.ms"
    base_url = "https://sm.ms/api/v2"

    def __init__(self, credential):
        self.credential = credential
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            trust_env=False,
            headers={"Authorization": self.credential},
        )

    async def upload(self, path: str) -> str:
        log(f"Uploading {path} to {self.name}")
        resp = await self.client.post(
            "/upload",
            files={
                "smfile": open(path, "rb"),
            },
            timeout=10,
        )
        resp.raise_for_status()
        resp_data = resp.json()
        if resp_data["success"] is False:
            if resp_data["code"] == "image_repeated":
                log(f"Image {path} already exists")
                return resp_data["images"]
            else:
                raise Exception(f"Upload image {path} failed: {resp_data}")

        url = resp_data["data"]["url"]
        log(f"Uploaded: {url}")
        return url

    async def cleanup(self) -> None:
        log("Deleting old images")
        resp = await self.client.get("/upload_history")
        resp.raise_for_status()
        history = resp.json()["data"]
        now = pendulum.now("local")
        for item in history:
            created_at = pendulum.from_timestamp(item["created_at"], "local")
            filename = item["filename"]
            if (now - created_at).days >= 7:
                try:
                    await self.client.get(item["delete"])
                    log(f"Deleted image {filename}")
                except httpx.HTTPError as e:
                    log(f"Delete image {filename} failed: {e!r}")


def get_today() -> str:
    return pendulum.now("local").strftime("%Y-%m-%d")


def log(msg: str) -> None:
    print(msg, flush=True)


class LoginFailed(Exception):
    def __init__(self, name: str, msg: str | None = None):
        super().__init__(f"{name} login failed: {msg}")


T = typing.TypeVar("T", bound="DataGenerator")
Agent = typing.Union[AsyncClient, Page]


class DataGenerator:
    name: str
    image_service: ImageService

    @abc.abstractmethod
    async def generate(self: T) -> dict[str:str]:
        raise NotImplementedError


class GithubCalendar(DataGenerator):
    name = "github_calendar"

    @classmethod
    def from_env(cls, page: Page) -> T:
        return cls(os.environ["GH_USERNAME"], page)

    def __init__(self, username: str, page: Page):
        self.username = username
        self.page = page

    async def generate(self) -> dict[str:str]:
        page = self.page
        await page.goto(f"https://github.com/{self.username}")
        await page.wait_for_timeout(500)
        calendar = page.locator("div.js-yearly-contributions")

        save_to = os.path.join(OUTPUT_FOLDER, f"{self.name}.png")
        await calendar.screenshot(path=save_to)

        image_url = await self.image_service.upload(save_to)
        return {
            self.name: image_url,
            f"{self.name}_update_date": get_today(),
        }


class LoginDataGenerator(DataGenerator):
    cookie_domain: str

    def __init__(self, credentials: tuple, cookies: dict):
        self.credentials = credentials
        self.cookies = cookies

    async def login(self) -> None:
        log(f"Trying to login {self.name} by cookies")
        await self.login_by_cookies()
        if await self.check_login():
            log(f"Login {self.name} by cookies succeeded")
            return
        log(f"Trying to login {self.name} by credential")
        await self.login_by_credential()
        if await self.check_login():
            log(f"Login {self.name} by credential succeeded")
            return
        raise LoginFailed(self.name)

    @abc.abstractmethod
    async def login_by_credential(self) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    async def login_by_cookies(self) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    async def check_login(self) -> bool:
        raise NotImplementedError


class LeetcodeSummary(LoginDataGenerator):
    name = "leetcode_summary"
    base_url = "https://leetcode.cn"
    cookie_domain = ".leetcode.cn"

    def __init__(self, credentials: tuple, cookies: dict, page: Page):
        super().__init__(credentials, cookies)
        self.page = page

    @classmethod
    def from_env(cls, page: Page) -> T:
        cookies = os.environ.get("LC_COOKIES") or {}
        if cookies:
            cookies = parse_cookies_string(cookies)
        return cls(
            (os.environ["LC_USERNAME"], os.environ["LC_PASSWORD"]),
            cookies,
            page,
        )

    async def login_by_credential(self) -> None:
        page = self.page
        await page.goto(self.base_url)

        # wait for the popup
        await page.click("text=帐号密码登录")
        await page.click('[placeholder="手机/邮箱"]')
        await page.fill('[placeholder="手机/邮箱"]', self.credentials[0])
        await page.fill('[placeholder="输入密码"]', self.credentials[1])
        await page.click('button:has-text("登录")')
        await page.wait_for_timeout(500)

    async def login_by_cookies(self) -> None:
        if self.cookies:
            cookies = [
                {"name": k, "value": v, "domain": self.cookie_domain, "path": "/"}
                for k, v in self.cookies.items()
            ]
            await self.page.context.add_cookies(cookies)

    async def check_login(self) -> bool:
        page = self.page
        await page.goto(self.base_url)
        await page.reload()
        cnt = await page.locator("div[data-cypress=AuthLinks]").count()
        return cnt == 0

    async def generate(self) -> dict[str:str]:
        await self.login()

        await self.page.goto(f"{self.base_url}/u/{self.credentials[0]}/")
        await self.page.wait_for_timeout(2000)
        btn = self.page.locator("span:has-text('知道了')")
        if await btn.count() > 0:
            await btn.click()
            log("dismiss '知道了' button")
        else:
            log("'知道了' button not found")

        # await self.page.screenshot(
        #     path=os.path.join(DEBUG_FOLDER, f"leetcode-full-screen.png")
        # )
        save_to = os.path.join(OUTPUT_FOLDER, f"{self.name}.png")
        await self.page.screenshot(
            path=save_to, clip=dict(x=1380, y=393, width=1674, height=1207)
        )

        image_url = await self.image_service.upload(save_to)
        return {
            self.name: image_url,
            f"{self.name}_update_date": get_today(),
        }


class GeekTimeCalendar(LoginDataGenerator):
    name = "geek_time_calendar"
    base_url = "https://time.geekbang.org"
    cookie_domain = ".geekbang.org"

    def __init__(self, credentials: tuple, cookies: dict, page: Page):
        super().__init__(credentials, cookies)
        self.page = page

    @classmethod
    def from_env(cls, page: Page) -> T:
        cookies = os.environ.get("GT_COOKIES") or {}
        if cookies:
            cookies = parse_cookies_string(cookies)
        return cls(
            (os.environ["GT_USERNAME"], os.environ["GT_PASSWORD"]),
            cookies,
            page,
        )

    async def login_by_credential(self):
        page = self.page
        await page.goto("https://account.geekbang.org/login?country=86")
        await page.wait_for_timeout(500)

        # TODO: 模拟密码登录会有问题：操作过于频繁，请稍后再试
        await page.wait_for_selector('[placeholder="密码"]')
        await page.type('[placeholder="手机号"]', self.credentials[0], delay=10)
        await page.type('[placeholder="密码"]', self.credentials[1], delay=10)
        await page.check('input[type="checkbox"]')
        await page.click(':nth-match(:text("登录"), 3)')
        await page.wait_for_url("https://time.geekbang.org/", timeout=3000)

    async def login_by_cookies(self) -> None:
        if self.cookies:
            cookies = [
                {"name": k, "value": v, "domain": self.cookie_domain, "path": "/"}
                for k, v in self.cookies.items()
            ]
            await self.page.context.add_cookies(cookies)

    async def check_login(self) -> bool:
        await self.page.goto(self.base_url)
        await self.page.reload()
        cnt = await self.page.locator("div.profile-dropdown").count()
        return cnt == 1

    async def generate(self) -> dict[str:str]:
        await self.login()

        page = self.page
        await page.goto("https://time.geekbang.org/dashboard/usercenter")
        await page.wait_for_timeout(500)
        calendar = page.locator("div[class^=LearningRecord_learningWrapper]")

        save_to = os.path.join(OUTPUT_FOLDER, f"{self.name}.png")
        await calendar.screenshot(path=save_to)

        image_url = await self.image_service.upload(save_to)
        return {
            self.name: image_url,
            f"{self.name}_update_date": get_today(),
        }


class BilibiliHistory(LoginDataGenerator):
    name = "bilibili_history"

    def __init__(self, credentials: tuple, cookies: dict, client: AsyncClient):
        super().__init__(credentials, cookies)
        self.client = client

    @classmethod
    def from_env(cls, client: AsyncClient) -> T:
        cookies = os.environ["BILI_COOKIES"]
        cookies = parse_cookies_string(cookies)
        return cls((), cookies, client)

    async def login_by_cookies(self) -> None:
        self.client.cookies.update(self.cookies)

    async def login_by_credential(self) -> None:
        return

    async def check_login(self) -> bool:
        resp = await self.client.get("https://api.bilibili.com/x/web-interface/nav")
        data = resp.json()
        return data["data"]["isLogin"]

    def load_histories(self) -> dict[str, int]:
        with open(os.path.join(DATA_FOLDER, "bilibili_histories.json"), "rt") as f:
            data = json.load(f)
        return data

    def save_histories(self, data: dict[str, int]) -> None:
        data = {str(k): v for k, v in data.items()}
        with open(os.path.join(DATA_FOLDER, "bilibili_histories.json"), "wt") as f:
            json.dump(data, f, indent=2, sort_keys=True)

    async def get_yesterday_history(self) -> int:
        # 获取昨天的观看记录
        cnt = 0
        yesterday_starts = pendulum.yesterday().int_timestamp
        today_starts = pendulum.today().int_timestamp
        view_at = today_starts
        max = 0
        exhausted = False
        while not exhausted:
            log("Fetching bilibili history")
            resp = await self.client.get(
                "https://api.bilibili.com/x/web-interface/history/cursor",
                params={
                    "max": max,
                    "view_at": view_at,
                    "business": "archive",
                },
            )
            data = resp.json()
            max = data["data"]["cursor"]["max"]
            view_at = data["data"]["cursor"]["view_at"]

            for view in data["data"]["list"]:
                if view["view_at"] >= today_starts:
                    continue
                if yesterday_starts <= view["view_at"]:
                    cnt += 1
                else:
                    exhausted = True
                    break
            await asyncio.sleep(0.5)

        return cnt

    async def generate_svg(self, data: dict[str, int]) -> str:
        p = Poster()
        p.colors = {
            "background": "#222222",
            "track": "#fdc7d6",
            "special": "#fdaac2",
            "special2": "#c95b7a",
            "text": "#ffffff",
        }
        p.special_number = {
            "special_number1": 30,
            "special_number2": 15,
        }
        p.units = "videos"
        p.title = "j178 BiliBili"
        p.height = 35 + 43
        p.set_tracks(data, [pendulum.today().year], ["bilibili"])
        d = Drawer(p)
        save_to = os.path.join(OUTPUT_FOLDER, f"{self.name}.svg")
        p.draw(d, save_to)

        return save_to

    async def generate(self) -> dict[str:str]:
        await self.login()

        history = self.load_histories()
        yesterday = pendulum.yesterday().to_date_string()
        yesterday_count = await self.get_yesterday_history()
        history[yesterday] = yesterday_count
        self.save_histories(history)

        svg_path = await self.generate_svg(history)
        image_url = os.path.join(DATA_FOLDER, f"{self.name}.svg")
        shutil.copy(svg_path, DATA_FOLDER)
        return {
            self.name: image_url,
            f"{self.name}_update_date": get_today(),
        }


class WeReadHistory(LoginDataGenerator):
    name = "weread_history"
    base_url = "https://weread.qq.com/"
    read_detail_url = (
        "https://i.weread.qq.com/readdetail?baseTimestamp=0&count=12&type=0"
    )

    def __init__(
        self, credentials: tuple, cookies: dict[str, str], client: AsyncClient
    ) -> None:
        super().__init__(credentials, cookies)
        self.client = client

    @classmethod
    def from_env(cls, client: AsyncClient) -> T:
        cookies = os.environ["WEREAD_COOKIES"]
        cookies = parse_cookies_string(cookies)
        return cls((), cookies, client)

    async def login_by_credential(self) -> None:
        return

    async def login_by_cookies(self) -> None:
        self.client.cookies.update(self.cookies)

    async def check_login(self) -> bool:
        return True

    async def get_history(self, retries: int = 0) -> list:
        r = await self.client.get(self.read_detail_url)
        data = r.json()
        if data.get("errcode", 0) == -2012:
            if retries < 2:
                await self.client.get(self.base_url)
                return await self.get_history(retries + 1)
            else:
                raise LoginFailed(f"get weread history failed: {data}")
        return data["monthTimeSummary"]

    async def generate_svg(self, data: dict[str:str]) -> str:
        p = Poster()
        p.colors = {
            "background": "#222222",
            "track": "#abdcfc",
            "special": "#6dc2f9",
            "special2": "#2076ad",
            "text": "#ffffff",
        }
        p.special_number = {
            "special_number1": 30,
            "special_number2": 15,
        }
        p.units = "mins"
        p.title = "j178 WeRead"
        p.height = 35 + 43
        p.set_tracks(data, [pendulum.today().year], ["weread"])
        d = Drawer(p)
        save_to = os.path.join(OUTPUT_FOLDER, f"{self.name}.svg")
        p.draw(d, save_to)

        return save_to

    async def generate(self) -> dict[str:str]:
        await self.login()
        history = await self.get_history()
        data = {}
        for month in history:
            if month["monthTotalReadTime"] < 60:
                continue
            month_start = pendulum.from_timestamp(month["monthTimestamp"], tz="local")
            month_end = month_start.end_of("month")
            for date, seconds in zip(month_end - month_start, month["timeSample"]):
                data[date.to_date_string()] = round(seconds / 60, 2)

        svg_path = await self.generate_svg(data)
        image_url = os.path.join(DATA_FOLDER, f"{self.name}.svg")
        shutil.copy(svg_path, DATA_FOLDER)
        return {
            self.name: image_url,
            f"{self.name}_update_date": get_today(),
        }


def update_readme(params: dict):
    with open("./README.md.in", "rt") as f:
        content = f.read()

    with open("./data/readme.json", "rt") as f:
        data = json.load(f)

    params = {k: v for k, v in params.items() if v is not None}
    data.update(params)
    content = content.format_map(data)

    with open("./README.md", "wt") as f:
        f.write(content)
    with open("./data/readme.json", "wt") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    log("Updated README.md")


def parse_cookies_string(cookies_string: str) -> dict[str, str]:
    cookies = {}
    for cookie in cookies_string.split(";"):
        cookie = cookie.strip()
        if cookie:
            key, value = cookie.split("=", 1)
            cookies[key] = value
    return cookies


async def run() -> bool:
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    os.makedirs(DEBUG_FOLDER, exist_ok=True)

    sm_token = os.environ["SM_TOKEN"]
    image_service = ImageService(sm_token)
    DataGenerator.image_service = image_service

    client = httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        trust_env=False,
    )

    async with async_playwright() as playwright:
        if DEBUG:
            browser = await playwright.firefox.launch(headless=False, slow_mo=500)
        else:
            browser = await playwright.firefox.launch()

        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1920, "height": 1080},
            screen={"width": 1920, "height": 1080},
            device_scale_factor=2,
        )
        await context.add_init_script(path="stealth.min.js")
        page = await context.new_page()

        generators = [
            LeetcodeSummary.from_env(page),
            GithubCalendar.from_env(page),
            GeekTimeCalendar.from_env(page),
            WeReadHistory.from_env(client),
            BilibiliHistory.from_env(client),
        ]

        full_data = {}
        for generator in generators:
            log(f"Generating {generator.name}")
            try:
                data = await generator.generate()
                full_data.update(data)
                log(f"Generated {generator.name}")
            except Exception as e:
                traceback.print_exc()
                log(f"::error::Generate {generator.name} failed: {e!r}")
                await page.screenshot(
                    path=os.path.join(DEBUG_FOLDER, f"{generator.name}.png")
                )

    if not full_data:
        log("::error::No links to update")
        return False

    update_readme(full_data)
    await image_service.cleanup()
    await image_service.client.aclose()
    await client.aclose()

    return True


def main():
    if not asyncio.run(run()):
        exit(1)


if __name__ == "__main__":
    main()
