"""Chat use case orchestration."""

from app.config import Settings
from app.models.chat import ChatRequest, ChatResponse
from app.repositories.documents import DocumentRepository
from app.services.embeddings import EmbeddingService
from app.services.llm import LanguageModelService
from app.services.prompts import build_system_prompt


class ChatService:
    def __init__(
        self,
        settings: Settings,
        embeddings: EmbeddingService,
        documents: DocumentRepository,
        language_model: LanguageModelService,
    ):
        self._settings = settings
        self._embeddings = embeddings
        self._documents = documents
        self._language_model = language_model

    def chat(self, request: ChatRequest) -> ChatResponse:
        latest_query = request.messages[-1].content
        embedding = self._embeddings.embed(latest_query)
        documents = self._documents.search(
            embedding=embedding,
            user_id=self._settings.single_user_id,
        )
        prompt = build_system_prompt(documents)
        answer = self._language_model.respond(request.messages, prompt)
        return ChatResponse(response=answer)
