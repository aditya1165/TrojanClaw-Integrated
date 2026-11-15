"""Legacy desktop diagnostic, separate from the hosted app.

Requires explicitly installed playwright and keyring packages. It writes local
test credentials; it must never be called from a hosted route or service.

Run from backend/:  python -m app.connectors.usc_login

Opens a Chrome window. You log in to USC (Shibboleth + Duo) once on Brightspace.
Piazza then reuses the same USC login. Only the Brightspace and Piazza cookies are
saved; the USC login cookie is never saved, and no cookie value is printed.

Saved to:
  Brightspace: macOS Keychain, service "trojanclaw", account "brightspace_cookie"
               (the value brightspace-mcp-server reads as D2L_SESSION_COOKIE)
  Piazza:      ~/.config/trojanclaw/piazza-cookies.json (read by mcp-piazza through
               PIAZZA_COOKIE_FILE; your own mcp-piazza login is left untouched)
"""

import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import keyring
from playwright.sync_api import BrowserContext, Page, sync_playwright

BRIGHTSPACE_URL = "https://brightspace.usc.edu/d2l/home"
BRIGHTSPACE_HOST = "brightspace.usc.edu"
BRIGHTSPACE_COOKIES = ("d2lSessionVal", "d2lSecureSessionVal")
PIAZZA_URL = "https://piazza.com/account/login"

KEYCHAIN_SERVICE = "trojanclaw"
KEYCHAIN_BRIGHTSPACE = "brightspace_cookie"
PIAZZA_COOKIE_FILE = Path("~/.config/trojanclaw/piazza-cookies.json").expanduser()

WAIT_SECONDS = 300


def _cookies(context: BrowserContext, domain: str) -> dict[str, str]:
    return {
        c["name"]: c["value"]
        for c in context.cookies()
        if c["domain"].lstrip(".").endswith(domain) and c["value"]
    }


def _wait_for(description: str, done) -> bool:
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if done():
            return True
        time.sleep(1)
    print(f"Timed out after {WAIT_SECONDS // 60} minutes waiting for {description}.")
    return False


def _host_path(page: Page) -> tuple[str, str]:
    url = urlparse(page.url)
    return url.hostname or "", url.path


def wait_for_brightspace(context: BrowserContext, page: Page) -> str | None:
    """Open Brightspace, wait for the USC login, return the D2L_SESSION_COOKIE value (None on timeout)."""
    page.goto(BRIGHTSPACE_URL)

    def logged_in() -> bool:
        host, path = _host_path(page)
        cookies = _cookies(context, BRIGHTSPACE_HOST)
        return host == BRIGHTSPACE_HOST and path.startswith("/d2l/home") and all(
            name in cookies for name in BRIGHTSPACE_COOKIES
        )

    if not _wait_for("the Brightspace login", logged_in):
        return None
    cookies = _cookies(context, BRIGHTSPACE_HOST)
    return "; ".join(f"{name}={cookies[name]}" for name in BRIGHTSPACE_COOKIES)


def read_whoami(page: Page) -> dict:
    """Who is logged in to Brightspace: Identifier, FirstName, LastName, UniqueName."""
    versions = page.request.get(f"https://{BRIGHTSPACE_HOST}/d2l/api/lp/versions/")
    if not versions.ok:
        raise RuntimeError(f"Brightspace API versions returned HTTP {versions.status}")
    latest = versions.json()["LatestVersion"]
    response = page.request.get(f"https://{BRIGHTSPACE_HOST}/d2l/api/lp/{latest}/users/whoami")
    if not response.ok:
        raise RuntimeError(f"Brightspace whoami returned HTTP {response.status}")
    return response.json()


def login_brightspace(context: BrowserContext, page: Page) -> bool:
    print("1/2 Brightspace: log in with your USC account and approve Duo in the window.")
    value = wait_for_brightspace(context, page)
    if value is None:
        return False
    keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_BRIGHTSPACE, value)
    print("    Saved the Brightspace session to the Keychain.")

    try:
        whoami = read_whoami(page)
        print(f"    Logged in as {whoami.get('FirstName')} {whoami.get('LastName')} ({whoami.get('UniqueName')}).")
    except Exception as exc:  # informational only; the login itself already succeeded
        print(f"    Could not read who is logged in: {exc}")
    return True


def login_piazza(context: BrowserContext, page: Page) -> bool:
    print("2/2 Piazza: choose your school (USC) login. USC should not ask for your password again.")
    page.goto(PIAZZA_URL)

    def logged_in() -> bool:
        host, path = _host_path(page)
        return (
            host.endswith("piazza.com")
            and not path.startswith("/account")
            and "session_id" in _cookies(context, "piazza.com")
        )

    if not _wait_for("the Piazza login", logged_in):
        return False

    PIAZZA_COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(PIAZZA_COOKIE_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(_cookies(context, "piazza.com"), f)
    os.chmod(PIAZZA_COOKIE_FILE, 0o600)
    print(f"    Saved the Piazza session to {PIAZZA_COOKIE_FILE}.")
    return True


def main() -> int:
    with sync_playwright() as playwright:
        # A fresh window that does not touch your normal Chrome profile.
        browser = playwright.chromium.launch(channel="chrome", headless=False)
        context = browser.new_context()
        page = context.new_page()
        try:
            brightspace_ok = login_brightspace(context, page)
            piazza_ok = brightspace_ok and login_piazza(context, page)
        finally:
            browser.close()

    print()
    print(f"Brightspace: {'saved' if brightspace_ok else 'not saved'}")
    print(f"Piazza:      {'saved' if piazza_ok else 'not saved'}")
    if brightspace_ok and piazza_ok:
        print("Next, check both MCP servers: see app/connectors/check.py")
    return 0 if brightspace_ok and piazza_ok else 1


if __name__ == "__main__":
    sys.exit(main())
