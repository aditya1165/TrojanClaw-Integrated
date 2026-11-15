"""Password verification and revocable, database-backed browser sessions."""

from functools import lru_cache
import hashlib
import hmac
import json
import logging
import re
import secrets
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg2.errors import UniqueViolation

from app.database import Database, get_database
from app.models.auth import AccountProfile, RegisterRequest


logger = logging.getLogger(__name__)
SESSION_SECONDS = 60 * 60 * 24 * 7
COOKIE_NAME = "trojanclaw_session"


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**17, r=8, p=1,
        maxmem=256 * 1024 * 1024, dklen=32,
    )
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, salt, _ = stored.split("$")
        if algorithm != "scrypt":
            return False
        return hmac.compare_digest(hash_password(password, bytes.fromhex(salt)), stored)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return hash_password(secrets.token_urlsafe(32))


def usc_usernames(unique_name: str, brightspace_id: str) -> list[str]:
    """Username candidates for a new USC account: the USC username first, then one from the Brightspace ID."""
    base = re.sub(r"[^a-z0-9_]", "_", unique_name.split("@")[0].lower())[:32]
    fallback = f"usc_{re.sub(r'[^a-z0-9_]', '_', brightspace_id.lower())}"[:32]
    return [name for name in (base, fallback) if len(name) >= 3]


def _profile(row: tuple) -> AccountProfile:
    return AccountProfile(id=row[0], name=row[1], username=row[2])


class AuthService:
    def __init__(self, database: Database):
        self.database = database

    def _new_session(self, cursor, user_id: str, old_token: str | None) -> str:
        if old_token:
            cursor.execute("DELETE FROM public.account_sessions WHERE token_hash = %s", (token_hash(old_token),))
        cursor.execute("DELETE FROM public.account_sessions WHERE user_id = %s AND expires_at <= now()", (user_id,))
        token = secrets.token_urlsafe(32)
        cursor.execute(
            "INSERT INTO public.account_sessions (token_hash, user_id, expires_at) VALUES (%s, %s, now() + %s * interval '1 second')",
            (token_hash(token), user_id, SESSION_SECONDS),
        )
        return token

    def register(self, payload: RegisterRequest, old_token: str | None) -> tuple[AccountProfile, str]:
        password_hash = hash_password(payload.password.get_secret_value())
        user_id = str(uuid4())
        try:
            with self.database.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO public.app_users (id, display_name, username) VALUES (%s, %s, %s) RETURNING id, display_name, username",
                        (user_id, payload.name, payload.username),
                    )
                    profile = _profile(cursor.fetchone())
                    cursor.execute(
                        "INSERT INTO public.account_credentials (user_id, password_hash) VALUES (%s, %s)",
                        (user_id, password_hash),
                    )
                    token = self._new_session(cursor, user_id, old_token)
        except UniqueViolation as exc:
            raise HTTPException(409, "That username is already taken.") from exc
        return profile, token

    def login(self, username: str, password: str, old_token: str | None) -> tuple[AccountProfile, str]:
        with self.database.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT u.id, u.display_name, u.username, c.password_hash FROM public.app_users u JOIN public.account_credentials c ON c.user_id = u.id WHERE u.username = %s",
                    (username,),
                )
                row = cursor.fetchone()
        valid = verify_password(password, row[3] if row else _dummy_hash())
        if not row or not valid:
            raise HTTPException(401, "Incorrect username or password.")
        with self.database.connection() as connection:
            with connection.cursor() as cursor:
                token = self._new_session(cursor, str(row[0]), old_token)
        return _profile(row), token

    def find_or_create_usc_account(self, whoami: dict) -> AccountProfile:
        """The account for Brightspace's whoami after a USC login, created on first use.

        USC accounts are matched only by Brightspace user ID, never by username, so a USC login
        can never take over a username/password account.
        """
        brightspace_id = str(whoami.get("Identifier") or "").strip()
        unique_name = str(whoami.get("UniqueName") or "").strip()
        if not brightspace_id:
            raise HTTPException(502, "Brightspace did not say who logged in.")
        name = " ".join(str(part) for part in (whoami.get("FirstName"), whoami.get("LastName")) if part).strip()
        name = (name or unique_name or "USC student")[:100]

        with self.database.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id, display_name, username FROM public.app_users WHERE profile->'usc'->>'brightspace_user_id' = %s",
                    (brightspace_id,),
                )
                row = cursor.fetchone()

        if row is None:
            profile = json.dumps({"usc": {"brightspace_user_id": brightspace_id, "unique_name": unique_name}})
            for username in usc_usernames(unique_name, brightspace_id):
                try:
                    with self.database.connection() as connection:
                        with connection.cursor() as cursor:
                            cursor.execute(
                                "INSERT INTO public.app_users (id, display_name, username, profile) VALUES (%s, %s, %s, %s::jsonb) RETURNING id, display_name, username",
                                (str(uuid4()), name, username, profile),
                            )
                            row = cursor.fetchone()
                    break
                except UniqueViolation:
                    continue  # username taken by another account; try the next one
            if row is None:
                raise HTTPException(409, "Could not create an account for this USC login.")
        logger.info("USC login: TrojanClaw user %s", row[2])
        return _profile(row)

    def start_session(self, user_id: UUID | str, old_token: str | None) -> str:
        """A new browser session for an account that has already proven who it is."""
        with self.database.connection() as connection:
            with connection.cursor() as cursor:
                return self._new_session(cursor, str(user_id), old_token)

    def current_user(self, token: str | None) -> AccountProfile:
        if not token or len(token) > 128:
            raise HTTPException(401, "Please sign in.")
        with self.database.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT u.id, u.display_name, u.username FROM public.account_sessions s JOIN public.app_users u ON u.id = s.user_id WHERE s.token_hash = %s AND s.expires_at > now()",
                    (token_hash(token),),
                )
                row = cursor.fetchone()
        if not row:
            raise HTTPException(401, "Your session has expired. Please sign in again.")
        return _profile(row)

    def logout(self, token: str | None) -> None:
        if token:
            with self.database.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM public.account_sessions WHERE token_hash = %s", (token_hash(token),))


@lru_cache(maxsize=1)
def get_auth_service() -> AuthService:
    return AuthService(get_database())
