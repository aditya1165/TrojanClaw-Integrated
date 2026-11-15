"""Browser sessions (HttpOnly cookie): who is signed in, and signing out.

Signing in is Continue with Google (routes/google_auth.py), which then runs the USC login
to connect Brightspace, Handshake and WebReg and to find the student's account."""

from collections import OrderedDict, deque
from threading import Lock
from time import monotonic
from hashlib import sha256

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.config import get_settings
from app.database import get_database
from app.models.auth import AccountProfile
from app.services.auth import AuthService, COOKIE_NAME, SESSION_SECONDS, get_auth_service


router = APIRouter(prefix="/auth", tags=["auth"])


def require_trusted_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    settings = get_settings()
    if origin and origin not in settings.cors_origins:
        raise HTTPException(403, "Request origin is not allowed.")
    if (not origin and getattr(settings, "app_env", "development") in {"production", "staging"}
            and request.method not in {"GET", "HEAD", "OPTIONS"}
            and not request.headers.get("authorization", "").startswith("Bearer ")):
        raise HTTPException(403, "A trusted request origin is required.")


class AuthRateLimiter:
    """Bound authentication work; use an atomic shared counter when hosted."""

    def __init__(self):
        self.attempts: OrderedDict[str, deque[float]] = OrderedDict()
        self.lock = Lock()

    def check(self, request: Request) -> None:
        key = request.client.host if request.client else "unknown"
        self.check_key(key)

    def check_key(self, key: str) -> None:
        """Apply the same bounded limiter to an authenticated account when needed."""
        if get_settings().app_env in {"production", "staging"}:
            with get_database().connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO public.auth_rate_limits (client_hash, attempts) VALUES (%s, 1) "
                    "ON CONFLICT (client_hash) DO UPDATE SET "
                    "attempts = CASE WHEN auth_rate_limits.window_started_at <= now() - interval '5 minutes' "
                    "THEN 1 ELSE auth_rate_limits.attempts + 1 END, "
                    "window_started_at = CASE WHEN auth_rate_limits.window_started_at <= now() - interval '5 minutes' "
                    "THEN now() ELSE auth_rate_limits.window_started_at END RETURNING attempts",
                    (sha256(key.encode()).hexdigest(),),
                )
                attempts = cursor.fetchone()[0]
            if attempts > 20:
                raise HTTPException(429, "Too many attempts. Try again in five minutes.", headers={"Retry-After": "300"})
            return
        now = monotonic()
        with self.lock:
            attempts = self.attempts.setdefault(key, deque())
            self.attempts.move_to_end(key)
            while attempts and attempts[0] <= now - 300:
                attempts.popleft()
            if len(attempts) >= 20:
                raise HTTPException(429, "Too many attempts. Try again in five minutes.", headers={"Retry-After": "300"})
            attempts.append(now)
            while len(self.attempts) > 10000:
                self.attempts.popitem(last=False)


rate_limiter = AuthRateLimiter()


def limit_auth_attempts(request: Request) -> None:
    rate_limiter.check(request)


def get_current_user(request: Request, service: AuthService = Depends(get_auth_service)) -> AccountProfile:
    return service.current_user(request.cookies.get(COOKIE_NAME))


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME, token, max_age=SESSION_SECONDS, httponly=True,
        secure=get_settings().app_env not in {"development", "test"},
        samesite="lax", path="/",
    )
    response.headers["Cache-Control"] = "no-store"


@router.get("/me", response_model=AccountProfile)
def me(response: Response, user: AccountProfile = Depends(get_current_user)) -> AccountProfile:
    response.headers["Cache-Control"] = "no-store"
    return user


@router.post("/logout", status_code=204, dependencies=[Depends(require_trusted_origin)])
def logout(request: Request, response: Response, service: AuthService = Depends(get_auth_service)) -> None:
    service.logout(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax",
                           secure=get_settings().app_env not in {"development", "test"})
    response.headers["Cache-Control"] = "no-store"
