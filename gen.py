import asyncio
import os

import httpx
from playwright.async_api import async_playwright, Playwright, Page

LEETCODE_BASE = "https://leetcode-cn.com"
DEBUG = True

if os.environ.get("IN_GH_ACTION"):
  DEBUG = False


async def login(page: Page, username: str, password: str) -> bool:
  await page.goto(LEETCODE_BASE)

  # wait for the popup
  await page.click("text=帐号密码登录")
  await page.click('[placeholder="手机/邮箱"]')
  await page.fill('[placeholder="手机/邮箱"]', username)
  await page.fill('[placeholder="输入密码"]', password)
  await page.click('button:has-text("登录")')
  await page.wait_for_timeout(500)

  # test if login succeed
  cookies = await page.context.cookies(LEETCODE_BASE)
  for cookie in cookies:
    if cookie["name"] == "LEETCODE_SESSION":
      print(f"Logged in as {username}")
      return True
  print(f"Login as {username} failed")
  return False


async def clip_leetcode_summary_page(
    playwright: Playwright,
    username: str,
    password: str,
    save_to: str,
) -> bool:
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

  print(f"Logging in as {username}")
  if not await login(page, username, password):
    return False

  await page.goto(f"{LEETCODE_BASE}/u/{username}/")
  await page.wait_for_timeout(500)

  await page.screenshot(
    path=save_to,
    clip=dict(x=700, y=160, width=772, height=365)
  )
  await browser.close()
  return True


async def upload_image(path: str):
  print(f"Uploading {path} to sm.ms")
  async with httpx.AsyncClient(trust_env=False) as client:
    resp = await client.post(
      "https://sm.ms/api/v2/upload",
      files={"smfile": open(path, "rb")},
      timeout=10,
    )
    resp.raise_for_status()
    url = resp.json()["data"]["url"]
    print(f"Uploaded: {url}")
    return url


def update_readme_links(links: dict):
  with open("./README.md.in", "rt") as f:
    content = f.read()
  content = content.format_map(links)
  with open("./README.md", "wt") as f:
    f.write(content)
  print("Updated README.md")


async def run():
  username = os.environ["LC_USERNAME"]
  password = os.environ["LC_PASSWORD"]
  output_path = "./output"
  os.makedirs(output_path, exist_ok=True)

  leetcode_image = os.path.join(output_path, "leetcode_summary.png")
  async with async_playwright() as playwright:
    if await clip_leetcode_summary_page(
      playwright, username, password, leetcode_image
    ):
      leetcode_url = await upload_image(leetcode_image)
      update_readme_links(
        {
          "leetcode_summary_image_url": leetcode_url,
          "github_calendar_url": "",
        }
      )


def main():
  asyncio.run(run())


if __name__ == '__main__':
  main()
