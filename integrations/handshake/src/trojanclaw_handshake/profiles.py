"""Separate persistent Chromium profiles for each local connector."""

import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import urlparse


def session_root() -> Path:
    return Path(os.environ.get("TROJANCLAW_SESSION_DIR", "").strip() or Path.home() / ".trojanclaw" / "connectors").expanduser().resolve()


def configure_handshake() -> None:
    if not os.environ.get("HANDSHAKE_DIR", "").strip():
        os.environ["HANDSHAKE_DIR"] = str(session_root() / "handshake")


def handshake_origin(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or not host.endswith(".joinhandshake.com") or parsed.port not in (None, 443)):
        return None
    return f"https://{host}"


def configure_handshake_api() -> None:
    """Bind the pinned upstream client to the origin that owns the saved session."""
    import handshake_mcp.client as client

    origin = "https://app.joinhandshake.com"
    path = Path(os.environ["HANDSHAKE_DIR"]).expanduser() / "origin.json"
    if path.exists():
        candidate = json.loads(path.read_text(encoding="utf-8")).get("origin", "")
        origin = handshake_origin(candidate)
        if not origin:
            raise RuntimeError("Invalid Handshake origin. Reconnect Handshake.")
    client.BASE_URL = origin
    client.API_BASE = f"{origin}/stu"
    client.GQL_URL = f"{origin}/hs/graphql"
    client._csrf_token = None


class LoginError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _atomic_json(directory: Path, name: str, data) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=".session-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream)
        os.replace(temporary, directory / name)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _save_cookie_snapshot(directory: Path, cookies: list[dict]) -> None:
    _atomic_json(directory, "cookies.json", cookies)


async def handshake_page_authenticated(page) -> bool:
    """Use the signed-in browser's own TLS/session; never return account data."""
    if not handshake_origin(page.url):
        return False
    try:
        return await asyncio.wait_for(page.evaluate("""async () => {
            const token = document.querySelector('meta[name="csrf-token"]')?.content;
            if (!token) return false;
            const response = await fetch('/hs/graphql', {
                method: 'POST', credentials: 'same-origin',
                headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token},
                body: JSON.stringify({query: '{ currentUser { id } }'})
            });
            if (!response.ok) return false;
            const payload = await response.json();
            return Boolean(payload.data?.currentUser?.id);
        }"""), timeout=10)
    except Exception:
        # Login redirects and closed tabs are expected while the user signs in.
        return False


async def login_handshake() -> None:
    from patchright.async_api import async_playwright

    directory = Path(os.environ["HANDSHAKE_DIR"]).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(directory / "profile"), headless=False,
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://app.joinhandshake.com/login", wait_until="domcontentloaded")
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                pages = list(context.pages)
                if not pages:
                    raise LoginError("login_closed", "The Handshake window closed before sign-in was confirmed. Reconnect and leave it open until it closes automatically.")
                for candidate in pages:
                    if await handshake_page_authenticated(candidate):
                        origin = handshake_origin(candidate.url)
                        if not origin:
                            continue
                        cookies = await context.cookies([origin])
                        if not cookies:
                            continue
                        _save_cookie_snapshot(directory, cookies)
                        _atomic_json(directory, "origin.json", {"origin": origin})
                        return
                await asyncio.sleep(1)
            raise LoginError("login_timeout", "Handshake did not confirm a signed-in account within five minutes. Reconnect and finish signing in in Chromium.")
        finally:
            await context.close()


def brightspace_directory() -> Path:
    return session_root() / "brightspace"


def save_brightspace_cookies(cookies: list[dict]) -> None:
    _save_cookie_snapshot(brightspace_directory(), cookies)


def brightspace_cookie_header() -> str:
    path = brightspace_directory() / "cookies.json"
    if not path.exists():
        raise RuntimeError("Connect Brightspace first.")
    cookies = json.loads(path.read_text(encoding="utf-8"))
    values = {item["name"]: item["value"] for item in cookies}
    required = ("d2lSessionVal", "d2lSecureSessionVal")
    if not all(values.get(name) for name in required):
        raise RuntimeError("Reconnect Brightspace to refresh its session.")
    return "; ".join(f"{name}={values[name]}" for name in required)


async def login_brightspace() -> None:
    from patchright.async_api import async_playwright

    directory = brightspace_directory()
    directory.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(directory / "profile"), headless=False,
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://brightspace.usc.edu", wait_until="domcontentloaded")
            for _ in range(600):
                cookies = await context.cookies(["https://brightspace.usc.edu"])
                names = {item["name"] for item in cookies if item.get("value")}
                on_dashboard = any(
                    urlparse(tab.url).hostname == "brightspace.usc.edu"
                    and urlparse(tab.url).path.startswith("/d2l/")
                    and not any(part in urlparse(tab.url).path.lower() for part in ("login", "auth"))
                    for tab in context.pages
                )
                if on_dashboard and {"d2lSessionVal", "d2lSecureSessionVal"} <= names:
                    save_brightspace_cookies(cookies)
                    return
                await asyncio.sleep(0.5)
            raise RuntimeError("Brightspace login timed out. Please reconnect and finish Duo verification.")
        finally:
            await context.close()
