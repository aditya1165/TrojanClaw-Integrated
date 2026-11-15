"""Files attached in chat: checked here, kept in a private Supabase Storage bucket.

Objects live at `<account id>/<upload id>/<file name>`, so an account can only ever
reach its own files: every lookup starts from the signed-in account's folder.
PDFs and images go to Claude as they are; text files as text; Word, PowerPoint and
Excel files as their extracted text (Claude does not read those formats directly).
"""

import base64
from dataclasses import dataclass
import logging
from functools import lru_cache
import io
import re
from typing import Any
from uuid import UUID, uuid4

from app.config import ConfigurationError, get_settings
from app.errors import AppError

logger = logging.getLogger(__name__)
BUCKET = "chat-uploads"
MAX_BYTES = 5 * 1024 * 1024  # per file
MAX_FILES = 5  # per message
# Attachments re-sent with each message (newest first). Base64 adds a third, and a
# Claude request is limited to 32 MB, so older files beyond this are left out.
CONTEXT_BUDGET = 18 * 1024 * 1024
MAX_EXTRACTED_CHARS = 300_000

_OFFICE = "application/vnd.openxmlformats-officedocument."
# extension -> (MIME type stored, kind)
TYPES: dict[str, tuple[str, str]] = {
    "pdf": ("application/pdf", "pdf"),
    "png": ("image/png", "image"),
    "jpg": ("image/jpeg", "image"),
    "jpeg": ("image/jpeg", "image"),
    "gif": ("image/gif", "image"),
    "webp": ("image/webp", "image"),
    "txt": ("text/plain", "text"),
    "md": ("text/markdown", "text"),
    "csv": ("text/csv", "text"),
    "json": ("application/json", "text"),
    "py": ("text/plain", "text"), "js": ("text/plain", "text"), "ts": ("text/plain", "text"),
    "java": ("text/plain", "text"), "c": ("text/plain", "text"), "cpp": ("text/plain", "text"),
    "html": ("text/plain", "text"), "css": ("text/plain", "text"), "sql": ("text/plain", "text"),
    "docx": (_OFFICE + "wordprocessingml.document", "docx"),
    "pptx": (_OFFICE + "presentationml.presentation", "pptx"),
    "xlsx": (_OFFICE + "spreadsheetml.sheet", "xlsx"),
}
ACCEPTED = ", ".join(sorted(f".{ext}" for ext in TYPES))


@dataclass(frozen=True)
class Upload:
    id: UUID
    name: str
    mime: str
    size: int
    kind: str

    def summary(self) -> dict[str, Any]:
        return {"id": str(self.id), "name": self.name, "mime": self.mime, "size": self.size, "kind": self.kind}


def safe_name(name: str) -> str:
    """A plain file name: no folders, control characters or path tricks."""
    base = re.split(r"[\\/]", name or "")[-1]
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip().lstrip(".")
    base = re.sub(r"[^A-Za-z0-9._\-()]", "_", base)[:120]  # storage keys: plain ASCII
    return base or "file"


def _extension(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def detect(name: str, data: bytes) -> tuple[str, str]:
    """(MIME type, kind) for an allowed file whose bytes match its extension."""
    found = TYPES.get(_extension(name))
    if not found:
        raise AppError(f"This file type isn't supported. Attach {ACCEPTED}.", "upload_type", 415)
    mime, kind = found
    signatures = {
        "pdf": data.startswith(b"%PDF-"),
        "image": (data.startswith(b"\x89PNG\r\n\x1a\n") if mime == "image/png"
                  else data.startswith(b"\xff\xd8\xff") if mime == "image/jpeg"
                  else data.startswith(b"GIF8") if mime == "image/gif"
                  else data[:4] == b"RIFF" and data[8:12] == b"WEBP"),
        "docx": data.startswith(b"PK\x03\x04"),
        "pptx": data.startswith(b"PK\x03\x04"),
        "xlsx": data.startswith(b"PK\x03\x04"),
    }
    if kind == "text":
        try:
            if b"\x00" in data:
                raise ValueError
            data.decode("utf-8")
        except ValueError:
            raise AppError("This text file isn't plain UTF-8 text.", "upload_content", 415) from None
    elif not signatures[kind]:
        raise AppError("This file's contents don't match its type. Re-save it and try again.", "upload_content", 415)
    return mime, kind


def _extract(kind: str, data: bytes) -> str:
    """Readable text from a Word, PowerPoint or Excel file."""
    try:
        if kind == "docx":
            from docx import Document
            document = Document(io.BytesIO(data))
            lines = [p.text for p in document.paragraphs]
            for table in document.tables:
                lines += [" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows]
        elif kind == "pptx":
            from pptx import Presentation
            lines = []
            for number, slide in enumerate(Presentation(io.BytesIO(data)).slides, 1):
                lines.append(f"--- Slide {number} ---")
                lines += [shape.text_frame.text for shape in slide.shapes if shape.has_text_frame and shape.text_frame.text.strip()]
        else:
            from openpyxl import load_workbook
            lines = []
            for sheet in load_workbook(io.BytesIO(data), read_only=True, data_only=True).worksheets:
                lines.append(f"--- Sheet: {sheet.title} ---")
                for row in sheet.iter_rows(values_only=True):
                    if any(value is not None for value in row):
                        lines.append(", ".join("" if value is None else str(value) for value in row))
                    if sum(len(line) for line in lines) > MAX_EXTRACTED_CHARS:
                        break
    except Exception:
        raise AppError("This file couldn't be read. Check that it opens, then attach it again.", "upload_unreadable", 422) from None
    return "\n".join(line for line in lines if line is not None).strip()


def content_block(upload: Upload, data: bytes) -> dict[str, Any]:
    """The Claude content block for an attachment."""
    if upload.kind == "pdf":
        return {"type": "document", "title": upload.name,
                "source": {"type": "base64", "media_type": "application/pdf", "data": base64.b64encode(data).decode()}}
    if upload.kind == "image":
        return {"type": "image", "source": {"type": "base64", "media_type": upload.mime, "data": base64.b64encode(data).decode()}}
    text = data.decode("utf-8") if upload.kind == "text" else _extract(upload.kind, data)
    if len(text) > MAX_EXTRACTED_CHARS:
        text = text[:MAX_EXTRACTED_CHARS] + f"\n\n[Only the first {MAX_EXTRACTED_CHARS:,} characters of this file are included.]"
    return {"type": "document", "title": upload.name,
            "source": {"type": "text", "media_type": "text/plain", "data": text or "[This file has no readable text.]"}}


def _storage(call, *args):
    """Run one Storage request; a Storage or network failure becomes a clear, safe error."""
    try:
        return call(*args)
    except (AppError, ConfigurationError):
        raise
    except Exception as exc:
        logger.warning("Supabase Storage request failed: %s", type(exc).__name__)
        raise AppError("File storage is unavailable right now. Please try again.", "upload_storage", 502) from exc


class UploadStore:
    """The private `chat-uploads` bucket, used only with the backend's secret key."""

    def __init__(self, url: str | None, secret_key: str | None):
        self._url, self._key = url, secret_key
        self._client = None

    def _bucket(self):
        if not self._url or not self._key:
            raise ConfigurationError("File uploads need SUPABASE_SECRET_KEY in backend/.env.")
        if self._client is None:
            from supabase import create_client
            self._client = create_client(self._url, self._key)
        return self._client.storage.from_(BUCKET)

    def save(self, user_id: UUID, name: str, data: bytes) -> Upload:
        if len(data) > MAX_BYTES:
            raise AppError("Files can be up to 5 MB.", "upload_too_large", 413)
        if not data:
            raise AppError("This file is empty.", "upload_empty", 400)
        name = safe_name(name)
        mime, kind = detect(name, data)
        if kind in {"docx", "pptx", "xlsx"}:
            _extract(kind, data)  # refuse a broken file now, not when the student sends the message
        upload = Upload(uuid4(), name, mime, len(data), kind)
        _storage(self._bucket().upload, f"{user_id}/{upload.id}/{name}", data, {"content-type": mime, "upsert": "false"})
        return upload

    def find(self, user_id: UUID, upload_id: UUID) -> tuple[Upload, str]:
        """The account's own upload with this ID (the folder is the account's, so others' files are never found)."""
        folder = f"{user_id}/{UUID(str(upload_id))}"
        entries = [entry for entry in _storage(self._bucket().list, folder) if entry.get("name") and entry.get("id")]
        if not entries:
            raise AppError("This file was not found. Attach it again.", "upload_not_found", 404)
        entry = entries[0]
        mime, kind = TYPES.get(_extension(entry["name"]), ("application/octet-stream", "text"))
        size = int((entry.get("metadata") or {}).get("size") or 0)
        return Upload(UUID(str(upload_id)), entry["name"], mime, size, kind), f"{folder}/{entry['name']}"

    def read(self, user_id: UUID, upload_id: UUID) -> tuple[Upload, bytes]:
        upload, path = self.find(user_id, upload_id)
        return upload, _storage(self._bucket().download, path)

    def delete(self, user_id: UUID, upload_ids: list[UUID]) -> None:
        paths = []
        for upload_id in upload_ids:
            try:
                paths.append(self.find(user_id, upload_id)[1])
            except AppError:
                continue
        if paths:
            _storage(self._bucket().remove, paths)


@lru_cache(maxsize=1)
def get_upload_store() -> UploadStore:
    settings = get_settings()
    return UploadStore(settings.supabase_url, settings.supabase_secret_key)
