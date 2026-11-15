"""Accounts (found from the USC login) and revocable, database-backed browser sessions."""

from functools import lru_cache
import hashlib
import json
import logging
import re
import secrets
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg2.errors import UniqueViolation

from app.database import Database, get_database
from app.models.auth import AccountProfile


logger = logging.getLogger(__name__)
SESSION_SECONDS = 60 * 60 * 24 * 7
COOKIE_NAME = "trojanclaw_session"






def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()




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

    def usc_sign_in(self, whoami, old_token=None):
        profile = self.find_or_create_usc_account(whoami)
        return profile, self.start_session(profile.id, old_token)

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
        if not token or len(token) > 128 or token.startswith(("tc_pair_", "tc_mcp_", "tc_oauth_", "tc_siri_")):
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

    def create_mobile_pairing(self, user_id: UUID) -> str:
        code = "tc_pair_" + secrets.token_urlsafe(24)
        with self.database.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.account_sessions (token_hash, user_id, expires_at) VALUES (%s, %s, now() + interval '2 minutes')",
                (token_hash(code), str(user_id)),
            )
        return code

    def redeem_mobile_pairing(self, code: str) -> str:
        if not code.startswith("tc_pair_") or len(code) != 40:
            raise HTTPException(401, "Invalid or expired pairing code.")
        with self.database.connection() as connection, connection.cursor() as cursor:
            # Consume once and create the phone session in the same transaction.
            cursor.execute("DELETE FROM public.account_sessions WHERE token_hash = %s AND expires_at > now() RETURNING user_id",
                           (token_hash(code),))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(401, "Invalid or expired pairing code.")
            return self._new_session(cursor, str(row[0]), None)

    def logout(self, token: str | None) -> None:
        if token:
            with self.database.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM public.account_sessions WHERE token_hash = %s", (token_hash(token),))


@lru_cache(maxsize=1)
def get_auth_service() -> AuthService:
    return AuthService(get_database())
