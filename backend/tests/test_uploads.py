"""Chat attachments: allowed types, private per-account storage, and how files reach Claude."""

import base64
import io
import json
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from app.dependencies import get_chat_service, get_conversation_repository
from app.errors import AppError
from app.main import app
from app.services import uploads as uploads_module
from app.services.uploads import Upload, UploadStore, content_block, detect, get_upload_store, safe_name
from tests.test_conversations import FakeChatService, FakeConversations

USER = UUID("00000000-0000-0000-0000-000000000002")  # the signed_in_account fixture
OTHER = uuid4()
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
pytestmark = pytest.mark.usefixtures("signed_in_account")


class FakeBucket:
    """In-memory stand-in for the Supabase Storage bucket."""

    def __init__(self):
        self.objects = {}

    def upload(self, path, data, options):
        assert options["upsert"] == "false"
        self.objects[path] = data

    def list(self, folder):
        return [{"id": "x", "name": p.rsplit("/", 1)[1], "metadata": {"size": len(d)}}
                for p, d in self.objects.items() if p.rsplit("/", 1)[0] == folder]

    def download(self, path):
        return self.objects[path]

    def remove(self, paths):
        for path in paths:
            self.objects.pop(path, None)


@pytest.fixture
def store(monkeypatch):
    bucket = FakeBucket()
    store = UploadStore("https://example.supabase.co", "secret")
    monkeypatch.setattr(store, "_bucket", lambda: bucket)
    store.bucket = bucket
    app.dependency_overrides[get_upload_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_upload_store, None)


def test_names_are_made_safe():
    assert safe_name("../../etc/passwd") == "passwd"
    assert safe_name("C:\\Users\\me\\Lecture 3 (final).pdf") == "Lecture_3_(final).pdf"
    assert safe_name("") == "file"


@pytest.mark.parametrize("name,data,kind", [
    ("notes.pdf", PDF, "pdf"), ("board.png", PNG, "image"), ("todo.md", b"# hi", "text"),
])
def test_allowed_files_are_recognised(name, data, kind):
    assert detect(name, data)[1] == kind


@pytest.mark.parametrize("name,data,code", [
    ("evil.svg", b"<svg/>", "upload_type"),          # not an allowed type
    ("fake.pdf", b"MZ\x90\x00", "upload_content"),  # contents don't match the extension
    ("binary.txt", b"a\x00b", "upload_content"),
])
def test_disallowed_or_disguised_files_are_refused(name, data, code):
    with pytest.raises(AppError) as error:
        detect(name, data)
    assert error.value.code == code


def test_office_files_become_text():
    from docx import Document
    from openpyxl import Workbook
    from pptx import Presentation
    from pptx.util import Inches

    doc = Document(); doc.add_paragraph("Midterm is on Nov 20"); docx = io.BytesIO(); doc.save(docx)
    deck = Presentation(); slide = deck.slides.add_slide(deck.slide_layouts[5]); slide.shapes.title.text = "Lecture 7"
    slide.shapes.add_textbox(Inches(1), Inches(2), Inches(4), Inches(1)).text_frame.text = "Dynamic programming"
    pptx = io.BytesIO(); deck.save(pptx)
    book = Workbook(); book.active.append(["Week", "Topic"]); book.active.append([1, "Graphs"]); xlsx = io.BytesIO(); book.save(xlsx)

    for kind, data, expected in (("docx", docx, "Midterm is on Nov 20"), ("pptx", pptx, "Dynamic programming"), ("xlsx", xlsx, "1, Graphs")):
        block = content_block(Upload(uuid4(), f"f.{kind}", "x", 0, kind), data.getvalue())
        assert block["type"] == "document" and expected in block["source"]["data"]


def test_pdfs_and_images_go_to_claude_as_they_are():
    pdf = content_block(Upload(uuid4(), "notes.pdf", "application/pdf", len(PDF), "pdf"), PDF)
    assert pdf["source"] == {"type": "base64", "media_type": "application/pdf", "data": base64.b64encode(PDF).decode()}
    image = content_block(Upload(uuid4(), "board.png", "image/png", len(PNG), "image"), PNG)
    assert image["type"] == "image" and image["source"]["media_type"] == "image/png"


def test_upload_limit_is_5_mb(store, monkeypatch):
    with TestClient(app) as client:
        big = client.post("/uploads", files={"file": ("big.pdf", PDF + b"0" * (5 * 1024 * 1024), "application/pdf")})
        assert big.status_code == 413
        ok = client.post("/uploads", files={"file": ("notes.pdf", PDF, "application/pdf")})
        assert ok.status_code == 200 and ok.json()["kind"] == "pdf"
    [path] = store.bucket.objects
    assert path.startswith(f"{USER}/{ok.json()['id']}/")  # kept in this account's own folder


def test_an_account_cannot_read_or_send_another_accounts_file(store):
    theirs = store.save(OTHER, "theirs.pdf", PDF)
    repo, service = FakeConversations(), FakeChatService()
    app.dependency_overrides[get_conversation_repository] = lambda: repo
    app.dependency_overrides[get_chat_service] = lambda: service
    try:
        with TestClient(app) as client:
            assert client.get(f"/uploads/{theirs.id}").status_code == 404
            assert client.post("/chat", json={"message": "read this", "attachments": [str(theirs.id)]}).status_code == 404
    finally:
        app.dependency_overrides.pop(get_conversation_repository, None)
        app.dependency_overrides.pop(get_chat_service, None)
    assert service.requests == []


def test_attached_files_reach_claude_and_are_saved_with_the_message(store, monkeypatch):
    repo, service = FakeConversations(), FakeChatService()
    app.dependency_overrides[get_conversation_repository] = lambda: repo
    app.dependency_overrides[get_chat_service] = lambda: service
    try:
        with TestClient(app) as client:
            notes = client.post("/uploads", files={"file": ("notes.pdf", PDF, "application/pdf")}).json()
            events = [json.loads(line) for line in client.post(
                "/chat", json={"message": "Summarize this", "attachments": [notes["id"]]}).text.splitlines()]
            chat_id = events[-1]["conversation_id"]
            history = client.get(f"/conversations/{chat_id}/messages").json()
            # A follow-up still sends the earlier file (within the size budget).
            monkeypatch.setattr(uploads_module, "CONTEXT_BUDGET", 10**9)
            client.post("/chat", json={"conversation_id": chat_id, "message": "And the second page?"})
            # Deleting the chat deletes its files.
            assert client.post(f"/conversations/{chat_id}/delete").status_code == 204
    finally:
        app.dependency_overrides.pop(get_conversation_repository, None)
        app.dependency_overrides.pop(get_chat_service, None)
    first, _ = service.requests[0]
    assert first.messages[-1].blocks[0]["type"] == "document" and first.messages[-1].content == "Summarize this"
    assert history[0]["attachments"][0]["name"] == "notes.pdf"
    follow_up, _ = service.requests[1]
    assert follow_up.messages[0].blocks[0]["type"] == "document"  # the earlier file is still there
    assert store.bucket.objects == {}


def test_earlier_files_beyond_the_budget_are_named_not_sent(store, monkeypatch):
    repo, service = FakeConversations(), FakeChatService()
    app.dependency_overrides[get_conversation_repository] = lambda: repo
    app.dependency_overrides[get_chat_service] = lambda: service
    try:
        with TestClient(app) as client:
            notes = client.post("/uploads", files={"file": ("notes.pdf", PDF, "application/pdf")}).json()
            chat_id = json.loads(client.post("/chat", json={"message": "Read", "attachments": [notes["id"]]}).text.splitlines()[-1])["conversation_id"]
            monkeypatch.setattr("app.api.routes.chat.CONTEXT_BUDGET", 0)
            client.post("/chat", json={"conversation_id": chat_id, "message": "More?"})
    finally:
        app.dependency_overrides.pop(get_conversation_repository, None)
        app.dependency_overrides.pop(get_chat_service, None)
    follow_up, _ = service.requests[1]
    assert follow_up.messages[0].blocks == [{"type": "text", "text": '[The earlier attachment "notes.pdf" is not included in this message. If you need it, ask the student to attach it again.]'}]


def test_uploads_without_the_secret_key_say_what_to_set():
    with pytest.raises(Exception) as error:
        UploadStore("https://example.supabase.co", None).save(USER, "notes.pdf", PDF)
    assert "SUPABASE_SECRET_KEY" in str(error.value)
