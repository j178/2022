import asyncio
import os

import httpx
from playwright.async_api import async_playwright, Playwright, BrowserContext

LEETCODE_BASE = "https://leetcode-cn.com"
DEBUG = True
if os.environ.get("IN_GH_ACTION"):
  DEBUG = False

async def login(browser: BrowserContext, username: str, password: str):
  page = await browser.new_page()
  await page.goto(LEETCODE_BASE)

  # wait for the popup
  await page.click("text=帐号密码登录")
  await page.click('[placeholder="手机/邮箱"]')
  await page.fill('[placeholder="手机/邮箱"]', username)
  await page.fill('[placeholder="输入密码"]', password)
  await page.click('button:has-text("登录")')


async def clip_leetcode_summary_page(
    playwright: Playwright,
    username: str,
    password: str,
    save_to: str,
):
  if DEBUG:
    browser = await playwright.firefox.launch(headless=False, slow_mo=500)
  else:
    browser = await playwright.firefox.launch()
  context = await browser.new_context(
    viewport={"width": 1920, "height": 1080},
    screen={"width": 1920, "height": 1080},
    device_scale_factor=2,
  )

  await login(context, username, password)

  page = await context.new_page()
  await page.goto(f"{LEETCODE_BASE}/u/{username}/")
  await page.wait_for_timeout(500)

  await page.screenshot(
    path=save_to,
    clip=dict(x=700, y=160, width=772, height=365)
  )
  await browser.close()


async def upload_image(path: str):
  client = httpx.AsyncClient()
  resp = await client.post(
    "https://sm.ms/api/v2/upload",
    files={"smfile": open(path, "rb")},
    timeout=10,
  )
  resp.raise_for_status()
  return resp.json()["data"]["url"]


def update_readme_links(links: dict):
  with open("./README.md.in", "rt") as f:
    content = f.read()
  content = content.format_map(links)
  with open("./README.md", "wt") as f:
    f.write(content)


async def run():
  username = os.environ["LC_USERNAME"]
  password = os.environ["LC_PASSWORD"]
  output_path = "./output"
  os.makedirs(output_path, exist_ok=True)

  leetcode_image = os.path.join(output_path, "leetcode_summary.png")
  async with async_playwright() as playwright:
    await clip_leetcode_summary_page(
      playwright, username, password, leetcode_image
    )
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
