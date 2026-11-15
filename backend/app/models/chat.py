"""Chat API models."""

from enum import Enum
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
    connectors: list[str] = Field(default_factory=list, max_length=2)
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
        if any(provider not in {"brightspace", "handshake"} for provider in value):
            raise ValueError("Only Brightspace and Handshake are available.")
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def require_latest_user_message(self) -> "ChatRequest":
        if self.messages[-1].role is not ChatRole.user:
            raise ValueError("The final message must have role 'user'.")
        return self


class ChatResponse(BaseModel):
    response: str
    calendar_proposals: list[CalendarProposal] = Field(default_factory=list)
