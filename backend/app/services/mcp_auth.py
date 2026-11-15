"""Account-scoped OAuth for remote MCP; SDK handles PKCE and redirect validation.

Encrypted envelopes hold protocol metadata. Existing hashed account_sessions rows
hold single-use codes and revocable grants, so workers share state without a new schema.
"""
import asyncio
from functools import lru_cache
import json
import secrets
import time
from urllib.parse import urlencode, urlparse

from cryptography.fernet import Fernet, InvalidToken
from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError,
    OAuthAuthorizationServerProvider, RefreshToken, RegistrationError, TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from app.config import ConfigurationError, get_settings
from app.database import get_database
from app.services.auth import token_hash

SCOPE = "trojanclaw:chat"
CALENDAR_SCOPE = "trojanclaw:calendar-draft"
SCOPES = [SCOPE, CALENDAR_SCOPE]
GRANT_SECONDS = 7 * 86400


class McpOAuth(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    def __init__(self, settings, database):
        self.origin = (settings.mcp_public_url or "").rstrip("/")
        parsed = urlparse(self.origin)
        if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise ConfigurationError("MCP_PUBLIC_URL must be a public HTTPS origin without a path.")
        try:
            self.cipher = Fernet((settings.mcp_encryption_key or "").encode())
        except (ValueError, TypeError) as exc:
            raise ConfigurationError("Set MCP_ENCRYPTION_KEY to a persistent Fernet key.") from exc
        self.database = database
        self.resource = self.origin + "/mcp"

    def pack(self, kind, data):
        return self.cipher.encrypt(json.dumps({"kind": kind, "data": data}).encode()).decode()

    def unpack(self, kind, value, ttl):
        try:
            envelope = json.loads(self.cipher.decrypt(value.encode(), ttl=ttl))
            return envelope["data"] if envelope["kind"] == kind else None
        except (InvalidToken, ValueError, KeyError, TypeError, AttributeError):
            return None

    def _save(self, kind, secret, user_id, seconds):
        with self.database.connection() as connection, connection.cursor() as cursor:
            cursor.execute("INSERT INTO public.account_sessions (token_hash, user_id, expires_at) VALUES (%s, %s, now() + %s * interval '1 second')",
                           (self._key(kind, secret), str(user_id), seconds))

    @staticmethod
    def _key(kind, secret):
        # account_sessions requires exactly 64 characters. This non-hex namespace
        # cannot match website SHA-256 hashes; the suffix retains 232 bits.
        return f"mcp:{kind[0]}:" + token_hash(secret)[:58]

    def _owner(self, kind, secret, consume=False):
        with self.database.connection() as connection, connection.cursor() as cursor:
            key = self._key(kind, secret)
            if consume:
                cursor.execute("DELETE FROM public.account_sessions WHERE token_hash = %s AND expires_at > now() RETURNING user_id", (key,))
            else:
                cursor.execute("SELECT user_id FROM public.account_sessions WHERE token_hash = %s AND expires_at > now()", (key,))
            row = cursor.fetchone()
            return str(row[0]) if row else None

    async def register_client(self, client_info):
        if len(client_info.redirect_uris or []) > 10 or len(client_info.model_dump_json()) > 6000:
            raise RegistrationError("invalid_client_metadata", "Client metadata is too large.")
        for uri in client_info.redirect_uris or []:
            parsed = urlparse(str(uri))
            if parsed.scheme != "https" or parsed.username or parsed.fragment:
                raise RegistrationError("invalid_redirect_uri", "Use an HTTPS callback without credentials or fragments.")
        # DCR returns this authenticated encrypted client identifier. No client secrets
        # are stored in plaintext, and registration cannot grow an in-memory registry.
        data = client_info.model_dump(mode="json")
        data.pop("client_id", None)
        client_info.client_id = self.pack("client", data)

    async def get_client(self, client_id):
        if len(client_id) > 12000:
            return None
        data = self.unpack("client", client_id, 365 * 86400)
        return OAuthClientInformationFull.model_validate({**data, "client_id": client_id}) if data else None

    async def authorize(self, client, params: AuthorizationParams):
        if params.resource not in (None, self.resource):
            raise AuthorizeError("invalid_target", "Use the advertised TrojanClaw MCP resource.")
        requested = set(params.scopes or [SCOPE])
        if SCOPE not in requested or not requested.issubset(SCOPES):
            raise AuthorizeError("invalid_scope", "Request TrojanClaw chat and optionally calendar drafts.")
        pending = self.pack("pending", {"client_id": client.client_id, "name": (client.client_name or "Custom assistant")[:100],
            "params": params.model_dump(mode="json"), "nonce": secrets.token_urlsafe(24)})
        return self.origin + "/?" + urlencode({"mcp_request": pending})

    def pending(self, request):
        data = self.unpack("pending", request, 600)
        if not data:
            raise ValueError("The connection request expired. Restart linking in Gemini.")
        return data

    def approve(self, request, user_id):
        pending = self.pending(request)
        params = AuthorizationParams.model_validate(pending["params"])
        code = self.pack("code", {"client_id": pending["client_id"], "params": pending["params"], "nonce": secrets.token_urlsafe(24)})
        self._save("code", code, user_id, 120)
        # The SDK already checked this exact redirect against registered client metadata.
        separator = "&" if urlparse(str(params.redirect_uri)).query else "?"
        return str(params.redirect_uri) + separator + urlencode({"code": code, **({"state": params.state} if params.state is not None else {})})

    async def load_authorization_code(self, client, authorization_code):
        data = self.unpack("code", authorization_code, 120)
        if not data or data["client_id"] != client.client_id:
            return None
        owner = await asyncio.to_thread(self._owner, "code", authorization_code)
        if not owner:
            return None
        params = AuthorizationParams.model_validate(data["params"])
        return AuthorizationCode(code=authorization_code, client_id=client.client_id, scopes=params.scopes or [SCOPE],
            expires_at=self.cipher.extract_timestamp(authorization_code.encode()) + 120,
            code_challenge=params.code_challenge, redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly, resource=self.resource, subject=owner)

    def _tokens(self, client_id, owner, grant=None, deadline=None, scopes=None):
        scopes = scopes or [SCOPE]
        deadline = deadline or int(time.time()) + GRANT_SECONDS
        grant = grant or secrets.token_urlsafe(32)
        self._save("grant", grant, owner, max(0, deadline - int(time.time())))
        data = {"grant": grant, "client_id": client_id, "owner": owner, "deadline": deadline, "resource": self.resource, "scopes": scopes}
        return OAuthToken(access_token=self.pack("access", data), token_type="Bearer", expires_in=3600,
                          refresh_token=self.pack("refresh", data), scope=" ".join(scopes))

    async def exchange_authorization_code(self, client, authorization_code):
        owner = await asyncio.to_thread(self._owner, "code", authorization_code.code, True)
        if not owner:
            raise TokenError("invalid_grant", "This authorization code has already been used or expired.")
        return await asyncio.to_thread(self._tokens, client.client_id, owner, scopes=authorization_code.scopes)

    async def load_access_token(self, token):
        data = self.unpack("access", token, 3600)
        if not data or data["resource"] != self.resource or data["deadline"] <= time.time():
            return None
        owner = await asyncio.to_thread(self._owner, "grant", data["grant"])
        if owner != data["owner"]:
            return None
        return AccessToken(token=token, client_id=data["client_id"], scopes=data.get("scopes", [SCOPE]), subject=owner,
                           resource=self.resource, expires_at=min(data["deadline"], int(self.cipher.extract_timestamp(token.encode())) + 3600))

    async def load_refresh_token(self, client, refresh_token):
        data = self.unpack("refresh", refresh_token, GRANT_SECONDS)
        if not data or data["client_id"] != client.client_id or data["resource"] != self.resource or data["deadline"] <= time.time():
            return None
        owner = await asyncio.to_thread(self._owner, "grant", data["grant"])
        return RefreshToken(token=refresh_token, client_id=client.client_id, scopes=data.get("scopes", [SCOPE]),
            subject=owner, resource=self.resource, expires_at=data["deadline"]) if owner == data["owner"] else None

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        data = self.unpack("refresh", refresh_token.token, GRANT_SECONDS)
        if not data or data["deadline"] <= time.time() or SCOPE not in scopes or not set(scopes).issubset(data.get("scopes", [SCOPE])):
            raise TokenError("invalid_grant", "Reconnect TrojanClaw in Gemini.")
        owner = await asyncio.to_thread(self._owner, "grant", data["grant"], True)
        if owner != data["owner"]:
            raise TokenError("invalid_grant", "This refresh token was already used or revoked.")
        return await asyncio.to_thread(self._tokens, client.client_id, owner, deadline=data["deadline"], scopes=scopes)

    async def revoke_token(self, token):
        data = self.unpack("access", token.token, GRANT_SECONDS) or self.unpack("refresh", token.token, GRANT_SECONDS)
        if data:
            await asyncio.to_thread(self._owner, "grant", data["grant"], True)

    def status(self, user_id):
        with self.database.connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT count(*), max(expires_at) FROM public.account_sessions WHERE user_id = %s AND token_hash LIKE 'mcp:g:%%' AND expires_at > now()", (str(user_id),))
            row = cursor.fetchone()
        count, expires = row if row else (0, None)
        return {"connected": count > 0, "connections": count, "expires_at": expires, "mcp_url": self.resource}

    def disconnect(self, user_id):
        with self.database.connection() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM public.account_sessions WHERE user_id = %s AND token_hash LIKE 'mcp:%%'", (str(user_id),))


@lru_cache(maxsize=1)
def get_mcp_oauth():
    return McpOAuth(get_settings(), get_database())
