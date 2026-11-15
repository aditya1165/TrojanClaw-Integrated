"""Read access to the student's synced academic data (Brightspace, Piazza).

Every query is scoped to one user_id. Course filters accept a course code such as
"CSCI 570" or "csci570" (spaces and case are ignored).
"""

from typing import Any
from uuid import UUID

from app.database import Database

_COURSE_MATCH = "regexp_replace(lower(c.code), '\\s', '', 'g') LIKE %s"


def _course_pattern(course: str | None) -> str | None:
    if not course:
        return None
    return "%" + "".join(course.lower().split()) + "%"


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


class StudentDataRepository:
    def __init__(self, database: Database):
        self._database = database

    def _query(self, sql: str, parameters: list[object]) -> list[dict[str, Any]]:
        with self._database.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, parameters)
                columns = [column[0] for column in cursor.description]
                return [
                    {name: _iso(value) for name, value in zip(columns, row)}
                    for row in cursor.fetchall()
                ]

    def list_deadlines(
        self, user_id: UUID, days_ahead: int = 14, course: str | None = None
    ) -> list[dict[str, Any]]:
        sql = f"""
            SELECT d.title, d.kind, d.course_code AS course, d.due_at,
                   d.submission_status, d.points_possible, d.url
            FROM public.upcoming_deadlines d
            LEFT JOIN public.courses c ON c.id = d.course_id
            WHERE d.user_id = %s
              AND d.due_at <= now() + make_interval(days => %s)
              AND (%s::text IS NULL OR {_COURSE_MATCH})
            ORDER BY d.due_at NULLS LAST
            LIMIT 50
        """
        pattern = _course_pattern(course)
        return self._query(sql, [str(user_id), days_ahead, pattern, pattern])

    def list_announcements(
        self,
        user_id: UUID,
        days_back: int = 14,
        course: str | None = None,
        limit: int = 15,
    ) -> list[dict[str, Any]]:
        sql = f"""
            SELECT f.item_type AS type, c.code AS course, f.title, f.happened_at, f.is_pinned,
                   left(coalesce(a.body, p.body, ''), 1500) AS body,
                   coalesce(a.url, p.url) AS url,
                   p.post_number AS piazza_post_number,
                   ch.old_due_at, ch.new_due_at
            FROM public.activity_feed f
            LEFT JOIN public.courses c ON c.id = f.course_id
            LEFT JOIN public.announcements a
                   ON f.item_type = 'announcement' AND a.id = f.item_id
            LEFT JOIN public.piazza_posts p
                   ON f.item_type = 'instructor_note' AND p.id = f.item_id
            LEFT JOIN public.deliverable_due_changes ch
                   ON f.item_type = 'due_date_change' AND ch.id = f.item_id
            WHERE f.user_id = %s
              AND f.happened_at >= now() - make_interval(days => %s)
              AND (%s::text IS NULL OR {_COURSE_MATCH})
            ORDER BY f.is_pinned DESC, f.happened_at DESC NULLS LAST
            LIMIT %s
        """
        pattern = _course_pattern(course)
        return self._query(sql, [str(user_id), days_back, pattern, pattern, limit])

    def search_content(
        self,
        user_id: UUID,
        embedding: list[float],
        limit: int = 6,
        min_similarity: float = 0.0,
        course: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = f"""
            SELECT m.source_table AS kind, c.code AS course,
                   coalesce(dl.title, an.title, pp.subject, cd.title, ce.title, 'Office hours') AS title,
                   coalesce(dl.url, an.url, pp.url, cd.url) AS url,
                   pp.post_number AS piazza_post_number,
                   m.chunk_text AS text,
                   round(m.similarity::numeric, 3) AS similarity
            FROM public.match_content_chunks(%s::extensions.vector, %s::uuid, %s, %s) m
            LEFT JOIN public.courses c ON c.id = m.course_id
            LEFT JOIN public.deliverables dl ON m.source_table = 'deliverables' AND dl.id = m.source_id
            LEFT JOIN public.announcements an ON m.source_table = 'announcements' AND an.id = m.source_id
            LEFT JOIN public.piazza_posts pp ON m.source_table = 'piazza_posts' AND pp.id = m.source_id
            LEFT JOIN public.course_documents cd ON m.source_table = 'course_documents' AND cd.id = m.source_id
            LEFT JOIN public.course_events ce ON m.source_table = 'course_events' AND ce.id = m.source_id
            WHERE (%s::text IS NULL OR {_COURSE_MATCH})
            ORDER BY m.similarity DESC
            LIMIT %s
        """
        vector = "[" + ",".join(map(str, embedding)) + "]"
        pattern = _course_pattern(course)
        # Over-fetch so a course filter applied after the vector search still fills `limit`.
        fetch = limit * 4 if pattern else limit
        return self._query(
            sql, [vector, str(user_id), fetch, min_similarity, pattern, pattern, limit]
        )

    def data_status(self, user_id: UUID) -> dict[str, Any]:
        connections = self._query(
            """
            SELECT provider, status, last_synced_at, last_error
            FROM public.integration_connections
            WHERE user_id = %s
            ORDER BY provider
            """,
            [str(user_id)],
        )
        counts = self._query(
            """
            SELECT
                (SELECT count(*) FROM public.courses WHERE user_id = %s) AS courses,
                (SELECT count(*) FROM public.deliverables WHERE user_id = %s) AS deliverables,
                (SELECT count(*) FROM public.announcements WHERE user_id = %s) AS announcements,
                (SELECT count(*) FROM public.piazza_posts WHERE user_id = %s) AS piazza_posts,
                (SELECT count(*) FROM public.content_chunks WHERE user_id = %s) AS searchable_chunks,
                (SELECT count(*) FROM public.documents) AS shared_campus_chunks
            """,
            [str(user_id)] * 5,
        )
        return {"connections": connections, "counts": counts[0] if counts else {}}
