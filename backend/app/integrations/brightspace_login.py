"""Brightspace steps of Sign in with USC: wait for the USC login, then ask who logged in."""

import time
from urllib.parse import urlparse

from playwright.sync_api import BrowserContext, Page

BRIGHTSPACE_URL = "https://brightspace.usc.edu/d2l/home"
BRIGHTSPACE_HOST = "brightspace.usc.edu"
BRIGHTSPACE_COOKIES = ("d2lSessionVal", "d2lSecureSessionVal")
WAIT_SECONDS = 300


def _cookie_names(context: BrowserContext) -> set[str]:
    return {c["name"] for c in context.cookies() if c["domain"].lstrip(".").endswith(BRIGHTSPACE_HOST) and c["value"]}


def wait_for_brightspace(context: BrowserContext, page: Page) -> bool:
    """Open Brightspace and wait (up to five minutes) until the student has finished the USC login."""
    page.goto(BRIGHTSPACE_URL)
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        url = urlparse(page.url)
        if (url.hostname == BRIGHTSPACE_HOST and url.path.startswith("/d2l/home")
                and set(BRIGHTSPACE_COOKIES) <= _cookie_names(context)):
            return True
        time.sleep(1)
    return False


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
