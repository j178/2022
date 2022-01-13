import asyncio
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from playwright.async_api import async_playwright, Page

LEETCODE_BASE = "https://leetcode-cn.com"
TZ = ZoneInfo("Asia/Shanghai")
DEBUG = True

if os.environ.get("CI"):
  DEBUG = False


async def upload_image(path: str, token: str) -> str:
  print(f"Uploading {path} to sm.ms")
  async with httpx.AsyncClient(trust_env=False) as client:
    resp = await client.post(
      "https://sm.ms/api/v2/upload",
      headers={"Authorization": token},
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


async def delete_old_images(token: str):
  print("Deleting old images")
  async with httpx.AsyncClient(trust_env=False) as client:
    resp = await client.get(
      "https://sm.ms/api/v2/upload_history",
      headers={"Authorization": token},
    )
    resp.raise_for_status()
    history = resp.json()["data"]
    now = datetime.now()
    for item in history:
      created_at = datetime.fromtimestamp(item["created_at"])
      if (now - created_at).days >= 7:
        await client.get(item["delete"])
        print(f"Deleted image {item['filename']}")


async def login_leetcode(page: Page, username: str, password: str) -> bool:
  await page.goto(LEETCODE_BASE)

  # wait for the popup
  await page.click("text=帐号密码登录")
  await page.click('[placeholder="手机/邮箱"]')
  await page.fill('[placeholder="手机/邮箱"]', username)
  await page.fill('[placeholder="输入密码"]', password)
  await page.click('button:has-text("登录")')
  await page.wait_for_timeout(500)

  # test if login_leetcode succeed
  cookies = await page.context.cookies(LEETCODE_BASE)
  for cookie in cookies:
    if cookie["name"] == "LEETCODE_SESSION":
      print(f"Logged in leetcode as {username}")
      return True
  print(f"Login leetcode as {username} failed")
  return False


async def clip_leetcode_summary_page(
    page: Page,
    username: str,
    password: str,
    save_to: str,
):
  print(f"Logging in leetcode as {username}")
  if not await login_leetcode(page, username, password):
    raise Exception("Login failed")

  await page.goto(f"{LEETCODE_BASE}/u/{username}/")
  await page.wait_for_timeout(2000)

  await page.screenshot(
    path=save_to,
    clip=dict(x=700, y=160, width=772, height=365)
  )


async def clip_github_calendar(
    page: Page, username: str, save_to: str,
):
  print("Clipping github calendar")
  await page.goto(f"https://github.com/{username}")
  await page.wait_for_timeout(500)
  calendar = page.locator("div.js-yearly-contributions")
  await calendar.screenshot(path=save_to)


async def login_geek_time(page: Page, phone: str, password: str) -> bool:
  await page.goto("https://account.geekbang.org/login?country=86")
  await page.wait_for_timeout(500)

  await page.wait_for_selector('[placeholder="密码"]')
  await page.fill('[placeholder="手机号"]', phone)
  await page.fill('[placeholder="密码"]', password)
  await page.check('input[type="checkbox"]')
  await page.click(':nth-match(:text("登录"), 3)')
  return True


async def clip_geek_time_calendar(
    page: Page, username: str, password: str, save_to: str,
):
  print("Clipping geek time calendar")
  if not await login_geek_time(page, username, password):
    raise Exception("Login failed")

  await page.goto("https://time.geekbang.org/dashboard/usercenter")
  await page.wait_for_timeout(500)
  calendar = page.locator("div[class^=LearningRecord_learningWrapper]")
  await calendar.screenshot(path=save_to)


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

  leetcode_image = "leetcode_summary.png"
  github_image = "github_calendar.png"
  geek_time_image = "geek_time_calendar.png"

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
    data = {}
    today = datetime.now(timezone.utc).astimezone(TZ).strftime("%Y-%m-%d")

    try:
      await clip_leetcode_summary_page(
          page, lc_username, lc_password, os.path.join(output_path, leetcode_image)
      )
      leetcode_url = await upload_image(leetcode_image, sm_token)
      data["leetcode_summary"] = leetcode_url
      data["leetcode_update_date"] = today
    except Exception as e:
      print(f"::error::Clip leetcode summary error: {e!r}")
      await page.screenshot(path=os.path.join(debug_path, leetcode_image))

    try:
      await clip_github_calendar(
          page, gh_username, os.path.join(output_path, github_image)
      )
      github_url = await upload_image(github_image, sm_token)
      data["github_calendar"] = github_url
      data["github_update_date"] = today
    except Exception as e:
      print(f"::error::Clip github calendar error: {e!r}")
      await page.screenshot(path=os.path.join(debug_path, github_image))

    try:
      await clip_geek_time_calendar(
        page, gt_username, gt_password, os.path.join(output_path, geek_time_image)
      )
      geek_time_url = await upload_image(geek_time_image, sm_token)
      data["geek_time_calendar"] = geek_time_url
      data["geek_time_update_date"] = today
    except Exception as e:
      print(f"::error::Clip geek time calendar error: {e!r}")
      await page.screenshot(path=os.path.join(debug_path, geek_time_image))

  if not data:
    print("::error::No links to update")
    return False

  update_readme(data)
  await delete_old_images(sm_token)
  return True


def main():
  if not asyncio.run(run()):
    exit(1)


if __name__ == '__main__':
  main()
