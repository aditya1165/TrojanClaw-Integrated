"""Knowledge-document persistence and vector search."""

from uuid import UUID

from app.database import Database
from app.models.document import RetrievedDocument


class DocumentRepository:
    def __init__(self, database: Database):
        self._database = database

    def search(
        self,
        embedding: list[float],
        user_id: UUID,
        limit: int = 8,
        data_types: list[str] | None = None,
    ) -> list[RetrievedDocument]:
        vector = "[" + ",".join(map(str, embedding)) + "]"
        filters = ["user_id = %s", "embedding IS NOT NULL"]
        parameters: list[object] = [vector, str(user_id)]  # psycopg2 cannot adapt UUID
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
