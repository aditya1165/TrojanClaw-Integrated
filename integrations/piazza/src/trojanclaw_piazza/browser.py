"""Visible, account-specific Chromium sign-in; no password handling in the app."""

import asyncio
from time import monotonic
from urllib.parse import urlparse

from .server import AccountClient, COOKIE_NAMES, directory, save_cookies


class LoginError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__("Complete Piazza sign-in in Chromium.")


def verified(cookies):
    account = AccountClient(cookies)
    try:
        account.status()
    finally:
        account.close()


async def login():
    from patchright.async_api import async_playwright
    folder = directory()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(user_data_dir=str(folder / "profile"), headless=False)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://piazza.com/account/login", wait_until="domcontentloaded")
            deadline = monotonic() + 300
            while monotonic() < deadline:
                if not context.pages:
                    raise LoginError("login_closed")
                dashboard = any(urlparse(tab.url).scheme == "https" and urlparse(tab.url).hostname == "piazza.com"
                                and (urlparse(tab.url).path == "/class" or urlparse(tab.url).path.startswith("/class/"))
                                for tab in context.pages)
                if dashboard:
                    cookies = [cookie for cookie in await context.cookies(["https://piazza.com"])
                               if cookie.get("name") in COOKIE_NAMES]
                    if {cookie["name"] for cookie in cookies if cookie.get("value")} == COOKIE_NAMES:
                        await asyncio.to_thread(verified, cookies)
                        save_cookies(cookies)
                        return
                await asyncio.sleep(1)
            raise LoginError("login_timeout")
        finally:
            await context.close()
