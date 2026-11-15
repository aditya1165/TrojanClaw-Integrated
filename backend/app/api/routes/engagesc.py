from fastapi import APIRouter, Depends, Query

from app.api.routes.auth import get_current_user, require_trusted_origin
from app.models.auth import AccountProfile
from app.models.events import CampusEventList, EngageSCStatus
from app.services.engagesc import EngageSCService, get_engagesc_service


router = APIRouter(prefix="/integrations/engagesc", tags=["engagesc"])


@router.get("", response_model=EngageSCStatus)
def status(user: AccountProfile = Depends(get_current_user), service: EngageSCService = Depends(get_engagesc_service)) -> EngageSCStatus:
    return EngageSCStatus(connected=service.repository.is_linked(user.id))


@router.post("/connect", response_model=EngageSCStatus, dependencies=[Depends(require_trusted_origin)])
def connect(user: AccountProfile = Depends(get_current_user), service: EngageSCService = Depends(get_engagesc_service)) -> EngageSCStatus:
    service.connect(user.id)
    return EngageSCStatus(connected=True)


@router.post("/disconnect", response_model=EngageSCStatus, dependencies=[Depends(require_trusted_origin)])
def disconnect(user: AccountProfile = Depends(get_current_user), service: EngageSCService = Depends(get_engagesc_service)) -> EngageSCStatus:
    service.repository.set_linked(user.id, False)
    return EngageSCStatus(connected=False)


@router.get("/events", response_model=CampusEventList)
def events(days: int = Query(30, ge=1, le=90), search: str = Query("", max_length=100), limit: int = Query(30, ge=1, le=100),
           user: AccountProfile = Depends(get_current_user), service: EngageSCService = Depends(get_engagesc_service)) -> CampusEventList:
    return service.events(user.id, days, search.strip(), limit)
