import abc
import asyncio
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from playwright.async_api import async_playwright, Page

LEETCODE_BASE = "https://leetcode-cn.com"
TZ = ZoneInfo("Asia/Shanghai")
OUTPUT_FOLDER = "./output"
DEBUG = True

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
    now = datetime.now()
    for item in history:
      created_at = datetime.fromtimestamp(item["created_at"])
      filename = item["filename"]
      if (now - created_at).days >= 7:
        try:
          await self.client.get(item["delete"])
          print(f"Deleted image {filename}")
        except httpx.HTTPError as e:
          print(f"Delete image {filename} failed: {e!r}")


def get_today() -> str:
  return datetime.now(timezone.utc).astimezone(TZ).strftime("%Y-%m-%d")


class DataGenerator:
  name: str

  @abc.abstractmethod
  async def generate(self) -> dict[str: str]:
    raise NotImplementedError


class LeetcodeSummary(DataGenerator):
  name = "leetcode_summary"

  def __init__(self, page: Page, username: str, password: str, image_service: ImageService):
    self.page = page
    self.username = username
    self.password = password
    self.image_service = image_service

  async def login_leetcode(self) -> bool:
    print(f"Logging in leetcode")
    page = self.page
    await page.goto(LEETCODE_BASE)

    # wait for the popup
    await page.click("text=帐号密码登录")
    await page.click('[placeholder="手机/邮箱"]')
    await page.fill('[placeholder="手机/邮箱"]', self.username)
    await page.fill('[placeholder="输入密码"]', self.password)
    await page.click('button:has-text("登录")')
    await page.wait_for_timeout(500)

    # test if login_leetcode succeed
    cookies = await page.context.cookies(LEETCODE_BASE)
    for cookie in cookies:
      if cookie["name"] == "LEETCODE_SESSION":
        print(f"Logged in leetcode")
        return True
    print(f"Login leetcode failed")
    return False

  async def generate(self) -> dict[str: str]:
    if not await self.login_leetcode():
      raise Exception("Login failed")

    await self.page.goto(f"{LEETCODE_BASE}/u/{self.username}/")
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


class GithubCalendar(DataGenerator):
  name = "github_calendar"

  def __init__(self, page: Page, username: str, image_service: ImageService):
    self.page = page
    self.username = username
    self.image_service = image_service

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


class GeekTimeCalendar(DataGenerator):
  name = "geek_time_calendar"

  def __init__(self, page: Page, phone: str, password: str, image_service: ImageService):
    self.page = page
    self.phone = phone
    self.password = password
    self.image_service = image_service

  async def login_geek_time(self) -> bool:
    page = self.page
    await page.goto("https://account.geekbang.org/login?country=86")
    await page.wait_for_timeout(500)

    await page.wait_for_selector('[placeholder="密码"]')
    await page.fill('[placeholder="手机号"]', self.phone)
    await page.fill('[placeholder="密码"]', self.password)
    await page.check('input[type="checkbox"]')
    await page.click(':nth-match(:text("登录"), 3)')
    await page.wait_for_url("https://time.geekbang.org/")

    # test if login_leetcode succeed
    cookies = await page.context.cookies("https://time.geekbang.org")
    for cookie in cookies:
      if cookie["name"] == "GCESS":
        print(f"Logged in geek time")
        return True
    print(f"Login geek time failed")
    return False

  async def generate(self) -> dict[str: str]:
    page = self.page
    if not await self.login_geek_time():
      raise Exception("Login failed")

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


def update_readme(params: dict):
  with open("./README.md.in", "rt") as f:
    content = f.read()

  with open("./data.json", "rt") as f:
    data = json.load(f)

  params = {k: v for k, v in params.items() if v is not None}
  data.update(params)
  content = content.format_map(data)

  with open("./README.md", "wt") as f:
    f.write(content)
  with open("./data.json", "wt") as f:
    json.dump(data, f, indent=2, sort_keys=True)
  print("Updated README.md")


async def run() -> bool:
  lc_username = os.environ["LC_USERNAME"]
  lc_password = os.environ["LC_PASSWORD"]
  gh_username = os.environ["GH_USERNAME"]
  gt_username = os.environ["GT_USERNAME"]
  gt_password = os.environ["GT_PASSWORD"]
  sm_token = os.environ["SM_TOKEN"]

  output_path = "./output"
  debug_path = "./debug"
  os.makedirs(output_path, exist_ok=True)
  os.makedirs(debug_path, exist_ok=True)

  image_service = ImageService(sm_token)

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
      LeetcodeSummary(page, lc_username, lc_password, image_service),
      GithubCalendar(page, gh_username, image_service),
      GeekTimeCalendar(page, gt_username, gt_password, image_service),
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
        await page.screenshot(path=os.path.join(debug_path, f"{source.name}.png"))

  if not full_data:
    print("::error::No links to update")
    return False

  update_readme(full_data)
  await image_service.cleanup()
  await image_service.client.aclose()
  return True


def main():
  if not asyncio.run(run()):
    exit(1)


if __name__ == '__main__':
  main()
