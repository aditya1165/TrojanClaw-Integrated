"""Chat attachments: upload a file, or download one of your own."""

import asyncio
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response

from app.api.routes.auth import get_current_user, require_trusted_origin
from app.errors import AppError
from app.models.auth import AccountProfile
from app.services.uploads import MAX_BYTES, UploadStore, get_upload_store

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post("", dependencies=[Depends(require_trusted_origin)])
async def upload(file: UploadFile = File(...), user: AccountProfile = Depends(get_current_user),
                 store: UploadStore = Depends(get_upload_store)) -> dict:
    data = await file.read(MAX_BYTES + 1)  # never read more than the limit allows
    if len(data) > MAX_BYTES:
        raise AppError("Files can be up to 5 MB.", "upload_too_large", 413)
    saved = await asyncio.to_thread(store.save, user.id, file.filename or "file", data)
    return saved.summary()


@router.get("/{upload_id}")
async def download(upload_id: UUID, user: AccountProfile = Depends(get_current_user),
                   store: UploadStore = Depends(get_upload_store)) -> Response:
    saved, data = await asyncio.to_thread(store.read, user.id, upload_id)
    return Response(data, media_type=saved.mime, headers={
        # Always a download, never rendered in the app's origin.
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(saved.name)}",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    })
