"""Google-first USC onboarding. No calendar writes occur during sign-in."""

import logging

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
import psycopg2

from app.api.routes.auth import _set_cookie, limit_auth_attempts, require_trusted_origin
from app.api.routes.integrations import require_local_request
from app.config import ConfigurationError, get_settings
from app.errors import AppError
from app.models.auth import UscLoginPoll, UscLoginStart, UscLoginStartRequest, UscLoginStatus
from app.models.calendar import CalendarConnect
from app.services.auth import AuthService, COOKIE_NAME, get_auth_service
from app.services.calendar import GoogleCalendar, get_calendar_service
from app.services.google_auth import GOOGLE_ATTEMPT_COOKIE, google_signin


router = APIRouter(prefix="/auth/google", tags=["auth"])
logger = logging.getLogger(__name__)


@router.get("/config")
def config(response: Response, calendar: GoogleCalendar = Depends(get_calendar_service)) -> dict[str, bool]:
    response.headers["Cache-Control"] = "no-store"
    return {"configured": calendar.configured}


@router.post("/start", response_model=CalendarConnect,
             dependencies=[Depends(require_trusted_origin), Depends(require_local_request), Depends(limit_auth_attempts)])
def start(request: Request, response: Response):
    token, url = google_signin.start(request.cookies.get(GOOGLE_ATTEMPT_COOKIE))
    response.set_cookie(GOOGLE_ATTEMPT_COOKIE, token, max_age=1200, httponly=True,
                        secure=get_settings().app_env not in {"development", "test"}, samesite="lax", path="/auth/google")
    response.headers["Cache-Control"] = "no-store"
    return CalendarConnect(url=url)


@router.get("/callback")
def callback(request: Request, state: str = Query(default="", max_length=200),
             code: str | None = Query(default=None, max_length=4096), error: str | None = Query(default=None, max_length=200)):
    outcome = "ready"
    try:
        google_signin.callback(request.cookies.get(GOOGLE_ATTEMPT_COOKIE), state, code, error)
    except (AppError, ConfigurationError):
        outcome = "error"
    return RedirectResponse(get_settings().google_calendar_frontend_url + "?google_signin=" + outcome,
                            status_code=303, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.post("/finish", response_model=UscLoginStart,
             dependencies=[Depends(require_trusted_origin), Depends(require_local_request)])
def finish(request: Request, response: Response, payload: UscLoginStartRequest | None = None):
    response.headers["Cache-Control"] = "no-store"
    position = (payload.left, payload.top) if payload and payload.left is not None and payload.top is not None else None
    attempt_id = google_signin.begin_usc(request.cookies.get(GOOGLE_ATTEMPT_COOKIE), position)
    return UscLoginStart(attempt_id=attempt_id)


@router.post("/status", response_model=UscLoginStatus,
             dependencies=[Depends(require_trusted_origin), Depends(require_local_request)])
def status(request: Request, response: Response, payload: UscLoginPoll,
           auth: AuthService = Depends(get_auth_service), calendar: GoogleCalendar = Depends(get_calendar_service)):
    response.headers["Cache-Control"] = "no-store"
    usc, tokens = google_signin.poll(request.cookies.get(GOOGLE_ATTEMPT_COOKIE), payload.attempt_id)
    if usc.status == "waiting":
        return UscLoginStatus(status="waiting")
    response.delete_cookie(GOOGLE_ATTEMPT_COOKIE, path="/auth/google")
    if usc.status == "failed":
        return UscLoginStatus(status="failed", detail=usc.detail or "The USC connection did not finish. Please retry using your USC account.")
    profile = usc.account
    session = auth.start_session(profile.id, request.cookies.get(COOKIE_NAME))
    _set_cookie(response, session)
    detail = None
    try:
        calendar.complete_connection(profile.id, tokens)
    except AppError as exc:
        logger.warning("Google sign-in finished; Calendar connection failed (%s)", exc.code)
        detail = "Signed in and connected Brightspace. Calendar: " + exc.message
    except (ConfigurationError, psycopg2.Error):
        logger.warning("Google sign-in finished; Calendar MCP connection needs setup")
        detail = "Signed in and connected Brightspace. Google Calendar could not connect yet. Check that Google Calendar API is enabled, then connect it from Sources."
    return UscLoginStatus(status="done", account=profile, detail=detail)


@router.post("/cancel", status_code=204, dependencies=[Depends(require_trusted_origin)])
def cancel(request: Request, response: Response):
    google_signin.cancel(request.cookies.get(GOOGLE_ATTEMPT_COOKIE))
    response.delete_cookie(GOOGLE_ATTEMPT_COOKIE, path="/auth/google")
    response.headers["Cache-Control"] = "no-store"
