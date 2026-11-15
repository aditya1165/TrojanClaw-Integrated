"""Account-bound Android pairing and a narrow, read-only course AppFunction."""

import asyncio

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.api.routes.auth import get_current_user, limit_auth_attempts, require_trusted_origin
from app.integrations.local import LocalConnectors, get_local_connectors
from app.models.auth import AccountProfile
from app.services.auth import AuthService, get_auth_service

router = APIRouter(prefix="/mobile", tags=["android"], dependencies=[Depends(require_trusted_origin)])


class PairingCode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: SecretStr = Field(min_length=40, max_length=40)


class SemesterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    semester: str = Field(default="current semester", min_length=1, max_length=80)


class SemesterResponse(BaseModel):
    requested_semester: str
    enrollment_data: str
    semester_filter_applied: bool = False
    source: str = "Brightspace"
    note: str = "Match the requested semester using the returned enrollment metadata. If term metadata is absent, do not guess. Enrollments are not proof of course completion."


@router.post("/pairing", dependencies=[Depends(limit_auth_attempts)])
def create_pairing(response: Response, user: AccountProfile = Depends(get_current_user),
                   auth: AuthService = Depends(get_auth_service)):
    response.headers["Cache-Control"] = "no-store"
    return {"code": auth.create_mobile_pairing(user.id), "expires_in": 120}


@router.post("/pairing/redeem", dependencies=[Depends(limit_auth_attempts)])
def redeem_pairing(payload: PairingCode, response: Response, auth: AuthService = Depends(get_auth_service)):
    response.headers["Cache-Control"] = "no-store"
    return {"session_token": auth.redeem_mobile_pairing(payload.code.get_secret_value())}


@router.post("/semester-courses", response_model=SemesterResponse)
async def semester_courses(payload: SemesterRequest, response: Response,
                           user: AccountProfile = Depends(get_current_user),
                           connectors: LocalConnectors = Depends(get_local_connectors)):
    response.headers["Cache-Control"] = "no-store"
    # A phone may read exactly this tool with its authenticated owner's session.
    # It cannot choose a provider, tool, user, arbitrary URL or output path.
    content = await asyncio.to_thread(connectors.call, user.id, "brightspace__get_my_courses", {}, ["brightspace"])
    return SemesterResponse(requested_semester=payload.semester, enrollment_data=content)
