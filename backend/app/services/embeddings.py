"""Embedding provider abstraction."""

from openai import OpenAI

from app.config import Settings
from app.errors import DependencyUnavailableError


class EmbeddingService:
    def __init__(self, settings: Settings):
        self._settings = settings

    def embed(self, text: str) -> list[float]:
        if not self._settings.openai_api_key:
            raise DependencyUnavailableError("OpenAI embeddings")
        client = OpenAI(api_key=self._settings.openai_api_key)
        response = client.embeddings.create(
            input=text,
            model=self._settings.openai_embedding_model,
        )
        return response.data[0].embedding
