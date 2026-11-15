"""A short JSON answer for Siri Shortcuts, isolated from website/MCP credentials."""
import asyncio
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.routes.auth import AuthRateLimiter, get_current_user, limit_auth_attempts, require_trusted_origin
from app.dependencies import get_chat_service
from app.models.auth import AccountProfile
from app.models.chat import ChatMessage, ChatRequest, Source
from app.services.chat import ChatService
from app.services.siri import SiriAccess, get_siri_access, spoken_answer

router = APIRouter(prefix="/siri", tags=["siri"], dependencies=[Depends(require_trusted_origin)])
bearer = HTTPBearer(auto_error=False)
invalid_attempts = AuthRateLimiter()
chat_usage = AuthRateLimiter()  # Per account, per worker; never a shared proxy-IP quota.
CHAT_TIMEOUT = 90


class SiriQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=4000)
    timezone: str = Field(default="America/Los_Angeles", min_length=1, max_length=100)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Use an IANA time zone such as America/Los_Angeles.") from None
        return value


class SiriAnswer(BaseModel):
    answer: str
    sources: list[Source] = Field(default_factory=list)


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def get_siri_owner(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
                   access: SiriAccess = Depends(get_siri_access)) -> UUID:
    try:
        if not credentials or credentials.scheme.lower() != "bearer":
            raise HTTPException(401, "Add the Siri access key to the shortcut's Authorization header.", headers={"WWW-Authenticate": "Bearer"})
        owner = access.owner(credentials.credentials)
    except HTTPException:
        invalid_attempts.check(request)
        raise
    chat_usage.check_key(str(owner))
    return owner


@router.get("/status")
def status(response: Response, user: AccountProfile = Depends(get_current_user), access: SiriAccess = Depends(get_siri_access)):
    no_store(response)
    return access.status(user.id)


@router.post("/connect", dependencies=[Depends(limit_auth_attempts)])
def connect(response: Response, user: AccountProfile = Depends(get_current_user), access: SiriAccess = Depends(get_siri_access)):
    no_store(response)
    return access.create(user.id)


@router.post("/disconnect")
def disconnect(response: Response, user: AccountProfile = Depends(get_current_user), access: SiriAccess = Depends(get_siri_access)):
    no_store(response)
    access.disconnect(user.id)
    return {"disconnected": True}


@router.post("/chat", response_model=SiriAnswer)
async def chat(payload: SiriQuestion, response: Response, user_id: UUID = Depends(get_siri_owner),
               service: ChatService = Depends(get_chat_service)) -> SiriAnswer:
    no_store(response)
    request = ChatRequest(messages=[ChatMessage(role="user", content=payload.question)], timezone=payload.timezone)
    try:
        result = await asyncio.wait_for(service.chat(request, user_id=user_id,
            use_saved_connectors=True, allow_calendar_drafts=False), timeout=CHAT_TIMEOUT)
    except TimeoutError:
        raise HTTPException(504, "TrojanClaw took too long. Try a shorter, more specific question.") from None
    return SiriAnswer(answer=spoken_answer(result.response), sources=result.sources)
