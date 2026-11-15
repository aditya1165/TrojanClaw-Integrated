"""Saved chats: list, open, rename and delete. Sending messages is POST /chat."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.api.routes.auth import get_current_user, require_trusted_origin
from app.dependencies import get_conversation_repository
from app.models.auth import AccountProfile
from app.models.chat import ConversationMessage, ConversationSummary, PinConversation, RenameConversation
from app.errors import AppError
from app.repositories.conversations import ConversationRepository
from app.services.uploads import UploadStore, get_upload_store


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
           conversations: ConversationRepository = Depends(get_conversation_repository),
           store: UploadStore = Depends(get_upload_store)):
    try:
        files = [UUID(str(item["id"])) for message in conversations.messages(user.id, conversation_id)
                 for item in message.get("attachments") or []]
    except AppError:
        files = []  # not this account's chat: nothing to delete
    conversations.delete(user.id, conversation_id)
    if files:
        try:
            store.delete(user.id, files)  # the chat's attached files go with it
        except Exception:
            logging.getLogger(__name__).warning("Could not delete a deleted chat's files", exc_info=True)
