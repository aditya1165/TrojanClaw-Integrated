"""Retrieved knowledge document model."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetrievedDocument:
    chunk_text: str
    source_url: str
    page_title: str
    data_type: str
    campus: str | None
    distance: float
