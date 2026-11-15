"""Local Google-first onboarding followed by a verified USC connector login.

Google OAuth runs in the user's ordinary browser. The existing USC helper then
establishes Brightspace's session; Google cookies are never inspected or copied.
Like UscLogin, pending attempts are short-lived and belong to this local worker.
"""

from dataclasses import dataclass, field
import hmac
import secrets
from threading import RLock
import time

from app.errors import AppError
from app.services.auth import token_hash
from app.services.calendar import get_calendar_service
from app.services.usc_auth import usc_login, verify_brightspace_identity


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


class GoogleSignIn:
    def __init__(self, calendar_factory=get_calendar_service, usc=usc_login):
        self.calendar_factory, self.usc = calendar_factory, usc
        self.attempts = {}
        self.lock = RLock()

    def _get(self, token):
        if not token or len(token) > 128:
            raise AppError("This Google sign-in expired. Please start again.", "google_signin_expired", 400)
        key = token_hash(token)
        attempt = self.attempts.get(key)
        if attempt is None or time.monotonic() - attempt.started > ATTEMPT_SECONDS:
            self.attempts.pop(key, None)
            raise AppError("This Google sign-in expired. Please start again.", "google_signin_expired", 400)
        return attempt

    def start(self, old_token=None):
        calendar = self.calendar_factory()
        state, verifier, token = secrets.token_urlsafe(32), secrets.token_urlsafe(64), secrets.token_urlsafe(32)
        url = calendar.authorization_url(state, verifier, redirect_uri=calendar.settings.google_signin_redirect_uri, signin=True)
        with self.lock:
            now = time.monotonic()
            self.attempts = {key: value for key, value in self.attempts.items() if now - value.started <= ATTEMPT_SECONDS}
            if old_token:
                self.attempts.pop(token_hash(old_token), None)
            if len(self.attempts) >= 100:
                raise AppError("Too many sign-in attempts. Please try again shortly.", "google_signin_busy", 429)
            self.attempts[token_hash(token)] = GoogleAttempt(token_hash(state), verifier)
        return token, url

    def callback(self, token, state, code, error=None):
        with self.lock:
            attempt = self._get(token)
            if attempt.status != "google" or not hmac.compare_digest(attempt.state_hash, token_hash(state)):
                raise AppError("The Google sign-in response did not match this browser. Please start again.", "google_signin_state", 400)
            attempt.status = "exchanging"
        try:
            if error or not code:
                raise AppError("Google sign-in was cancelled. You can try again or sign in with USC.", "google_signin_cancelled", 400)
            calendar = self.calendar_factory()
            tokens = calendar.exchange(code, attempt.verifier, redirect_uri=calendar.settings.google_signin_redirect_uri)
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
            if attempt.status == "failed":
                raise AppError(attempt.detail, "google_signin_failed", 400)
            if attempt.status == "usc":
                return attempt.usc_attempt_id
            if attempt.status != "ready":
                raise AppError("Finish Google sign-in before connecting Brightspace.", "google_signin_not_ready", 409)
            attempt.usc_attempt_id = self.usc.start(position, expected_email=attempt.email)
            attempt.status = "usc"
            return attempt.usc_attempt_id

    def poll(self, token, attempt_id):
        with self.lock:
            attempt = self._get(token)
            if attempt.status != "usc" or not hmac.compare_digest(attempt.usc_attempt_id or "", attempt_id):
                raise AppError("This sign-in attempt does not belong to this browser.", "google_signin_attempt", 403)
            usc = self.usc.take(attempt_id)
            if usc.status == "waiting":
                return usc, None
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
                self.attempts.pop(token_hash(token), None)


google_signin = GoogleSignIn()
