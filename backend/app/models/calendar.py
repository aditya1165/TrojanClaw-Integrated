"""Calendar drafts contain only fields the user can review before approval."""

from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class CalendarEventDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    start: AwareDatetime
    end: AwareDatetime
    timezone: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=4000)
    location: str = Field(default="", max_length=500)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Use a valid time zone, such as America/Los_Angeles.") from exc
        return value

    @model_validator(mode="after")
    def valid_times(self):
        if self.end <= self.start:
            raise ValueError("The end must be after the start.")
        if (self.end - self.start).total_seconds() > 86400:
            raise ValueError("An event can be at most 24 hours long.")
        zone = ZoneInfo(self.timezone)
        for value in (self.start, self.end):
            if value.utcoffset() != value.astimezone(zone).utcoffset():
                raise ValueError("Event time offsets must match the selected time zone.")
        return self


class CalendarProposal(BaseModel):
    id: UUID
    version: int
    event: CalendarEventDraft
    calendar: str
    status: Literal["pending", "approved", "applied", "cancelled"]
    expires_at: datetime
    event_url: str | None = None


class ProposalVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class EditProposal(ProposalVersion):
    event: CalendarEventDraft


class CalendarStatus(BaseModel):
    configured: bool
    connected: bool
    calendar: str | None = None


class CalendarConnect(BaseModel):
    url: str
