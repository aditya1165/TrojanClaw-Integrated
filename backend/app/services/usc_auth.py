"""Sign in with USC, then set up the connectors in the same browser.

1. A Chrome popup opens on this computer. The student logs in to USC (Shibboleth + Duo) on Brightspace.
2. Brightspace says who logged in. The page signs the student in to TrojanClaw ("Setting things up"),
   and Brightspace's own session is saved for its connector.
3. The popup is minimized and, in the same browser, each site in SETUP opens through its direct USC
   sign-in link. USC's login session is still alive there, so the site signs in without a password.
   If a site still needs the student (for example Duo again), the popup comes back to the front.
4. The browser closes. USC's login cookies are session cookies, so nothing of the USC login is kept.

The backend must run on the student's computer, because the popup opens there.
"""

from dataclasses import dataclass, field
import logging
import secrets
import threading
import time
from typing import Callable
from urllib.parse import urlparse
from uuid import UUID

from fastapi import HTTPException

from app.integrations.local import get_local_connectors
from app.integrations.models import IntegrationState
from app.integrations.saved_sessions import save_json
from app.models.auth import AccountProfile


logger = logging.getLogger(__name__)
ATTEMPT_SECONDS = 600
POPUP_WIDTH = 520
POPUP_HEIGHT = 720
SILENT_SECONDS = 20  # how long a site may take to sign in by itself before the popup comes back
SITE_SECONDS = 300   # total time allowed per site, including the student's part

# USC's direct Handshake sign-in link: it skips Handshake's "choose your school" page.
HANDSHAKE_SIGN_IN = "https://usc.joinhandshake.com/auth/saml/70/session/new?redirect_to_idp=true"

# Same check as the connector helper: the page's own session can read the current user.
HANDSHAKE_SIGNED_IN = """async () => {
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
}"""

Position = tuple[int, int] | None  # screen position of the popup's top-left corner
Identify = Callable[[dict], AccountProfile]  # Brightspace whoami -> TrojanClaw account
Ready = Callable[[], None]                    # the page may sign the account in now


def handshake_origin(url: str) -> str | None:
    """https://<school>.joinhandshake.com for a Handshake page, else None."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not host.endswith(".joinhandshake.com") or parsed.port not in (None, 443):
        return None
    return f"https://{host}"


def _wait(check: Callable[[], bool], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(1)
    return False


def _show_popup(page, visible: bool) -> None:
    """Minimize the popup during setup, or bring it back when a site needs the student."""
    try:
        cdp = page.context.new_cdp_session(page)
        window = cdp.send("Browser.getWindowForTarget")["windowId"]
        cdp.send("Browser.setWindowBounds", {"windowId": window,
                                             "bounds": {"windowState": "normal" if visible else "minimized"}})
        if visible:
            page.bring_to_front()
    except Exception:  # cosmetic only; setup continues with the window as it is
        logger.warning("Could not change the USC sign-in window", exc_info=True)


def connect_handshake(page, user_id: UUID) -> None:
    connectors = get_local_connectors()
    page.goto(HANDSHAKE_SIGN_IN, wait_until="domcontentloaded")

    def signed_in() -> bool:
        if not handshake_origin(page.url):
            return False
        try:
            return bool(page.evaluate(HANDSHAKE_SIGNED_IN))
        except Exception:  # redirects are expected while signing in
            return False

    if not _wait(signed_in, SILENT_SECONDS):
        logger.info("USC setup: Handshake needs the student, showing the popup")
        connectors.set_state(user_id, "handshake", IntegrationState.connecting, "Finish the USC sign-in in the pop-up.")
        _show_popup(page, visible=True)
        if not _wait(signed_in, SITE_SECONDS - SILENT_SECONDS):
            raise TimeoutError("Handshake sign-in was not finished in time.")
        _show_popup(page, visible=False)

    origin = handshake_origin(page.url)
    save_json(user_id, "handshake", "cookies.json", page.context.cookies([origin]))
    save_json(user_id, "handshake", "origin.json", {"origin": origin})
    connectors.check_session(user_id, "handshake")  # read-only check through the connector; marks it connected
    logger.info("USC setup: Handshake connected")


# Sites connected automatically after Sign in with USC, in order.
SETUP: dict[str, Callable] = {"handshake": connect_handshake}


def browser_login(position: Position, identify: Identify, ready: Ready) -> None:
    """Run the USC sign-in popup, find the account, then set up that account's connectors."""
    from playwright.sync_api import sync_playwright

    from app.connectors.usc_login import read_whoami, wait_for_brightspace

    # A popup-sized window with its address bar, so the student can check it is the real USC login page.
    args = [f"--window-size={POPUP_WIDTH},{POPUP_HEIGHT}"]
    if position:
        args.append(f"--window-position={position[0]},{position[1]}")
    connectors = get_local_connectors()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=False, args=args)
        try:
            # An in-memory context: the USC login session disappears when the browser closes.
            context = browser.new_context(no_viewport=True)
            page = context.new_page()
            if wait_for_brightspace(context, page) is None:
                raise TimeoutError("The USC login was not finished in time.")
            logger.info("USC login: Brightspace login detected, asking who logged in")
            account = identify(read_whoami(page))
            # Sessions are saved in this account's own folder, so no other account can use them.
            save_json(account.id, "brightspace", "cookies.json", context.cookies(["https://brightspace.usc.edu"]))
            connectors.set_state(account.id, "brightspace", IntegrationState.connected)
            for provider in SETUP:  # before ready(), so "Setting things up" sees them at once
                connectors.set_state(account.id, provider, IntegrationState.connecting, "Setting up…")
            ready()

            _show_popup(page, visible=False)
            for provider, connect in SETUP.items():
                try:
                    connect(page, account.id)
                except Exception:
                    logger.exception("USC setup: %s could not be connected", provider)
                    connectors.set_state(account.id, provider, IntegrationState.needs_login,
                                         "Could not connect automatically. Click Connect to try again.")
        finally:
            browser.close()


@dataclass
class UscAttempt:
    status: str = "waiting"  # waiting | failed | done
    account: AccountProfile | None = None
    started: float = field(default_factory=time.monotonic)


def find_usc_account(whoami: dict) -> AccountProfile:
    from app.services.auth import get_auth_service

    return get_auth_service().find_or_create_usc_account(whoami)


class UscLogin:
    """Tracks sign-in popups. Each attempt ID is a secret that only the starting page knows."""

    def __init__(self, run_login: Callable[[Position, Identify, Ready], None] = browser_login,
                 find_account: Identify = find_usc_account):
        self._run_login = run_login
        self._find_account = find_account
        self._attempts: dict[str, UscAttempt] = {}
        self._lock = threading.Lock()

    def start(self, position: Position = None) -> str:
        with self._lock:
            now = time.monotonic()
            for attempt_id, attempt in list(self._attempts.items()):
                if now - attempt.started > ATTEMPT_SECONDS:
                    del self._attempts[attempt_id]
            if any(attempt.status == "waiting" for attempt in self._attempts.values()):
                raise HTTPException(409, "A USC login window is already open. Finish it first.")
            attempt_id = secrets.token_urlsafe(32)
            attempt = UscAttempt()
            self._attempts[attempt_id] = attempt
        threading.Thread(target=self._run, args=(attempt, position), daemon=True).start()
        return attempt_id

    def _run(self, attempt: UscAttempt, position: Position) -> None:
        def identify(whoami: dict) -> AccountProfile:
            attempt.account = self._find_account(whoami)
            return attempt.account

        def ready() -> None:
            attempt.status = "done"
            logger.info("USC login: finished, signing in %s", attempt.account.username if attempt.account else None)

        try:
            self._run_login(position, identify, ready)
        except Exception:
            logger.exception("USC login failed")
            if attempt.status == "waiting":  # a failure during setup does not undo the sign-in
                attempt.status = "failed"

    def take(self, attempt_id: str) -> UscAttempt:
        """Current state of an attempt. A finished attempt can be read only once."""
        with self._lock:
            attempt = self._attempts.get(attempt_id)
            if attempt is None:
                raise HTTPException(404, "This USC login has expired. Please start again.")
            if attempt.status != "waiting":
                del self._attempts[attempt_id]
                logger.info("USC login: page picked up the result (%s)", attempt.status)
            return attempt


usc_login = UscLogin()
