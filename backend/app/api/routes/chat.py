"""Chat endpoint. The server keeps each chat's history; the browser sends only the new message.

The reply streams back as newline-delimited JSON events:
  {"type": "activity", "text": "Checking Brightspace: upcoming due dates"}
  {"type": "text", "text": "..."}   part of the reply, as it is written
  {"type": "reset"}                 drop the text so far; the reply is being written again
  {"type": "done", ...}             the saved reply: response, sources, calendar_proposals, conversation_id, title
  {"type": "error", "message": "..."}
"""

import json
import logging
from queue import Queue
import threading
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from app.api.routes.auth import get_current_user, require_trusted_origin
from app.api.routes.integrations import require_local_request
from app.dependencies import get_chat_service, get_conversation_repository, get_language_model
from app.errors import AppError
from app.models.auth import AccountProfile
from app.models.chat import ChatMessage, ChatRequest, ChatTurn
from app.repositories.conversations import HISTORY_LIMIT, ConversationRepository
from app.services.chat import ChatService
from app.services.llm import LanguageModelService


router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)


def first_line_title(message: str) -> str:
    """Shown until the AI-written title arrives (or if it cannot be made)."""
    text = " ".join(message.split())
    return text if len(text) <= 60 else text[:57].rstrip() + "…"


def name_conversation(conversations: ConversationRepository, language_model: LanguageModelService,
                      user_id: UUID, conversation_id: UUID, question: str, answer: str) -> None:
    try:
        conversations.rename(user_id, conversation_id, language_model.title(question, answer))
    except Exception:  # The first-line title stays; a missing AI title never breaks the chat.
        logger.warning("Could not write a chat title", exc_info=True)


@router.post("/chat", dependencies=[Depends(require_trusted_origin)])
def chat(
    turn: ChatTurn,
    http_request: Request,
    service: ChatService = Depends(get_chat_service),
    conversations: ConversationRepository = Depends(get_conversation_repository),
    language_model: LanguageModelService = Depends(get_language_model),
    user: AccountProfile = Depends(get_current_user),
) -> StreamingResponse:
    if turn.connectors:
        require_local_request(http_request)

    history = conversations.messages(user.id, turn.conversation_id, HISTORY_LIMIT) if turn.conversation_id else []
    while history and history[0]["role"] != "user":  # the model expects the user to speak first
        history.pop(0)
    try:
        request = ChatRequest(
            messages=[ChatMessage(role=item["role"], content=item["content"][:20_000]) for item in history if item["content"].strip()]
            + [ChatMessage(role="user", content=turn.message)],
            connectors=turn.connectors, timezone=turn.timezone,
        )
    except ValidationError as exc:
        raise HTTPException(422, "; ".join(error["msg"].removeprefix("Value error, ") for error in exc.errors())) from exc

    created = turn.conversation_id is None
    conversation = conversations.create(user.id, first_line_title(turn.message)) if created \
        else conversations.get(user.id, turn.conversation_id)
    events: Queue = Queue()

    def work() -> None:
        # Runs to the end even if the browser stops listening, so the reply is still saved.
        saved = False
        try:
            response = service.chat(request, user_id=user.id, conversation_id=conversation["id"], emit=events.put)
            conversations.add_turn(user.id, conversation["id"], turn.message, response.response,
                                   [source.model_dump() for source in response.sources])
            saved = True
            response.conversation_id, response.title = conversation["id"], conversation["title"]
            events.put({"type": "done", **response.model_dump(mode="json")})
        except Exception as exc:
            if not isinstance(exc, AppError) or exc.status_code >= 500:
                logger.exception("Chat reply failed")
            if created:  # don't leave an empty chat behind
                try:
                    conversations.delete(user.id, conversation["id"])
                except Exception:
                    logger.exception("Could not remove the empty chat")
            message = exc.message if isinstance(exc, AppError) else "Something went wrong. Please try again."
            events.put({"type": "error", "message": message})
        finally:
            events.put(None)
        if saved and created:
            name_conversation(conversations, language_model, user.id, conversation["id"], turn.message, response.response)

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while (event := events.get()) is not None:
            yield json.dumps(event) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
