import abc
import asyncio
import json
import os

import httpx
import pendulum
from playwright.async_api import async_playwright, Page

TZ = pendulum.timezone("Asia/Shanghai")
OUTPUT_FOLDER = "./output"
DEBUG_FOLDER = "./debug"
DEBUG = True

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
    print(f"Uploading {path} to {self.name}")
    resp = await self.client.post(
      "/upload",
      files={
        "smfile": open(path, "rb"),
      },
      timeout=10,
    )
    resp.raise_for_status()
    resp_data = resp.json()
    if resp_data["success"] is False and resp_data["code"] == "image_repeated":
      print(f"Image {path} already exists")
      return resp_data["images"]
    else:
      url = resp_data["data"]["url"]
      print(f"Uploaded: {url}")
    return url

  async def cleanup(self) -> None:
    print("Deleting old images")
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
          print(f"Deleted image {filename}")
        except httpx.HTTPError as e:
          print(f"Delete image {filename} failed: {e!r}")


def get_today() -> str:
  return pendulum.now("local").strftime("%Y-%m-%d")


class LoginFailed(Exception):
  def __init__(self, name: str, msg: str | None = None):
    super().__init__(f"{name} login failed: {msg}")


class DataGenerator:
  name: str
  image_service: ImageService

  @abc.abstractmethod
  async def generate(self) -> dict[str: str]:
    raise NotImplementedError


class GithubCalendar(DataGenerator):
  name = "github_calendar"

  def __init__(self, username: str, page: Page):
    self.username = username
    self.page = page

  async def generate(self) -> dict[str: str]:
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

  def __init__(self, credentials: tuple, cookies: dict, page: Page):
    self.credentials = credentials
    self.cookies = cookies
    self.page = page

  async def login(self) -> None:
    print(f"Trying to login {self.name} by cookies")
    await self.login_by_cookies()
    if await self.check_login():
      print(f"Login {self.name} by cookies succeeded")
      return
    print(f"Trying to login {self.name} by credential")
    await self.login_by_credential()
    if await self.check_login():
      print(f"Login {self.name} by credential succeeded")
      return
    raise LoginFailed(self.name)

  @abc.abstractmethod
  async def login_by_credential(self) -> None:
    raise NotImplementedError

  async def login_by_cookies(self) -> None:
    if self.cookies:
      cookies = [
        {"name": k, "value": v, "domain": self.cookie_domain, "path": "/"}
        for k, v in self.cookies.items()
      ]
      await self.page.context.add_cookies(cookies)

  @abc.abstractmethod
  async def check_login(self) -> bool:
    raise NotImplementedError


class LeetcodeSummary(LoginDataGenerator):
  name = "leetcode_summary"
  base_url = "https://leetcode-cn.com"
  cookie_domain = ".leetcode-cn.com"

  async def login_by_credential(self):
    page = self.page
    await page.goto(self.base_url)

    # wait for the popup
    await page.click("text=帐号密码登录")
    await page.click('[placeholder="手机/邮箱"]')
    await page.fill('[placeholder="手机/邮箱"]', self.credentials[0])
    await page.fill('[placeholder="输入密码"]', self.credentials[1])
    await page.click('button:has-text("登录")')
    await page.wait_for_timeout(500)

  async def check_login(self) -> bool:
    await self.page.goto(self.base_url)
    cnt = await self.page.locator("div[data-cypress=AuthLinks]").count()
    return cnt == 0

  async def generate(self) -> dict[str: str]:
    await self.login()

    await self.page.goto(f"{self.base_url}/u/{self.credentials[0]}/")
    await self.page.wait_for_timeout(2000)

    save_to = os.path.join(OUTPUT_FOLDER, f"{self.name}.png")
    await self.page.screenshot(
      path=save_to,
      clip=dict(x=700, y=160, width=772, height=365)
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

  async def login_by_credential(self):
    page = self.page
    await page.goto("https://account.geekbang.org/login?country=86")
    await page.wait_for_timeout(500)

    await page.wait_for_selector('[placeholder="密码"]')
    await page.fill('[placeholder="手机号"]', self.credentials[0])
    await page.fill('[placeholder="密码"]', self.credentials[1])
    await page.check('input[type="checkbox"]')
    await page.click(':nth-match(:text("登录"), 3)')
    await page.wait_for_url("https://time.geekbang.org/")

  async def check_login(self) -> bool:
    await self.page.goto(self.base_url)
    cnt = await self.page.locator("div.profile-dropdown").count()
    return cnt == 1

  async def generate(self) -> dict[str: str]:
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

  def __init__(self, credentials: tuple, cookies: dict[str, str], page: Page | None):
    super().__init__(credentials, cookies, page)
    self.client = httpx.AsyncClient(
      base_url="https://api.bilibili.com",
      headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.149 Safari/537.36"},
      trust_env=False,
    )

  async def login_by_cookies(self) -> None:
    self.client.cookies.update(self.cookies)

  async def login_by_credential(self):
    return

  async def check_login(self) -> bool:
    resp = await self.client.get("/x/web-interface/nav")
    data = resp.json()
    return data["data"]["isLogin"]

  def load_histories(self) -> dict[int, int]:
    # 加载本地缓存的历史记录
    with open("./data/bilibili_histories.json", "rt") as f:
      data = json.load(f)
    return {int(k): v for k, v in data.items()}

  def save_histories(self, data: dict[int, int]) -> None:
    data = {str(k): v for k, v in data.items()}
    with open("./data/bilibili_histories.json", "wt") as f:
      json.dump(data, f, indent=2, sort_keys=True)

  async def get_yesterday_history(self) -> int:
    # 获取昨天的观看记录
    # 循环调用，直到获取完一天的所有记录
    cnt = 0
    yesterday_starts = pendulum.yesterday().int_timestamp
    today_starts = pendulum.today().int_timestamp
    view_at = today_starts
    max = 0
    exhausted = False
    while not exhausted:
      resp = await self.client.get(
        "/x/web-interface/history/cursor",
        params={
          "max": max,
          "viewed_at": view_at,
          "business": "archive",
        }
      )
      data = resp.json()
      max = data["data"]["cursor"]["max"]
      view_at = data["data"]["cursor"]["view_at"]

      for view in data["data"]["list"]:
        if view["view_at"] >= yesterday_starts:
          cnt += 1
        else:
          exhausted = True
          break

    return cnt

  async def generate_svg(self, data: dict[int, int]) -> str:
    # import svgwrite
    # TODO generate svg
    print(data)
    return ""

  async def generate(self) -> dict[str: str]:
    await self.login()

    history = self.load_histories()
    yesterday = pendulum.yesterday().day_of_year
    yesterday_count = await self.get_yesterday_history()
    history[yesterday] = yesterday_count
    self.save_histories(history)

    svg_path = await self.generate_svg(history)
    # image_url = await self.image_service.upload(svg_path)
    image_url = svg_path
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
  print("Updated README.md")


def parse_cookies_string(cookies_string: str) -> dict[str, str]:
  cookies = {}
  for cookie in cookies_string.split(";"):
    cookie = cookie.strip()
    if cookie:
      key, value = cookie.split("=", 1)
      cookies[key] = value
  return cookies


async def run() -> bool:
  sm_token = os.environ["SM_TOKEN"]
  gh_username = os.environ["GH_USERNAME"]

  lc_username = os.environ["LC_USERNAME"]
  lc_password = os.environ["LC_PASSWORD"]
  lc_cookies = os.environ.get("LC_COOKIES")
  if lc_cookies:
    lc_cookies = parse_cookies_string(lc_cookies)
  else:
    lc_cookies = {}

  gt_username = os.environ["GT_USERNAME"]
  gt_password = os.environ["GT_PASSWORD"]
  gt_cookies = os.environ.get("GT_COOKIES")
  if gt_cookies:
    gt_cookies = parse_cookies_string(gt_cookies)
  else:
    gt_cookies = {}

  bili_cookies = os.environ["BILI_COOKIES"]
  bili_cookies = parse_cookies_string(bili_cookies)

  os.makedirs(OUTPUT_FOLDER, exist_ok=True)
  os.makedirs(DEBUG_FOLDER, exist_ok=True)

  image_service = ImageService(sm_token)
  DataGenerator.image_service = image_service

  async with async_playwright() as playwright:
    print("Launching firefox browser")
    if DEBUG:
      browser = await playwright.firefox.launch(headless=False, slow_mo=500)
    else:
      browser = await playwright.firefox.launch()

    context = await browser.new_context(
      viewport={"width": 1920, "height": 1080},
      screen={"width": 1920, "height": 1080},
      device_scale_factor=2,
    )
    page = await context.new_page()

    sources = [
      LeetcodeSummary((lc_username, lc_password), lc_cookies, page),
      GithubCalendar(gh_username, page),
      GeekTimeCalendar((gt_username, gt_password), gt_cookies, page),
      BilibiliHistory((), bili_cookies, page),
    ]
    full_data = {}
    for source in sources:
      print(f"Generating {source.name}")
      try:
        data = await source.generate()
        full_data.update(data)
        print(f"Generated {source.name}")
      except Exception as e:
        print(f"::error::Generate {source.name} failed: {e!r}")
        await page.screenshot(path=os.path.join(DEBUG_FOLDER, f"{source.name}.png"))

  if not full_data:
    print("::error::No links to update")
    return False

  update_readme(full_data)
  await image_service.cleanup()

  return True


def main():
  if not asyncio.run(run()):
    exit(1)


if __name__ == '__main__':
  main()
