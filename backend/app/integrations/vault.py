"""Authenticated encryption with account, provider, and purpose binding."""

import json
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config import ConfigurationError
from app.errors import AppError


class SessionVault:
    def __init__(self, keys: str):
        try:
            self.cipher = MultiFernet([Fernet(key.strip().encode()) for key in keys.split(",") if key.strip()])
        except (ValueError, TypeError):
            raise ConfigurationError("CONNECTOR_ENCRYPTION_KEYS must contain valid Fernet keys.") from None

    def seal(self, user_id: UUID, provider: str, purpose: str, data: dict) -> bytes:
        return self.cipher.encrypt(json.dumps({
            "user_id": str(user_id), "provider": provider, "purpose": purpose, "data": data,
        }).encode())

    def open(self, user_id: UUID, provider: str, purpose: str, ciphertext) -> dict:
        try:
            value = json.loads(self.cipher.decrypt(bytes(ciphertext)))
            if (value["user_id"], value["provider"], value["purpose"]) != (str(user_id), provider, purpose):
                raise ValueError
            if not isinstance(value["data"], dict):
                raise ValueError
            return value["data"]
        except (InvalidToken, ValueError, TypeError, KeyError):
            raise AppError("The saved connection could not be opened. Reconnect this source.", "session_invalid", 409) from None
