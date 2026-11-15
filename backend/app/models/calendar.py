"""Calendar drafts contain only fields the user can review before approval."""

from datetime import datetime, date
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class CalendarRecurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frequency: Literal["DAILY", "WEEKLY", "MONTHLY"]
    interval: int = Field(default=1, ge=1, le=12)
    weekdays: list[Literal["MO", "TU", "WE", "TH", "FR", "SA", "SU"]] = Field(default_factory=list, max_length=7)
    count: int | None = Field(default=None, ge=1, le=52)
    until: date | None = None

    @model_validator(mode="after")
    def bounded(self):
        if (self.count is None) == (self.until is None):
            raise ValueError("Choose exactly one recurrence end: count or until date.")
        if len(set(self.weekdays)) != len(self.weekdays) or (self.weekdays and self.frequency != "WEEKLY"):
            raise ValueError("Unique weekdays are supported for weekly recurrence only.")
        return self


class CalendarEventDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    start: AwareDatetime
    end: AwareDatetime
    timezone: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=4000)
    location: str = Field(default="", max_length=500)
    recurrence: CalendarRecurrence | None = None
    reminder_minutes: int | None = Field(default=None, ge=0, le=40320)

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
    operation: Literal["create", "update"] = "create"
    before: dict | None = None
    scope: Literal["event", "series"] = "event"
    occurrences: list[dict] = Field(default_factory=list)
    recurrence_changed: bool = False
    reminder_changed: bool = False


class CalendarEventChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    timezone: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=4000)
    location: str | None = Field(default=None, max_length=500)
    recurrence: CalendarRecurrence | None = None
    reminder_minutes: int | None = Field(default=None, ge=0, le=40320)


class CalendarUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,1024}$")
    scope: Literal["event", "series"] = "event"
    changes: CalendarEventChanges


class CalendarEventQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None

    @model_validator(mode="after")
    def bounded_range(self):
        if (self.start is None) != (self.end is None):
            raise ValueError("Provide both start and end, or neither for the next seven days.")
        if self.start is not None and not 0 < (self.end - self.start).total_seconds() <= 31 * 86400:
            raise ValueError("Choose a calendar range of up to 31 days, with end after start.")
        return self


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
