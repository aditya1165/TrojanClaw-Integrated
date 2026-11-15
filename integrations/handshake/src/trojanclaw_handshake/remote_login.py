"""Managed-browser sign-in. Capture only provider cookies, never IdP passwords."""

from urllib.parse import urlparse

from .profiles import LoginError, handshake_origin, handshake_page_authenticated


def validate_connect_url(url: str) -> str:
    parsed = urlparse(url)
    if (parsed.scheme != "wss" or not (parsed.hostname or "").endswith(".browserbase.com")
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError("Invalid managed-browser endpoint.")
    return url


async def remote_login(provider: str, action: str, connect_url: str, origin: str | None = None, session: dict | None = None) -> dict:
    from patchright.async_api import async_playwright

    validate_connect_url(connect_url)
    if provider not in {"brightspace", "handshake", "piazza"}:
        raise ValueError("Unknown provider.")
    if action not in {"prepare", "capture", "resize", "seed", "identity", "usc_prepare", "usc_handshake"}:
        raise ValueError("Unknown browser action.")
    if (action in {"identity", "usc_prepare"} and provider != "brightspace"
            or action == "usc_handshake" and provider != "handshake"):
        raise ValueError("Invalid provider action.")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(connect_url)
        try:
            context = browser.contexts[0]
            if action == "seed":
                from .session import session_cookies
                values = session_cookies(session, provider)
                await context.add_cookies([
                    {"name": name, "value": value, "url": session["origin"], "secure": True}
                    for name, value in values.items()
                ])
                return {"seeded": True}
            if action == "usc_handshake":
                # Keep the original live-view target and the authenticated USC
                # browser context. A new tab leaves the embedded viewer on D2L.
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto("https://usc.joinhandshake.com/auth/saml/70/session/new?redirect_to_idp=true",
                                wait_until="domcontentloaded", timeout=30000)
                # The app has already hidden the live view. Selecting the remote
                # tab also makes a user-requested MFA fallback show the right page.
                await page.bring_to_front()
                return {"prepared": True}
            if action == "identity":
                for page in context.pages:
                    if urlparse(page.url).hostname != "brightspace.usc.edu":
                        continue
                    versions = await page.request.get("https://brightspace.usc.edu/d2l/api/lp/versions/")
                    if not versions.ok:
                        raise ValueError("Cannot read Brightspace API version.")
                    version = (await versions.json())["LatestVersion"]
                    import re
                    if not re.fullmatch(r"[0-9]+\.[0-9]+", str(version)):
                        raise ValueError("Invalid Brightspace API version.")
                    response = await page.request.get(f"https://brightspace.usc.edu/d2l/api/lp/{version}/users/whoami")
                    if not response.ok:
                        raise ValueError("Cannot identify the Brightspace account.")
                    whoami = await response.json()
                    if not isinstance(whoami, dict) or not whoami.get("Identifier"):
                        raise ValueError("Missing Brightspace identity.")
                    return {"whoami": whoami}
                raise LoginError("login_incomplete", "Sign-in has not completed.")
            if action == "resize":
                for page in context.pages:
                    await page.set_viewport_size({"width": 1024, "height": 720})
                return {"resized": True}
            if action in {"prepare", "usc_prepare"}:
                page = context.pages[0] if context.pages else await context.new_page()
                url = "https://brightspace.usc.edu/d2l/login" if action == "usc_prepare" else "https://brightspace.usc.edu"
                if provider == "handshake":
                    school = handshake_origin(origin or "https://usc.joinhandshake.com") or "https://usc.joinhandshake.com"
                    url = (school + "/auth/saml/70/session/new?redirect_to_idp=true"
                           if school == "https://usc.joinhandshake.com" else school + "/explore")
                elif provider == "piazza":
                    url = "https://piazza.com/account/login"
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                return {"prepared": True}
            for page in context.pages:
                if provider == "handshake":
                    if not await handshake_page_authenticated(page):
                        continue
                    target = handshake_origin(page.url)
                elif provider == "piazza":
                    parsed = urlparse(page.url)
                    # The login URL can remain open for an authenticated saved
                    # context. The subsequent MCP read proves authentication.
                    if parsed.scheme != "https" or parsed.hostname != "piazza.com":
                        continue
                    target = "https://piazza.com"
                else:
                    parsed = urlparse(page.url)
                    if (parsed.hostname != "brightspace.usc.edu" or not parsed.path.startswith("/d2l/")
                            or any(part in parsed.path.lower() for part in ("login", "auth"))):
                        continue
                    target = "https://brightspace.usc.edu"
                cookies = await context.cookies([target])
                if provider == "piazza":
                    cookies = [cookie for cookie in cookies if cookie["name"] in {"session_id", "piazza_session"} and cookie.get("value")]
                    if {cookie["name"] for cookie in cookies} != {"session_id", "piazza_session"}:
                        continue
                if not cookies:
                    continue
                if provider == "brightspace" and not {"d2lSessionVal", "d2lSecureSessionVal"} <= {cookie["name"] for cookie in cookies}:
                    continue
                return {"session": {"origin": target, "cookies": cookies}}
            raise LoginError("login_incomplete", "Sign-in has not completed.")
        finally:
            # Browserbase keepAlive preserves the hosted browser on CDP disconnect.
            await browser.close()
