"""Password verification and revocable, database-backed browser sessions."""

from functools import lru_cache
import hashlib
import hmac
import secrets
from uuid import uuid4

from fastapi import HTTPException
from psycopg2.errors import UniqueViolation

from app.database import Database, get_database
from app.models.auth import AccountProfile, RegisterRequest


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
