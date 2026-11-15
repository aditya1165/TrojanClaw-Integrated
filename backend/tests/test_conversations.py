"""Saved chats: history comes from the server, chats are private, titles are written after the first reply."""

from datetime import datetime, timezone
import json
import time
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from app.dependencies import get_chat_service, get_conversation_repository
from app.errors import AppError
from app.main import app
from app.models.chat import ChatResponse

USER = UUID("00000000-0000-0000-0000-000000000002")  # the signed_in_account fixture
OTHER = uuid4()
pytestmark = pytest.mark.usefixtures("signed_in_account")


class FakeConversations:
    def __init__(self):
        self.chats, self.messages_by_chat = {}, {}

    def _owned(self, user_id, conversation_id):
        chat = self.chats.get(UUID(str(conversation_id)))
        if not chat or chat["user_id"] != user_id:
            raise AppError("This chat was not found. It may have been deleted.", "conversation_not_found", 404)
        return chat

    def list(self, user_id, search=""):
        chats = [c for c in self.chats.values() if c["user_id"] == user_id]
        if search:
            chats = [c for c in chats if search.lower() in (c["title"] or "").lower()
                     or any(search.lower() in m["content"].lower() for m in self.messages_by_chat[c["id"]])]
        return sorted(chats, key=lambda c: not c.get("pinned", False))

    def pin(self, user_id, conversation_id, pinned):
        chat = self._owned(user_id, conversation_id)
        chat["pinned"] = pinned
        return chat

    def create(self, user_id, title):
        chat = {"id": uuid4(), "user_id": user_id, "title": title, "updated_at": datetime.now(timezone.utc)}
        self.chats[chat["id"]] = chat
        self.messages_by_chat[chat["id"]] = []
        return chat

    def get(self, user_id, conversation_id):
        return self._owned(user_id, conversation_id)

    def messages(self, user_id, conversation_id, limit=None):
        self._owned(user_id, conversation_id)
        items = self.messages_by_chat[UUID(str(conversation_id))]
        return [dict(m) for m in (items[-limit:] if limit else items)]

    def add_turn(self, user_id, conversation_id, question, answer, sources=None, attachments=None):
        self._owned(user_id, conversation_id)
        for role, text, items, files in (("user", question, [], attachments or []), ("assistant", answer, sources or [], [])):
            self.messages_by_chat[conversation_id].append(
                {"id": uuid4(), "role": role, "content": text, "sources": items, "attachments": files,
                 "created_at": datetime.now(timezone.utc)})

    def rename(self, user_id, conversation_id, title):
        chat = self._owned(user_id, conversation_id)
        chat["title"] = title
        return chat

    def delete(self, user_id, conversation_id):
        if self.chats.get(conversation_id, {}).get("user_id") == user_id:
            del self.chats[conversation_id]


class FakeChatService:
    def __init__(self, fail=False):
        self.requests, self.fail, self.title_fails = [], fail, False

    async def title(self, question, answer):
        if self.title_fails:
            raise RuntimeError("no title today")
        return "Midterm study plan"

    async def chat(self, request, *, user_id, use_saved_connectors=False, conversation_id=None, emit=None,
                   allowed_connectors=None):
        self.owner, self.saved = user_id, use_saved_connectors
        self.allowed = allowed_connectors
        emit({"type": "activity", "text": "Checking Brightspace: upcoming due dates"})
        if self.fail:
            raise AppError("The model is unavailable.", "llm_down", 503)
        self.requests.append((request, conversation_id))
        answer = f"Answer {len(self.requests)}"
        for word in answer.split(" "):
            emit({"type": "text", "text": word})
        return ChatResponse(response=answer, sources=[{"title": "ASN-04", "url": "https://brightspace.usc.edu/x", "kind": "connector"}])


def send(client, body):
    """Post a message and return the streamed events."""
    response = client.post("/chat", json=body)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(line) for line in response.text.splitlines() if line]


def done(client, body):
    events = send(client, body)
    assert events[-1]["type"] == "done", events
    return events[-1]


def wait_for(check):
    for _ in range(100):
        if check():
            return
        time.sleep(0.01)


@pytest.fixture
def setup():
    state = {"repo": FakeConversations(), "service": FakeChatService()}
    app.dependency_overrides[get_conversation_repository] = lambda: state["repo"]
    app.dependency_overrides[get_chat_service] = lambda: state["service"]
    yield state
    for dependency in (get_conversation_repository, get_chat_service):
        app.dependency_overrides.pop(dependency, None)


def test_remote_chat_uses_only_the_signed_in_accounts_selected_sources(setup):
    with TestClient(app, client=("203.0.113.25",1234)) as client:
        result = done(client, {"message":"My Handshake events?", "connectors":["handshake"]})
    assert result["response"] == "Answer 1"
    assert setup["service"].owner == USER
    assert setup["service"].saved is True
    assert setup["service"].allowed == ["handshake"]


def test_first_message_creates_a_chat_and_later_messages_get_its_history(setup):
    with TestClient(app) as client:
        events = send(client, {"message": "When is my CSCI 270 midterm?"})
        assert [e["type"] for e in events] == ["activity", "text", "text", "done"]
        first = events[-1]
        chat_id = first["conversation_id"]
        assert first["response"] == "Answer 1"
        assert first["sources"][0]["url"] == "https://brightspace.usc.edu/x"
        assert first["title"] == "When is my CSCI 270 midterm?"  # until the AI title is written
        wait_for(lambda: setup["repo"].chats[UUID(chat_id)]["title"] == "Midterm study plan")
        second = done(client, {"conversation_id": chat_id, "message": "Make a study plan"})
        assert second["conversation_id"] == chat_id
        listed = client.get("/conversations").json()
        history = client.get(f"/conversations/{chat_id}/messages").json()
    assert setup["service"].allowed == []  # the sources turned on in chat limit the saved connectors
    request, used_chat = setup["service"].requests[1]
    assert str(used_chat) == chat_id
    assert [(m.role.value, m.content) for m in request.messages] == [
        ("user", "When is my CSCI 270 midterm?"), ("assistant", "Answer 1"), ("user", "Make a study plan")]
    assert [item["title"] for item in listed] == ["Midterm study plan"]  # AI title replaced the first line
    assert [m["content"] for m in history] == ["When is my CSCI 270 midterm?", "Answer 1", "Make a study plan", "Answer 2"]
    assert history[1]["sources"][0]["title"] == "ASN-04"  # links are saved with the reply


def test_someone_elses_chat_cannot_be_read_continued_renamed_or_deleted(setup):
    theirs = setup["repo"].create(OTHER, "Their chat")
    with TestClient(app) as client:
        assert client.get(f"/conversations/{theirs['id']}/messages").status_code == 404
        assert client.post("/chat", json={"conversation_id": str(theirs["id"]), "message": "hi"}).status_code == 404
        assert client.post(f"/conversations/{theirs['id']}/rename", json={"title": "Mine now"}).status_code == 404
        assert client.post(f"/conversations/{theirs['id']}/delete").status_code == 204
        assert client.get("/conversations").json() == []
    assert setup["repo"].chats[theirs["id"]]["title"] == "Their chat"
    assert setup["service"].requests == []


def test_failed_first_reply_leaves_no_empty_chat(setup):
    setup["service"].fail = True
    with TestClient(app) as client:
        events = send(client, {"message": "Hello"})
    assert events[-1] == {"type": "error", "message": "The model is unavailable."}
    assert setup["repo"].chats == {}


def test_title_failure_keeps_the_first_line_title(setup):
    setup["service"].title_fails = True
    long_message = "Can you help me figure out " + "which classes " * 10
    with TestClient(app) as client:
        done(client, {"message": long_message})
        time.sleep(0.05)
        [chat] = client.get("/conversations").json()
    assert chat["title"].endswith("…") and len(chat["title"]) <= 60


def test_rename_and_delete(setup):
    with TestClient(app) as client:
        chat_id = done(client, {"message": "Hi"})["conversation_id"]
        renamed = client.post(f"/conversations/{chat_id}/rename", json={"title": "  Spring   plans "}).json()
        assert renamed["title"] == "Spring plans"
        assert client.post(f"/conversations/{chat_id}/rename", json={"title": "   "}).status_code == 422
        assert client.post(f"/conversations/{chat_id}/delete").status_code == 204
        assert client.get("/conversations").json() == []


def test_long_history_sends_only_recent_messages_starting_with_the_user(setup):
    chat = setup["repo"].create(USER, "Long chat")
    for index in range(30):
        setup["repo"].add_turn(USER, chat["id"], f"q{index}", f"a{index}")
    with TestClient(app) as client:
        done(client, {"conversation_id": str(chat["id"]), "message": "latest"})
    request, _ = setup["service"].requests[0]
    assert len(request.messages) == 21 and request.messages[0].content == "q20" and request.messages[-1].content == "latest"


def test_pin_and_search(setup):
    with TestClient(app) as client:
        first = done(client, {"message": "Internships for sophomores"})["conversation_id"]
        second = done(client, {"message": "Midterm dates"})["conversation_id"]
        assert client.post(f"/conversations/{first}/pin", json={"pinned": True}).json()["pinned"] is True
        listed = client.get("/conversations").json()
        assert listed[0]["id"] == first and listed[0]["pinned"] is True
        assert [c["id"] for c in client.get("/conversations", params={"q": "dates"}).json()] == [second]
        assert [c["id"] for c in client.get("/conversations", params={"q": "answer 1"}).json()] == [first]  # message text
