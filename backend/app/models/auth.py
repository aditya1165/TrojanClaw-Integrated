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


class UscLoginPoll(BaseModel):
    attempt_id: str = Field(min_length=1, max_length=64)


class UscLoginStatus(BaseModel):
    status: Literal["waiting", "failed", "done"]
    detail: str | None = None
    account: AccountProfile | None = None
