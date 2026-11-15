"""Chat API models."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.models.calendar import CalendarProposal


class ChatRole(str, Enum):
    user = "user"
    assistant = "assistant"


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: ChatRole
    content: str = Field(min_length=1, max_length=20_000)
    # Claude content blocks sent before the text: attached files (PDF, image, document text).
    blocks: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Message content cannot be blank.")
        return normalized


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(min_length=1, max_length=100)
    connectors: list[str] = Field(default_factory=list, max_length=3)
    timezone: str = Field(default="America/Los_Angeles", max_length=100)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Use a valid time zone.") from exc
        return value

    @field_validator("connectors")
    @classmethod
    def supported_connectors(cls, value: list[str]) -> list[str]:
        if any(provider not in {"brightspace", "handshake", "piazza"} for provider in value):
            raise ValueError("Only Brightspace, Handshake and Piazza are available.")
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def require_latest_user_message(self) -> "ChatRequest":
        if self.messages[-1].role is not ChatRole.user:
            raise ValueError("The final message must have role 'user'.")
        return self


class Source(BaseModel):
    """Where part of an answer came from (a deadline, post, announcement or web page)."""

    title: str
    url: str | None = None
    course: str | None = None
    kind: str | None = None


class ChatResponse(BaseModel):
    response: str
    sources: list[Source] = Field(default_factory=list)
    # Calendar drafts made during this reply; each needs the student's Approve click.
    calendar_proposals: list[CalendarProposal] = Field(default_factory=list)
    # The saved chat this reply belongs to, and its title (set by POST /chat).
    conversation_id: UUID | None = None
    title: str | None = None


class ChatTurn(BaseModel):
    """What the browser sends: one new message. Earlier messages come from the saved chat."""
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID | None = None  # None starts a new chat
    message: str = Field(min_length=1, max_length=20_000)
    attachments: list[UUID] = Field(default_factory=list, max_length=5)  # IDs from POST /uploads
    connectors: list[str] = Field(default_factory=list, max_length=3)
    timezone: str = Field(default="America/Los_Angeles", max_length=100)

    @field_validator("message")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message content cannot be blank.")
        return value.strip()

    @field_validator("connectors")
    @classmethod
    def supported_connectors(cls, value: list[str]) -> list[str]:
        return ChatRequest.supported_connectors(value)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        return ChatRequest.valid_timezone(value)


class ConversationSummary(BaseModel):
    id: UUID
    title: str | None
    updated_at: datetime
    pinned: bool = False


class Attachment(BaseModel):
    id: UUID
    name: str
    mime: str
    size: int
    kind: str


class ConversationMessage(BaseModel):
    id: UUID
    role: ChatRole
    content: str
    created_at: datetime
    sources: list[Source] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)


class PinConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pinned: bool


class RenameConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)

    @field_validator("title")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Title cannot be blank.")
        return " ".join(value.split())
