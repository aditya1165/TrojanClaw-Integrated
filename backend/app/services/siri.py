"""Revocable, chat-only credentials for Apple Shortcuts. Never browser sessions."""
from functools import lru_cache
import re
import secrets
from urllib.parse import urlparse
from uuid import UUID

from fastapi import HTTPException

from app.config import ConfigurationError, get_settings
from app.database import get_database
from app.services.auth import token_hash

KEY_DAYS = 30
KEY_SECONDS = KEY_DAYS * 86400
KEY_PATTERN = re.compile(r"tc_siri_[A-Za-z0-9_-]{43}\Z")


class SiriAccess:
    def __init__(self, settings, database):
        self.settings = settings
        self.database = database

    @property
    def chat_url(self) -> str | None:
        base = self.settings.siri_public_url
        if not base and self.settings.mcp_public_url:
            base = self.settings.mcp_public_url.rstrip("/") + "/api"
        if not base:
            return None
        base = base.rstrip("/")
        parsed = urlparse(base)
        if parsed.scheme != "https" or not parsed.hostname or parsed.netloc.find("@") >= 0 or parsed.query or parsed.fragment:
            raise ConfigurationError("SIRI_PUBLIC_URL must be the public HTTPS API base URL, without credentials, query or fragment.")
        return base + "/siri/chat"

    @staticmethod
    def storage_key(token: str) -> str:
        # The existing table requires length 64. This distinct namespace cannot
        # match a browser-session hash; 236 random hash bits remain.
        return "siri:" + token_hash(token)[:59]

    def status(self, user_id: UUID) -> dict:
        with self.database.connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT max(expires_at) FROM public.account_sessions WHERE user_id = %s AND token_hash LIKE 'siri:%%' AND expires_at > now()", (str(user_id),))
            row = cursor.fetchone()
        expires_at = row[0] if row else None
        return {"configured": self.chat_url is not None, "connected": expires_at is not None,
                "expires_at": expires_at, "chat_url": self.chat_url, "key_days": KEY_DAYS}

    def create(self, user_id: UUID) -> dict:
        if not self.chat_url:
            raise ConfigurationError("Set SIRI_PUBLIC_URL to the public HTTPS API base, or configure MCP_PUBLIC_URL.")
        token = "tc_siri_" + secrets.token_urlsafe(32)
        with self.database.connection() as connection, connection.cursor() as cursor:
            # Replacement is atomic. This only removes this account's Siri keys.
            cursor.execute("DELETE FROM public.account_sessions WHERE user_id = %s AND token_hash LIKE 'siri:%%'", (str(user_id),))
            cursor.execute("INSERT INTO public.account_sessions (token_hash, user_id, expires_at) VALUES (%s, %s, now() + %s * interval '1 second') RETURNING expires_at",
                           (self.storage_key(token), str(user_id), KEY_SECONDS))
            expires_at = cursor.fetchone()[0]
        return {"access_key": token, "expires_at": expires_at, "chat_url": self.chat_url}

    def owner(self, token: str) -> UUID:
        if not KEY_PATTERN.fullmatch(token):
            raise HTTPException(401, "Reconnect Siri in TrojanClaw Settings.", headers={"WWW-Authenticate": "Bearer"})
        with self.database.connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT user_id FROM public.account_sessions WHERE token_hash = %s AND expires_at > now()", (self.storage_key(token),))
            row = cursor.fetchone()
        if not row:
            raise HTTPException(401, "Your Siri key expired or was revoked. Create a new key in TrojanClaw Settings.", headers={"WWW-Authenticate": "Bearer"})
        return UUID(str(row[0]))

    def disconnect(self, user_id: UUID) -> None:
        with self.database.connection() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM public.account_sessions WHERE user_id = %s AND token_hash LIKE 'siri:%%'", (str(user_id),))


def spoken_answer(text: str) -> str:
    """Keep the answer's words while removing common Markdown speech clutter."""
    text = re.sub(r"!?\[([^\]]+)\]\([^\n)]+\)", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}(?:#{1,6}\s+|>\s?|[-*+]\s+)", "", text)
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
    text = re.sub(r"`+", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > 6000:
        text = text[:5900].rsplit(" ", 1)[0] + ". The answer is long. Ask a more specific question for the remaining details."
    return text or "TrojanClaw did not return an answer. Please try again."


@lru_cache(maxsize=1)
def get_siri_access() -> SiriAccess:
    return SiriAccess(get_settings(), get_database())
