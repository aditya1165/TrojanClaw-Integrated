"""Username/password authentication using HttpOnly session cookies."""

from collections import OrderedDict, deque
from threading import Lock
from time import monotonic

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.config import get_settings
from app.models.auth import (
    AccountProfile, LoginRequest, RegisterRequest, UscLoginPoll, UscLoginStart, UscLoginStartRequest,
    UscLoginStatus,
)
from app.services.auth import AuthService, COOKIE_NAME, SESSION_SECONDS, get_auth_service
from app.services.usc_auth import usc_login


router = APIRouter(prefix="/auth", tags=["auth"])


def require_trusted_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin not in get_settings().cors_origins:
        raise HTTPException(403, "Request origin is not allowed.")


class AuthRateLimiter:
    """Bound memory and password-hashing work. Apply per worker and client IP."""

    def __init__(self):
        self.attempts: OrderedDict[str, deque[float]] = OrderedDict()
        self.lock = Lock()

    def check(self, request: Request) -> None:
        key = request.client.host if request.client else "unknown"
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


@router.post("/register", response_model=AccountProfile, status_code=201,
             dependencies=[Depends(require_trusted_origin), Depends(limit_auth_attempts)])
def register(payload: RegisterRequest, request: Request, response: Response,
             service: AuthService = Depends(get_auth_service)) -> AccountProfile:
    profile, token = service.register(payload, request.cookies.get(COOKIE_NAME))
    _set_cookie(response, token)
    return profile


@router.post("/login", response_model=AccountProfile,
             dependencies=[Depends(require_trusted_origin), Depends(limit_auth_attempts)])
def login(payload: LoginRequest, request: Request, response: Response,
          service: AuthService = Depends(get_auth_service)) -> AccountProfile:
    profile, token = service.login(payload.username, payload.password.get_secret_value(), request.cookies.get(COOKIE_NAME))
    _set_cookie(response, token)
    return profile


@router.post("/usc/start", response_model=UscLoginStart,
             dependencies=[Depends(require_trusted_origin), Depends(limit_auth_attempts)])
def usc_start(response: Response, payload: UscLoginStartRequest | None = None) -> UscLoginStart:
    """Open the USC sign-in popup on this computer. Poll /auth/usc/status with the returned ID."""
    response.headers["Cache-Control"] = "no-store"
    position = (payload.left, payload.top) if payload and payload.left is not None and payload.top is not None else None
    return UscLoginStart(attempt_id=usc_login.start(position))


@router.post("/usc/status", response_model=UscLoginStatus, dependencies=[Depends(require_trusted_origin)])
def usc_status(payload: UscLoginPoll, request: Request, response: Response,
               service: AuthService = Depends(get_auth_service)) -> UscLoginStatus:
    response.headers["Cache-Control"] = "no-store"
    attempt = usc_login.take(payload.attempt_id)
    if attempt.status == "waiting":
        return UscLoginStatus(status="waiting")
    if attempt.status == "failed":
        return UscLoginStatus(status="failed", detail="The USC login did not finish. Please try again.")
    profile, token = service.usc_sign_in(attempt.whoami or {}, request.cookies.get(COOKIE_NAME))
    _set_cookie(response, token)
    return UscLoginStatus(status="done", account=profile)


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
