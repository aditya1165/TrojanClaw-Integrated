"""TrojanClaw MCP server.

A server is built for one signed-in student per agent run (see `build_mcp_server`), so
tools never take a user_id: the model cannot ask for another student's data. Course
tools read that student's rows; campus tools read shared public data.

Tools are async and run blocking database and embedding calls in a worker thread, so
one student's slow query does not stall other students' requests.

Every tool returns JSON with a `sources` list ({title, url, course, kind}) so the agent
runtime can show where an answer came from.
"""

import asyncio
from typing import Any, Protocol
from uuid import UUID

from fastapi import HTTPException
from mcp.server.mcpserver import MCPServer

from app.agent.config import RetrievalConfig
from app.errors import AppError
from app.repositories.documents import DocumentRepository
from app.repositories.student_data import StudentDataRepository
from app.services.embeddings import EmbeddingService

_NOT_SYNCED_HINT = (
    "No matching data. Call get_data_status to check whether this source has been synced."
)
_EVENTS_NOT_LINKED = (
    "The student has not turned on EngageSC public events. They can link it from the "
    "EngageSC panel in TrojanClaw."
)


class CampusEventsService(Protocol):
    """The parts of EngageSCService the tools use."""

    repository: Any

    def events(self, user_id: UUID, days: int, search: str, limit: int) -> Any: ...


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _source(title: Any, url: Any = None, course: Any = None, kind: Any = None) -> dict[str, Any]:
    return {"title": title, "url": url, "course": course, "kind": kind}


def build_mcp_server(
    user_id: UUID,
    embeddings: EmbeddingService,
    documents: DocumentRepository,
    student_data: StudentDataRepository,
    retrieval: RetrievalConfig,
    campus_events: CampusEventsService | None = None,
) -> MCPServer:
    server = MCPServer(
        "trojanclaw",
        instructions=(
            "Tools for the signed-in USC student's courses (Brightspace, Piazza) "
            "and for shared USC campus information and events."
        ),
    )
    run = asyncio.to_thread

    @server.tool()
    async def list_deadlines(days_ahead: int = 14, course: str | None = None) -> dict[str, Any]:
        """Assignments, quizzes and other graded items due in the next `days_ahead` days, soonest first.

        Args:
            days_ahead: How many days ahead to look (1-120). Default 14.
            course: Optional course code to filter by, e.g. "CSCI 570".
        """
        items = await run(student_data.list_deadlines, user_id, _clamp(days_ahead, 1, 120), course)
        return {
            "deadlines": items,
            "sources": [_source(i["title"], i.get("url"), i.get("course"), "deadline") for i in items],
            **({} if items else {"note": _NOT_SYNCED_HINT}),
        }

    @server.tool()
    async def list_announcements(
        days_back: int = 14, course: str | None = None, limit: int = 15
    ) -> dict[str, Any]:
        """Recent Brightspace announcements, Piazza instructor notes and detected due-date changes, pinned first.

        Args:
            days_back: How many days back to look (1-120). Default 14.
            course: Optional course code to filter by, e.g. "CSCI 570".
            limit: Maximum number of items (1-50). Default 15.
        """
        items = await run(
            student_data.list_announcements,
            user_id, _clamp(days_back, 1, 120), course, _clamp(limit, 1, 50),
        )
        return {
            "announcements": items,
            "sources": [_source(i["title"], i.get("url"), i.get("course"), i.get("type")) for i in items],
            **({} if items else {"note": _NOT_SYNCED_HINT}),
        }

    @server.tool()
    async def search_student_context(query: str, course: str | None = None, limit: int = 6) -> dict[str, Any]:
        """Semantic search over the student's course material: assignments, announcements,
        Piazza threads, syllabus and course content. Returns the most relevant passages.

        Args:
            query: What to look for, in natural language.
            course: Optional course code to filter by, e.g. "CSCI 570".
            limit: Maximum number of passages (1-12). Default 6.
        """
        vector = await run(embeddings.embed_query, query)
        items = await run(
            student_data.search_content,
            user_id, vector, _clamp(limit, 1, 12), retrieval.min_similarity, course,
        )
        return {
            "results": items,
            "sources": [_source(i["title"], i.get("url"), i.get("course"), i.get("kind")) for i in items],
            **({} if items else {"note": _NOT_SYNCED_HINT}),
        }

    @server.tool()
    async def search_campus_knowledge(query: str, limit: int = 6) -> dict[str, Any]:
        """Semantic search over public USC web pages: academic calendar, final exam schedule,
        orientation, campus services. Returns the most relevant passages with their page links.

        Args:
            query: What to look for, in natural language.
            limit: Maximum number of passages (1-12). Default 6.
        """
        vector = await run(embeddings.embed_query, query)
        docs = await run(
            lambda: documents.search(
                embedding=vector,
                limit=_clamp(limit, 1, 12),
                min_similarity=retrieval.min_similarity,
                per_source_limit=retrieval.per_source_limit,
            )
        )
        results = [
            {
                "title": d.page_title,
                "url": d.source_url,
                "type": d.data_type,
                "text": d.chunk_text,
                "similarity": round(1 - d.distance, 3),
            }
            for d in docs
        ]
        return {
            "results": results,
            "sources": [_source(r["title"], r["url"], None, "campus_page") for r in results],
            **({} if results else {"note": "No matching campus pages were found."}),
        }

    @server.tool()
    async def list_campus_events(days_ahead: int = 14, search: str = "", limit: int = 20) -> dict[str, Any]:
        """Upcoming public USC campus events from EngageSC (talks, workshops, club and career
        events), soonest first. Only available if the student has linked EngageSC.

        Args:
            days_ahead: How many days ahead to look (1-90). Default 14.
            search: Optional keyword matched against title, host and description, e.g. "robotics".
            limit: Maximum number of events (1-50). Default 20.
        """
        if campus_events is None:
            return {"events": [], "sources": [], "note": "Campus events are not available."}
        if not await run(campus_events.repository.is_linked, user_id):
            return {"events": [], "sources": [], "note": _EVENTS_NOT_LINKED}
        try:
            result = await run(
                campus_events.events,
                user_id, _clamp(days_ahead, 1, 90), search.strip()[:100], _clamp(limit, 1, 50),
            )
        except HTTPException:  # unlinked between the check and the query
            return {"events": [], "sources": [], "note": _EVENTS_NOT_LINKED}
        except AppError:
            return {"events": [], "sources": [], "note": "EngageSC is temporarily unavailable. Do not guess events."}
        events = [
            {
                "title": e.title,
                "host": e.host,
                "starts_at": e.starts_at.isoformat(),
                "ends_at": e.ends_at.isoformat() if e.ends_at else None,
                "all_day": e.all_day,
                "location": e.location,
                "url": e.url,
                "description": e.description[:600],
            }
            for e in result.events
        ]
        return {
            "events": events,
            "fetched_at": result.fetched_at.isoformat(),
            "stale": result.stale,
            "sources": [_source(e["title"], e["url"], None, "campus_event") for e in events],
            **({} if events else {"note": "No public EngageSC events match."}),
        }

    @server.tool()
    async def get_data_status() -> dict[str, Any]:
        """Which data sources (Brightspace, Piazza, EngageSC) are connected for this student,
        when each last synced, and how many items of each kind are stored. Use it to explain
        empty results."""
        return {**(await run(student_data.data_status, user_id)), "sources": []}

    return server
