"""Chat endpoint: runs the agent for the signed-in student.

The server keeps each chat's history; the browser sends only the new message. The reply
streams back as newline-delimited JSON events:
  {"type": "activity", "text": "Checking Brightspace: upcoming due dates"}
  {"type": "text", "text": "..."}   part of the reply, as it is written
  {"type": "reset"}                 drop the text so far; the reply is being written again
  {"type": "done", ...}             the saved reply: response, sources, calendar_proposals, conversation_id, title
  {"type": "error", "message": "..."}
"""

import asyncio
import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from app.api.routes.auth import get_current_user, require_trusted_origin
from app.api.routes.integrations import require_local_request
from app.dependencies import get_chat_service, get_conversation_repository
from app.errors import AppError
from app.models.auth import AccountProfile
from app.models.chat import ChatMessage, ChatRequest, ChatTurn
from app.repositories.conversations import HISTORY_LIMIT, ConversationRepository
from app.services.chat import ChatService
from app.services.uploads import CONTEXT_BUDGET, Upload, UploadStore, content_block, get_upload_store


router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)
# Replies keep running if the browser stops listening; hold them so they finish and are saved.
_running: set[asyncio.Task] = set()


def first_line_title(message: str) -> str:
    """Shown until the AI-written title arrives (or if it cannot be made)."""
    text = " ".join(message.split())
    return text if len(text) <= 60 else text[:57].rstrip() + "…"


def _omitted(name: str) -> dict[str, Any]:
    return {"type": "text", "text": f"[The earlier attachment \"{name}\" is not included in this message. "
                                    "If you need it, ask the student to attach it again.]"}


async def _attachment_blocks(store: UploadStore, user_id: UUID, new: list[UUID],
                             history: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[Upload], list[list[dict[str, Any]]]]:
    """Content blocks for this message's files and for earlier ones, newest first, within CONTEXT_BUDGET.

    Returns (blocks for the new message, the new uploads, blocks for each history item)."""
    budget = CONTEXT_BUDGET
    uploads, new_blocks = [], []
    for upload_id in dict.fromkeys(new):  # each must be the signed-in account's own file
        upload, data = await asyncio.to_thread(store.read, user_id, upload_id)
        budget -= len(data)
        uploads.append(upload)
        new_blocks.append(await asyncio.to_thread(content_block, upload, data))
    if budget < 0:
        raise HTTPException(413, "These files are too large to send together. Attach fewer files.")
    history_blocks: list[list[dict[str, Any]]] = [[] for _ in history]
    # Files are re-sent only for one follow-up: the latest earlier question's files. Older ones are
    # named, not sent, because every file is paid for again on each message and tool round.
    last_question = max((i for i, item in enumerate(history) if item["role"] == "user"), default=-1)
    for index in range(len(history) - 1, -1, -1):
        for item in history[index].get("attachments") or []:
            size = int(item.get("size") or 0)
            try:
                if index != last_question or size > budget:
                    raise LookupError
                upload, data = await asyncio.to_thread(store.read, user_id, UUID(str(item["id"])))
                history_blocks[index].append(await asyncio.to_thread(content_block, upload, data))
                budget -= len(data)
            except (LookupError, AppError, ValueError, KeyError):
                history_blocks[index].append(_omitted(str(item.get("name") or "file")))
    return new_blocks, uploads, history_blocks


def _is_local(http_request: Request) -> bool:
    """The signed-in account's saved connector sessions are used automatically only for requests from this computer."""
    try:
        require_local_request(http_request)
    except AppError:
        return False
    return True


async def name_conversation(conversations: ConversationRepository, service: ChatService,
                            user_id: UUID, conversation_id: UUID, question: str, answer: str) -> None:
    try:
        title = await service.title(question, answer)
        await asyncio.to_thread(conversations.rename, user_id, conversation_id, title)
    except Exception:  # The first-line title stays; a missing AI title never breaks the chat.
        logger.warning("Could not write a chat title", exc_info=True)


@router.post("/chat", dependencies=[Depends(require_trusted_origin)])
async def chat(
    turn: ChatTurn,
    http_request: Request,
    service: ChatService = Depends(get_chat_service),
    conversations: ConversationRepository = Depends(get_conversation_repository),
    store: UploadStore = Depends(get_upload_store),
    user: AccountProfile = Depends(get_current_user),
) -> StreamingResponse:
    if turn.connectors:
        # Connector helpers run on this computer (see CONNECTORS.md).
        require_local_request(http_request)
    use_saved_connectors = _is_local(http_request)

    history = await asyncio.to_thread(conversations.messages, user.id, turn.conversation_id, HISTORY_LIMIT) \
        if turn.conversation_id else []
    while history and history[0]["role"] != "user":  # the model expects the user to speak first
        history.pop(0)
    history = [item for item in history if item["content"].strip()]
    new_blocks, uploads, history_blocks = await _attachment_blocks(store, user.id, turn.attachments, history)
    try:
        request = ChatRequest(
            messages=[ChatMessage(role=item["role"], content=item["content"][:20_000], blocks=blocks)
                      for item, blocks in zip(history, history_blocks)]
            + [ChatMessage(role="user", content=turn.message, blocks=new_blocks)],
            # The sources turned on in chat limit which saved sessions are used (below); none is forced,
            # so an expired session is skipped instead of failing the reply.
            connectors=[], timezone=turn.timezone,
        )
    except ValidationError as exc:
        raise HTTPException(422, "; ".join(error["msg"].removeprefix("Value error, ") for error in exc.errors())) from exc

    created = turn.conversation_id is None
    conversation = await asyncio.to_thread(conversations.create, user.id, first_line_title(turn.message)) if created \
        else await asyncio.to_thread(conversations.get, user.id, turn.conversation_id)
    events: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def work() -> None:
        saved = False
        try:
            response = await service.chat(request, user_id=user.id, use_saved_connectors=use_saved_connectors,
                                          conversation_id=conversation["id"], emit=events.put_nowait,
                                          allowed_connectors=turn.connectors)
            await asyncio.to_thread(conversations.add_turn, user.id, conversation["id"], turn.message,
                                    response.response, [source.model_dump() for source in response.sources],
                                    [upload.summary() for upload in uploads])
            saved = True
            response.conversation_id, response.title = UUID(str(conversation["id"])), conversation["title"]
            events.put_nowait({"type": "done", **response.model_dump(mode="json")})
        except Exception as exc:
            if not isinstance(exc, AppError) or exc.status_code >= 500:
                logger.exception("Chat reply failed")
            if created:  # don't leave an empty chat behind
                try:
                    await asyncio.to_thread(conversations.delete, user.id, conversation["id"])
                except Exception:
                    logger.exception("Could not remove the empty chat")
            message = exc.message if isinstance(exc, AppError) else "Something went wrong. Please try again."
            events.put_nowait({"type": "error", "message": message})
        finally:
            events.put_nowait(None)
        if saved and created:
            await name_conversation(conversations, service, user.id, conversation["id"], turn.message, response.response)

    task = asyncio.create_task(work())
    _running.add(task)
    task.add_done_callback(_running.discard)

    async def stream():
        while (event := await events.get()) is not None:
            yield json.dumps(event) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
