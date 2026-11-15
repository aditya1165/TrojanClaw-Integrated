"""Saved chats. Every query is scoped to the signed-in user."""

import json
from uuid import UUID

from psycopg2.extras import RealDictCursor

from app.errors import AppError

# How many earlier messages are sent back to the model with each new message.
HISTORY_LIMIT = 40


SUMMARY = "id, title, updated_at, pinned_at IS NOT NULL AS pinned"


def _sources(content) -> list:
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "sources" and isinstance(block.get("items"), list):
                return block["items"]
    return []


def _text(content) -> str:
    """Messages are stored as Anthropic-style content blocks; return their text."""
    if isinstance(content, str):
        return content
    return "\n".join(block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text")


class ConversationRepository:
    def __init__(self, database):
        self.database = database

    def _query(self, sql, params=(), *, many=False):
        with self.database.connection() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(sql, params)
                if cursor.description:
                    return cursor.fetchall() if many else cursor.fetchone()

    def list(self, user_id: UUID, search: str = ""):
        """Pinned chats first, then newest. A search matches titles and message text."""
        where, params = "c.user_id = %s", [str(user_id)]
        if search:
            pattern = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where += (" AND (c.title ILIKE %s OR EXISTS (SELECT 1 FROM public.conversation_messages m"
                      " WHERE m.conversation_id = c.id AND m.content::text ILIKE %s))")
            params += [pattern, pattern]
        return self._query(
            f"SELECT c.id, c.title, c.updated_at, c.pinned_at IS NOT NULL AS pinned FROM public.conversations c WHERE {where} "
            "ORDER BY c.pinned_at DESC NULLS LAST, c.updated_at DESC LIMIT 200",
            tuple(params), many=True)

    def create(self, user_id: UUID, title: str):
        return self._query(
            f"INSERT INTO public.conversations (user_id, title) VALUES (%s, %s) RETURNING {SUMMARY}",
            (str(user_id), title))

    def get(self, user_id: UUID, conversation_id: UUID):
        row = self._query(
            f"SELECT {SUMMARY} FROM public.conversations WHERE user_id = %s AND id = %s",
            (str(user_id), str(conversation_id)))
        if row is None:
            raise AppError("This chat was not found. It may have been deleted.", "conversation_not_found", 404)
        return row

    def messages(self, user_id: UUID, conversation_id: UUID, limit: int | None = None):
        """Messages oldest first; with a limit, only the most recent ones."""
        self.get(user_id, conversation_id)
        rows = self._query(
            "SELECT id, role, content, created_at FROM public.conversation_messages WHERE conversation_id = %s "
            "ORDER BY created_at DESC, id DESC LIMIT %s",
            (str(conversation_id), limit or 1000), many=True)
        return [{**row, "content": _text(row["content"]), "sources": _sources(row["content"])} for row in reversed(rows)]

    def add_turn(self, user_id: UUID, conversation_id: UUID, question: str, answer: str, sources: "list[dict] | None" = None) -> None:
        """Save one question and its answer together, so history never has half a turn."""
        with self.database.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE public.conversations SET updated_at = now() WHERE user_id = %s AND id = %s",
                               (str(user_id), str(conversation_id)))
                if cursor.rowcount != 1:
                    raise AppError("This chat was not found. It may have been deleted.", "conversation_not_found", 404)
                answer_blocks = [{"type": "text", "text": answer}] + ([{"type": "sources", "items": sources}] if sources else [])
                for offset, (role, blocks) in enumerate((("user", [{"type": "text", "text": question}]), ("assistant", answer_blocks))):
                    cursor.execute(
                        "INSERT INTO public.conversation_messages (conversation_id, role, content, created_at) "
                        "VALUES (%s, %s, %s::jsonb, clock_timestamp() + %s * interval '1 microsecond')",
                        (str(conversation_id), role, json.dumps(blocks), offset))

    def rename(self, user_id: UUID, conversation_id: UUID, title: str):
        row = self._query(
            f"UPDATE public.conversations SET title = %s WHERE user_id = %s AND id = %s RETURNING {SUMMARY}",
            (title, str(user_id), str(conversation_id)))
        if row is None:
            raise AppError("This chat was not found. It may have been deleted.", "conversation_not_found", 404)
        return row

    def pin(self, user_id: UUID, conversation_id: UUID, pinned: bool):
        row = self._query(
            f"UPDATE public.conversations SET pinned_at = CASE WHEN %s THEN coalesce(pinned_at, now()) END "
            f"WHERE user_id = %s AND id = %s RETURNING {SUMMARY}",
            (pinned, str(user_id), str(conversation_id)))
        if row is None:
            raise AppError("This chat was not found. It may have been deleted.", "conversation_not_found", 404)
        return row

    def delete(self, user_id: UUID, conversation_id: UUID) -> None:
        self._query("DELETE FROM public.conversations WHERE user_id = %s AND id = %s",
                    (str(user_id), str(conversation_id)))
