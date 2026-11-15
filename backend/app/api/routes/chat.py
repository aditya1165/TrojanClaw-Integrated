"""Chat endpoint."""

from fastapi import APIRouter, Depends

from app.dependencies import get_chat_service
from app.models.chat import ChatRequest, ChatResponse
from app.services.chat import ChatService
from app.api.routes.auth import get_current_user, require_trusted_origin
from app.models.auth import AccountProfile


router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_trusted_origin)])
def chat(
    request: ChatRequest,
    service: ChatService = Depends(get_chat_service),
    user: AccountProfile = Depends(get_current_user),
) -> ChatResponse:
    return service.chat(request, user_id=user.id)
