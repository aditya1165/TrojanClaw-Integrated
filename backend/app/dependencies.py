"""Dependency wiring for the HTTP layer."""

from functools import lru_cache

from app.config import get_settings
from app.database import get_database
from app.repositories.documents import DocumentRepository
from app.services.chat import ChatService
from app.services.embeddings import EmbeddingService
from app.services.llm import LanguageModelService
from app.services.engagesc import get_engagesc_service


@lru_cache(maxsize=1)
def get_chat_service() -> ChatService:
    settings = get_settings()
    return ChatService(
        settings=settings,
        embeddings=EmbeddingService(settings),
        documents=DocumentRepository(get_database()),
        language_model=LanguageModelService(settings),
        campus_events=get_engagesc_service(),
    )
