from datetime import datetime

from pydantic import BaseModel


class CampusEvent(BaseModel):
    external_key: str
    title: str
    host: str | None = None
    starts_at: datetime
    ends_at: datetime | None = None
    all_day: bool = False
    location: str | None = None
    url: str
    description: str = ""


class EngageSCStatus(BaseModel):
    connected: bool
    source_url: str = "https://engage.usc.edu/events"


class CampusEventList(BaseModel):
    events: list[CampusEvent]
    fetched_at: datetime
    stale: bool = False
    source_url: str = "https://engage.usc.edu/events"
