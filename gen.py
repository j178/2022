import asyncio
import os
from datetime import datetime

import httpx
from playwright.async_api import async_playwright, Playwright, Page

LEETCODE_BASE = "https://leetcode-cn.com"
DEBUG = False

if os.environ.get("IN_GH_ACTION"):
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
) -> bool:
  print(f"Logging in leetcode as {username}")
  if not await login_leetcode(page, username, password):
    return False

  await page.goto(f"{LEETCODE_BASE}/u/{username}/")
  await page.wait_for_timeout(500)

  await page.screenshot(
    path=save_to,
    clip=dict(x=700, y=160, width=772, height=365)
  )
  return True


async def clip_github_calendar(
    page: Page, username: str, save_to: str,
) -> bool:
  print("Clipping github calendar")
  await page.goto(f"https://github.com/{username}")
  await page.wait_for_timeout(500)
  calendar = page.locator("div.js-yearly-contributions")
  await calendar.screenshot(path=save_to)
  return True


def update_readme_links(links: dict):
  with open("./README.md", "rt") as f:
    content = f.readlines()

  for name, link in links.items():
    i = content.index(f"<!--START_SECTION:{name}-->\n")
    j = content.index(f"<!--END_SECTION:{name}-->\n")
    content[i + 1:j] = [f"![{name}]({link})\n"]

  for line_no, line in enumerate(content):
    i = line.find("<!--START_INLINE:today-->")
    if i != -1:
      j = line.find("<!--END_INLINE:today-->")
      content[line_no] = line[:i + 25] + datetime.now().strftime("%Y-%m-%d") + line[j:]

  with open("./README.md", "wt") as f:
    f.write("".join(content))
  print("Updated README.md")


async def run() -> bool:
  lc_username = os.environ["LC_USERNAME"]
  lc_password = os.environ["LC_PASSWORD"]
  gh_username = os.environ["GH_USERNAME"]
  sm_token = os.environ["SM_TOKEN"]

  output_path = "./output"
  os.makedirs(output_path, exist_ok=True)

  leetcode_image = os.path.join(output_path, "leetcode_summary.png")
  github_image = os.path.join(output_path, "github_calendar.png")

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
    links = {}
    if await clip_leetcode_summary_page(
        page, lc_username, lc_password, leetcode_image
    ):
      leetcode_url = await upload_image(leetcode_image, sm_token)
      links["leetcode_summary"] = leetcode_url
    if await clip_github_calendar(
        page, gh_username, github_image
    ):
      github_url = await upload_image(github_image, sm_token)
      links["github_calendar"] = github_url

  if not links:
    print("No links to update")
    return False

  update_readme_links(links)
  await delete_old_images(sm_token)
  return True


def main():
  if not asyncio.run(run()):
    exit(1)


if __name__ == '__main__':
  main()
