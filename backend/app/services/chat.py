"""Chat use case orchestration."""

from uuid import UUID

from app.config import Settings
from app.models.chat import ChatRequest, ChatResponse
from app.repositories.documents import DocumentRepository
from app.services.embeddings import EmbeddingService
from app.services.llm import LanguageModelService
from app.services.prompts import build_system_prompt
from app.services.engagesc import EngageSCService
from app.errors import AppError
import logging
import psycopg2


class ChatService:
    def __init__(
        self,
        settings: Settings,
        embeddings: EmbeddingService,
        documents: DocumentRepository,
        language_model: LanguageModelService,
        campus_events: EngageSCService | None = None,
    ):
        self._settings = settings
        self._embeddings = embeddings
        self._documents = documents
        self._language_model = language_model
        self._campus_events = campus_events

    def chat(self, request: ChatRequest, *, user_id: UUID) -> ChatResponse:
        latest_query = request.messages[-1].content
        embedding = self._embeddings.embed(latest_query)
        documents = self._documents.search(
            embedding=embedding,
            user_id=user_id,
        )
        prompt = build_system_prompt(documents)
        if self._campus_events:
            try:
                prompt += self._campus_events.prompt_context(user_id)
            except (AppError, psycopg2.Error):
                logging.getLogger(__name__).warning("EngageSC context unavailable")
                prompt += "\nPublic EngageSC events are currently unavailable. Do not invent events."
        answer = self._language_model.respond(request.messages, prompt)
        return ChatResponse(response=answer)
