"""Text embeddings with Google Gemini.

Stored content and search queries must use the same model and dimensions as the
vector(N) columns in Supabase (documents.embedding, content_chunks.embedding).
"""

from collections.abc import Sequence
import math

from google import genai
from google.genai import types

from app.config import Settings
from app.errors import DependencyUnavailableError

# Gemini accepts up to 100 texts per embedding request.
_BATCH_SIZE = 100


def _normalize(values: Sequence[float]) -> list[float]:
    """L2-normalizes a vector. Gemini only normalizes its full 3072-dimension output."""
    norm = math.sqrt(sum(value * value for value in values))
    return [value / norm for value in values] if norm else list(values)


class EmbeddingService:
    def __init__(self, settings: Settings, client: genai.Client | None = None):
        self._settings = settings
        self._client = client

    def _get_client(self) -> genai.Client:
        if self._client is None:
            if not self._settings.gemini_api_key:
                raise DependencyUnavailableError("Gemini embeddings")
            self._client = genai.Client(api_key=self._settings.gemini_api_key)
        return self._client

    def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        client = self._get_client()
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _BATCH_SIZE):
            batch = texts[start : start + _BATCH_SIZE]
            response = client.models.embed_content(
                model=self._settings.embedding_model,
                contents=batch,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=self._settings.embedding_dimensions,
                ),
            )
            embeddings = response.embeddings or []
            if len(embeddings) != len(batch):
                raise DependencyUnavailableError("Gemini embeddings")
            vectors.extend(_normalize(item.values or []) for item in embeddings)
        return vectors

    def embed_query(self, text: str) -> list[float]:
        """Embeds a search query (RETRIEVAL_QUERY)."""
        return self._embed([text], "RETRIEVAL_QUERY")[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embeds content to be stored and searched later (RETRIEVAL_DOCUMENT)."""
        if not texts:
            return []
        return self._embed(texts, "RETRIEVAL_DOCUMENT")
