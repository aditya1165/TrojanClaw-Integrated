"""WebReg (USC Web Registration): what was last read from the student's course bin."""

import asyncio

from fastapi import APIRouter, Depends, Response

from app.api.routes.auth import get_current_user
from app.integrations.webreg import summary
from app.models.auth import AccountProfile

router = APIRouter(prefix="/integrations/webreg", tags=["webreg"])


@router.get("")
async def status(response: Response, user: AccountProfile = Depends(get_current_user)) -> dict:
    """When the course bin was last read (on Sign in with USC) and how many courses each term has."""
    response.headers["Cache-Control"] = "no-store"
    return await asyncio.to_thread(summary, user.id)
