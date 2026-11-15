"""Vector search over the shared campus knowledge base (public USC pages, not per user)."""

from app.database import Database
from app.models.document import RetrievedDocument


class DocumentRepository:
    def __init__(self, database: Database):
        self._database = database

    def search(
        self,
        embedding: list[float],
        limit: int = 8,
        data_types: list[str] | None = None,
        min_similarity: float = 0.0,
        per_source_limit: int | None = None,
    ) -> list[RetrievedDocument]:
        """Closest campus chunks, dropping weak matches and capping chunks per page."""
        vector = "[" + ",".join(map(str, embedding)) + "]"
        filters = ["embedding IS NOT NULL"]
        parameters: list[object] = [vector]
        if data_types:
            filters.append("data_type = ANY(%s)")
            parameters.append(data_types)
        # Over-fetch when filtering so the caller still gets up to `limit` results.
        filtering = min_similarity > 0 or per_source_limit is not None
        parameters.append(limit * 4 if filtering else limit)

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

        results: list[RetrievedDocument] = []
        per_source: dict[str, int] = {}
        for row in rows:
            document = RetrievedDocument(
                chunk_text=row[0],
                source_url=row[1],
                page_title=row[2],
                data_type=row[3],
                campus=row[4],
                distance=float(row[5]),
            )
            if 1 - document.distance < min_similarity:
                continue
            if per_source_limit is not None:
                if per_source.get(document.source_url, 0) >= per_source_limit:
                    continue
                per_source[document.source_url] = per_source.get(document.source_url, 0) + 1
            results.append(document)
            if len(results) == limit:
                break
        return results
