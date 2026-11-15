"""Saved chats: list, open, rename and delete. Sending messages is POST /chat."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.api.routes.auth import get_current_user, require_trusted_origin
from app.dependencies import get_conversation_repository
from app.models.auth import AccountProfile
from app.models.chat import ConversationMessage, ConversationSummary, PinConversation, RenameConversation
from app.repositories.conversations import ConversationRepository


def no_cache(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/conversations", tags=["conversations"], dependencies=[Depends(no_cache)])


@router.get("", response_model=list[ConversationSummary])
def list_conversations(q: str = Query("", max_length=100), user: AccountProfile = Depends(get_current_user),
                       conversations: ConversationRepository = Depends(get_conversation_repository)):
    return conversations.list(user.id, q.strip())


@router.get("/{conversation_id}/messages", response_model=list[ConversationMessage])
def messages(conversation_id: UUID, user: AccountProfile = Depends(get_current_user),
             conversations: ConversationRepository = Depends(get_conversation_repository)):
    return [message for message in conversations.messages(user.id, conversation_id) if message["content"]]


@router.post("/{conversation_id}/rename", response_model=ConversationSummary, dependencies=[Depends(require_trusted_origin)])
def rename(conversation_id: UUID, payload: RenameConversation, user: AccountProfile = Depends(get_current_user),
           conversations: ConversationRepository = Depends(get_conversation_repository)):
    return conversations.rename(user.id, conversation_id, payload.title)


@router.post("/{conversation_id}/pin", response_model=ConversationSummary, dependencies=[Depends(require_trusted_origin)])
def pin(conversation_id: UUID, payload: PinConversation, user: AccountProfile = Depends(get_current_user),
        conversations: ConversationRepository = Depends(get_conversation_repository)):
    return conversations.pin(user.id, conversation_id, payload.pinned)


@router.post("/{conversation_id}/delete", status_code=204, dependencies=[Depends(require_trusted_origin)])
def delete(conversation_id: UUID, user: AccountProfile = Depends(get_current_user),
           conversations: ConversationRepository = Depends(get_conversation_repository)):
    conversations.delete(user.id, conversation_id)
