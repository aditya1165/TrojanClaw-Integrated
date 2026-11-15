"""Account input and public profile contracts. Never return password hashes."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class AccountProfile(BaseModel):
    id: UUID
    name: str
    username: str


class UscLoginStartRequest(BaseModel):
    """Where to open the sign-in popup (screen pixels, top-left corner). Optional."""
    left: int | None = Field(default=None, ge=-20000, le=20000)
    top: int | None = Field(default=None, ge=-20000, le=20000)


class UscLoginStart(BaseModel):
    attempt_id: str
    live_url: str | None = None
    phase: Literal["brightspace", "handshake"] = "brightspace"


class UscLoginPoll(BaseModel):
    attempt_id: str = Field(min_length=1, max_length=64)
    skip_handshake: bool = False


class UscLoginStatus(BaseModel):
    status: Literal["waiting", "connecting", "interaction_required", "failed", "done"]
    detail: str | None = None
    account: AccountProfile | None = None
    live_url: str | None = None
    phase: Literal["brightspace", "handshake"] | None = None
    connectors: list[dict] = Field(default_factory=list)
    whoami: dict | None = Field(default=None, exclude=True)
