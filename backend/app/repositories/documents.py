"""Knowledge-document persistence and vector search."""

from uuid import UUID
from functools import cached_property

from app.database import Database
from app.models.document import RetrievedDocument


class DocumentRepository:
    def __init__(self, database: Database):
        self._database = database

    @cached_property
    def _per_user_documents(self) -> bool:
        # Newer databases share public campus pages. Older installations still
        # carry user_id; retain their ownership filter until they migrate.
        with self._database.connection() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'documents'
                AND column_name = 'user_id'
            )""")
            return cursor.fetchone()[0]

    def search(
        self,
        embedding: list[float],
        user_id: UUID,
        limit: int = 8,
        data_types: list[str] | None = None,
    ) -> list[RetrievedDocument]:
        vector = "[" + ",".join(map(str, embedding)) + "]"
        filters = ["embedding IS NOT NULL"]
        parameters: list[object] = [vector]
        if self._per_user_documents:
            filters.append("user_id = %s")
            parameters.append(str(user_id))  # psycopg2 cannot adapt UUID
        if data_types:
            filters.append("data_type = ANY(%s)")
            parameters.append(data_types)
        parameters.append(limit)

        query = f"""
            SELECT
                chunk_text,
                source_url,
                COALESCE(page_title, 'Untitled'),
                COALESCE(data_type, 'general'),
                campus,
                embedding <=> %s::extensions.vector AS distance
            FROM public.documents
            WHERE {' AND '.join(filters)}
            ORDER BY embedding <=> %s::extensions.vector
            LIMIT %s
        """
        # The same vector is used in SELECT and ORDER BY.
        parameters.insert(-1, vector)

        with self._database.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, parameters)
                rows = cursor.fetchall()

        return [
            RetrievedDocument(
                chunk_text=row[0],
                source_url=row[1],
                page_title=row[2],
                data_type=row[3],
                campus=row[4],
                distance=float(row[5]),
            )
            for row in rows
        ]
