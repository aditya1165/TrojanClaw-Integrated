"""Chat endpoint."""

from fastapi import APIRouter, Depends, Request

from app.api.routes.integrations import require_local_request

from app.dependencies import get_chat_service
from app.models.chat import ChatRequest, ChatResponse
from app.services.chat import ChatService


router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    http_request: Request,
    service: ChatService = Depends(get_chat_service),
) -> ChatResponse:
    if request.connectors:
        require_local_request(http_request)
    return service.chat(request)
