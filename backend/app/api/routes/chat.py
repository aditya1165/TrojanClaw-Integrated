"""Chat endpoint: runs the agent for the signed-in student."""

from fastapi import APIRouter, Depends, Request

from app.api.routes.auth import get_current_user, require_trusted_origin
from app.api.routes.integrations import require_local_request
from app.dependencies import get_chat_service
from app.errors import AppError
from app.models.auth import AccountProfile
from app.models.chat import ChatRequest, ChatResponse
from app.services.chat import ChatService


router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_trusted_origin)])
async def chat(
    request: ChatRequest,
    http_request: Request,
    service: ChatService = Depends(get_chat_service),
    user: AccountProfile = Depends(get_current_user),
) -> ChatResponse:
    if request.connectors:
        # Connector helpers run on this computer (see CONNECTORS.md).
        require_local_request(http_request)
    return await service.chat(
        request, user_id=user.id, use_saved_connectors=_is_local(http_request)
    )


def _is_local(http_request: Request) -> bool:
    """The signed-in account's saved connector sessions are used automatically only for requests from this computer."""
    try:
        require_local_request(http_request)
    except AppError:
        return False
    return True
