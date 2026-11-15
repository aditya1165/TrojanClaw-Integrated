"""Chat endpoint."""

from fastapi import APIRouter, Depends

from app.dependencies import get_chat_service
from app.models.chat import ChatRequest, ChatResponse
from app.services.chat import ChatService


router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    service: ChatService = Depends(get_chat_service),
) -> ChatResponse:
    return service.chat(request)
