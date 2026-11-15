"""Authenticated calendar endpoints; only /approve can write Google events."""

from uuid import UUID
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse

from app.api.routes.auth import get_current_user, require_trusted_origin
from app.models.auth import AccountProfile
from app.models.calendar import CalendarConnect, CalendarProposal, CalendarStatus, EditProposal, ProposalVersion
from app.services.auth import COOKIE_NAME
from app.services.calendar import GoogleCalendar, get_calendar_service
from app.errors import AppError


def no_cache(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


router = APIRouter(prefix="/calendar", tags=["calendar"], dependencies=[Depends(no_cache)])


@router.get("/status", response_model=CalendarStatus)
def status(user: AccountProfile = Depends(get_current_user), service: GoogleCalendar = Depends(get_calendar_service)):
    return service.status(user.id)


@router.post("/connect", response_model=CalendarConnect, dependencies=[Depends(require_trusted_origin)])
def connect(request: Request, user: AccountProfile = Depends(get_current_user), service: GoogleCalendar = Depends(get_calendar_service)):
    return CalendarConnect(url=service.start(user.id, request.cookies.get(COOKIE_NAME, "")))


@router.get("/callback")
def callback(request: Request, state: str = Query(min_length=1, max_length=200),
             code: str | None = Query(default=None, max_length=4096), error: str | None = Query(default=None, max_length=200),
             user: AccountProfile = Depends(get_current_user), service: GoogleCalendar = Depends(get_calendar_service)):
    destination = service.settings.google_calendar_frontend_url
    try:
        service.callback(user.id, request.cookies.get(COOKIE_NAME, ""), state, code, error)
    except AppError as exc:
        destination += "?" + urlencode({"calendar_error": exc.message})
    return RedirectResponse(destination, status_code=303,
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.post("/disconnect", status_code=204, dependencies=[Depends(require_trusted_origin)])
def disconnect(user: AccountProfile = Depends(get_current_user), service: GoogleCalendar = Depends(get_calendar_service)):
    service.repository.disconnect(user.id)


@router.get("/proposals", response_model=list[CalendarProposal])
def proposals(user: AccountProfile = Depends(get_current_user), service: GoogleCalendar = Depends(get_calendar_service)):
    return service.proposals(user.id)


@router.post("/proposals/{proposal_id}/edit", response_model=CalendarProposal, dependencies=[Depends(require_trusted_origin)])
def edit(proposal_id: UUID, payload: EditProposal, user: AccountProfile = Depends(get_current_user),
         service: GoogleCalendar = Depends(get_calendar_service)):
    return service.edit(user.id, proposal_id, payload.version, payload.event)


@router.post("/proposals/{proposal_id}/cancel", response_model=CalendarProposal, dependencies=[Depends(require_trusted_origin)])
def cancel(proposal_id: UUID, payload: ProposalVersion, user: AccountProfile = Depends(get_current_user),
           service: GoogleCalendar = Depends(get_calendar_service)):
    return service.cancel(user.id, proposal_id, payload.version)


@router.post("/proposals/{proposal_id}/approve", response_model=CalendarProposal, dependencies=[Depends(require_trusted_origin)])
def approve(proposal_id: UUID, payload: ProposalVersion, user: AccountProfile = Depends(get_current_user),
            service: GoogleCalendar = Depends(get_calendar_service)):
    return service.approve(user.id, proposal_id, payload.version)
