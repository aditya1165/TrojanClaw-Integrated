"""Google-first onboarding followed by a verified USC connector login.

Google OAuth runs in the user's ordinary browser. USC uses managed Chromium by
default; Google cookies are never inspected or copied. Pending Google attempts
remain short-lived and process-local; use one API worker/instance for onboarding.
"""

from dataclasses import dataclass, field
import hmac
import secrets
from threading import Lock, RLock
import time

from app.errors import AppError
from app.services.auth import token_hash
from app.services.calendar import get_calendar_service
from app.services.usc_auth import usc_login, verify_brightspace_identity
from app.config import get_settings
from app.models.auth import UscLoginStatus


GOOGLE_ATTEMPT_COOKIE = "trojanclaw_google_signin"
ATTEMPT_SECONDS = 600


@dataclass
class GoogleAttempt:
    state_hash: str
    verifier: str
    started: float = field(default_factory=time.monotonic)
    status: str = "google"
    email: str | None = None
    encrypted_tokens: str | None = None
    usc_attempt_id: str | None = None
    detail: str | None = None
    redirect_uri: str | None = None
    frontend_url: str | None = None
    remote_login: dict | None = None
    operation_lock: object = field(default_factory=Lock, repr=False)


class GoogleSignIn:
    def __init__(self, calendar_factory=get_calendar_service, usc=usc_login):
        self.calendar_factory, self.usc = calendar_factory, usc
        self.attempts = {}
        self.lock = RLock()

    def managed(self):
        return self.usc is usc_login and get_settings().connector_mode == "browserbase"

    def _get(self, token):
        if not token or len(token) > 128:
            raise AppError("This Google sign-in expired. Please start again.", "google_signin_expired", 400)
        key = token_hash(token)
        attempt = self.attempts.get(key)
        if attempt is None or time.monotonic() - attempt.started > ATTEMPT_SECONDS:
            self.attempts.pop(key, None)
            raise AppError("This Google sign-in expired. Please start again.", "google_signin_expired", 400)
        return attempt

    def start(self, old_token=None, *, redirect_uri=None, frontend_url=None):
        calendar = self.calendar_factory()
        state, verifier, token = secrets.token_urlsafe(32), secrets.token_urlsafe(64), secrets.token_urlsafe(32)
        redirect_uri = redirect_uri or calendar.settings.google_signin_redirect_uri
        url = calendar.authorization_url(state, verifier, redirect_uri=redirect_uri, signin=True)
        with self.lock:
            now = time.monotonic()
            self.attempts = {key: value for key, value in self.attempts.items() if now - value.started <= ATTEMPT_SECONDS}
            if old_token:
                self.attempts.pop(token_hash(old_token), None)
            if len(self.attempts) >= 100:
                raise AppError("Too many sign-in attempts. Please try again shortly.", "google_signin_busy", 429)
            self.attempts[token_hash(token)] = GoogleAttempt(token_hash(state), verifier,
                redirect_uri=redirect_uri, frontend_url=frontend_url)
        return token, url

    def frontend_destination(self, token):
        with self.lock:
            try:
                return self._get(token).frontend_url
            except AppError:
                return None

    def callback(self, token, state, code, error=None):
        with self.lock:
            attempt = self._get(token)
            if attempt.status != "google" or not hmac.compare_digest(attempt.state_hash, token_hash(state)):
                raise AppError("The Google sign-in response did not match this browser. Please start again.", "google_signin_state", 400)
            attempt.status = "exchanging"
        try:
            if error or not code:
                raise AppError("Google sign-in was cancelled. You can try again.", "google_signin_cancelled", 400)
            calendar = self.calendar_factory()
            tokens = calendar.exchange(code, attempt.verifier,
                redirect_uri=attempt.redirect_uri or calendar.settings.google_signin_redirect_uri)
            email = calendar.google_identity(tokens)
            encrypted = calendar.encrypt(tokens)
            with self.lock:
                attempt.email, attempt.encrypted_tokens = email, encrypted
                attempt.verifier = ""
                attempt.status = "ready"
                attempt.started = time.monotonic()
        except AppError as exc:
            with self.lock:
                attempt.status, attempt.detail, attempt.verifier = "failed", exc.message, ""
            raise
        except Exception:
            with self.lock:
                attempt.status, attempt.detail, attempt.verifier = "failed", "Google sign-in could not finish. Please retry.", ""
            raise

    def begin_usc(self, token, position=None):
        with self.lock:
            attempt = self._get(token)
        with attempt.operation_lock:
            if attempt.status == "failed":
                raise AppError(attempt.detail, "google_signin_failed", 400)
            if attempt.status == "usc":
                return attempt.remote_login or attempt.usc_attempt_id
            if attempt.status != "ready":
                raise AppError("Finish Google sign-in before connecting Brightspace.", "google_signin_not_ready", 409)
            if self.managed():
                from app.services.hosted_usc import get_usc_login
                attempt.remote_login = get_usc_login().start(token, expected_email=attempt.email)
                attempt.usc_attempt_id = attempt.remote_login["attempt_id"]
            else:
                attempt.usc_attempt_id = self.usc.start(position, expected_email=attempt.email)
            attempt.status = "usc"
            return attempt.remote_login or attempt.usc_attempt_id

    def poll(self, token, attempt_id, skip_handshake=False):
        with self.lock:
            attempt = self._get(token)
        with attempt.operation_lock:
            if attempt.status != "usc" or not hmac.compare_digest(attempt.usc_attempt_id or "", attempt_id):
                raise AppError("This sign-in attempt does not belong to this browser.", "google_signin_attempt", 403)
            if self.managed():
                from app.services.hosted_usc import get_usc_login
                from app.services.auth import get_auth_service
                from uuid import UUID
                result, _ = get_usc_login().finish(UUID(attempt_id), token, get_auth_service(), None,
                    skip_handshake, issue_session=False)
                usc = UscLoginStatus(**result)
            else:
                usc = self.usc.take(attempt_id)
            if usc.status not in {"failed", "done"}:
                return usc, None
            with self.lock:
                self.attempts.pop(token_hash(token), None)
            if usc.status == "done":
                # Repeat the identity check at the final account-linking boundary.
                verify_brightspace_identity(usc.whoami or {}, attempt.email)
                tokens = self.calendar_factory().decrypt(attempt.encrypted_tokens)
                return usc, tokens
            return usc, None

    def cancel(self, token):
        if token:
            with self.lock:
                attempt = self.attempts.get(token_hash(token))
            if attempt:
                with attempt.operation_lock:
                    if attempt.status == "usc" and self.managed():
                        from app.services.hosted_usc import get_usc_login
                        get_usc_login().cancel_current(token)
                    with self.lock:
                        self.attempts.pop(token_hash(token), None)
                    attempt.status = "cancelled"


google_signin = GoogleSignIn()
