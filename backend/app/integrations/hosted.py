"""Hosted connectors: authenticated ownership, encrypted state, managed browsers."""

from datetime import datetime, timezone
from functools import lru_cache
import json
from uuid import UUID

from app.config import get_settings
from app.database import get_database
from app.errors import AppError
from app.integrations.browserbase import ManagedBrowser
from app.integrations.models import IntegrationProvider, IntegrationState, IntegrationStatus
from app.integrations.runtime import MCPRuntime
from app.integrations.vault import SessionVault
from app.repositories.connectors import ConnectorRepository


class HostedConnectors:
    def __init__(self, repository, vault_factory, browser_factory, runtime):
        self.repository = repository
        self._vault_factory, self._browser_factory = vault_factory, browser_factory
        self.runtime = runtime

    @staticmethod
    def _provider(provider: str):
        if provider not in {"brightspace", "handshake", "piazza"}:
            raise AppError("This connector is coming soon.", "connector_unavailable", 400)

    def statuses(self, user_id: UUID) -> list[IntegrationStatus]:
        records = {row["provider"]: row for row in self.repository.list(user_id)}
        return [IntegrationStatus(
            provider=provider, transport="mcp",
            state=IntegrationState(records.get(provider.value, {}).get("state", "not_configured")),
        ) for provider in IntegrationProvider]

    def _open(self, user_id, provider, purpose, ciphertext):
        return self._vault_factory().open(user_id, provider, purpose, ciphertext) if ciphertext else {}

    def _seal(self, user_id, provider, purpose, payload):
        return self._vault_factory().seal(user_id, provider, purpose, payload)

    def _previous_session(self, user_id, provider, ciphertext):
        """Reauthentication may replace old ciphertext whose key was lost.

        Only use while explicitly connecting or importing a newly verified login.
        Ordinary reads must fail closed; an unreadable context cannot be reused.
        """
        try:
            return self._open(user_id, provider, "session", ciphertext)
        except AppError as exc:
            if exc.code != "session_invalid":
                raise
            return {}

    def _login(self, user_id: UUID, provider: str, operation_id: UUID | None = None):
        self._provider(provider)
        row = self.repository.get(user_id, provider)
        if (not row or row["state"] != "connecting" or not row["login"]
                or row["expires_at"] <= datetime.now(timezone.utc)
                or operation_id is not None and str(row["operation_id"]) != str(operation_id)):
            raise AppError("This sign-in has expired or is not yours. Start again.", "login_expired", 409)
        return row, self._open(user_id, provider, "login", row["login"])

    def connect(self, user_id: UUID, provider: str) -> dict:
        self._provider(provider)
        self._vault_factory()  # Fail closed before reserving or creating external resources.
        browser = self._browser_factory()
        row = self.repository.reserve(user_id, provider)
        remote, created_context = None, None
        try:
            session = self._previous_session(user_id, provider, row["session"])
            context_id = session.get("context_id")
            if not context_id:
                context_id = created_context = browser.create_context()
            remote = browser.create_session(context_id)
            self.runtime.run(provider, "prepare", connect_url=remote["connectUrl"], origin=session.get("origin"))
            login = {"session_id": remote["id"], "connect_url": remote["connectUrl"], "context_id": context_id}
            # Context identifiers and CDP bearer URLs are encrypted, too.
            session["context_id"] = context_id
            live_url = browser.live_url(remote["id"])
            self.repository.attach(user_id, provider, row["operation_id"],
                                   self._seal(user_id, provider, "session", session),
                                   self._seal(user_id, provider, "login", login))
            return {"state": "connecting", "login_id": str(row["operation_id"]),
                    "live_url": live_url}
        except Exception:
            try:
                if remote:
                    browser.release(remote["id"])
                if created_context:
                    browser.delete_context(created_context)
            finally:
                self.repository.cancel(user_id, provider, row["operation_id"])
            raise

    def resume(self, user_id: UUID, provider: str) -> dict:
        row, login = self._login(user_id, provider)
        self.runtime.run(provider, "resize", connect_url=login["connect_url"])
        return {"state": "connecting", "login_id": str(row["operation_id"]),
                "live_url": self._browser_factory().live_url(login["session_id"])}

    def finish(self, user_id: UUID, provider: str, operation_id: UUID) -> None:
        row, login = self._login(user_id, provider, operation_id)
        # Incomplete sign-in keeps the browser open so the student can finish MFA.
        captured = self.runtime.run(provider, "capture", connect_url=login["connect_url"])["session"]
        self.runtime.run(provider, "verify", session=captured)
        captured["context_id"] = login["context_id"]
        # Release before marking connected; the provider persists its encrypted context.
        self._browser_factory().release(login["session_id"])
        self.repository.complete(user_id, provider, row["operation_id"],
                                 self._seal(user_id, provider, "session", captured))

    def import_session(self, user_id: UUID, provider: str, captured: dict, *, managed_context_id: str | None = None) -> None:
        """Save a server-verified USC result into a distinct account/provider context."""
        self._provider(provider)
        if managed_context_id is not None and provider != "handshake":
            raise ValueError("Only Handshake can retain the verified USC browser.")
        self.runtime.run(provider, "verify", session=captured)
        row = self.repository.reserve(user_id, provider)
        browser = self._browser_factory()
        context_id, remote = None, None
        created_context = False
        try:
            previous = self._previous_session(user_id, provider, row["session"])
            context_id = managed_context_id or previous.get("context_id")
            if not context_id:
                context_id = browser.create_context()
                created_context = True
            if not managed_context_id:
                remote = browser.create_session(context_id)
                self.runtime.run(provider, "seed", connect_url=remote["connectUrl"], session=captured)
                browser.release(remote["id"])
            elif previous.get("context_id") and previous["context_id"] != context_id:
                browser.delete_context(previous["context_id"])
            session = {**captured, "context_id": context_id}
            self.repository.complete(user_id, provider, row["operation_id"],
                                     self._seal(user_id, provider, "session", session))
        except Exception:
            try:
                if remote:
                    browser.release(remote["id"])
                if context_id and created_context:
                    browser.delete_context(context_id)
            finally:
                self.repository.cancel(user_id, provider, row["operation_id"])
            raise

    def owns_handshake_context(self, user_id: UUID, context_id: str) -> bool:
        row = self.repository.get(user_id, "handshake")
        return bool(row and self._previous_session(user_id, "handshake", row["session"]).get("context_id") == context_id)

    def import_handshake_login_context(self, user_id: UUID, context_id: str) -> bool:
        """Retain this authenticated owner's USC context when Handshake is skipped.

        Called only after server-side Brightspace identity verification. This
        keeps the IdP browser state with Browserbase, never in cookie JSON.
        """
        existing = self.repository.get(user_id, "handshake")
        if existing and self._previous_session(user_id, "handshake", existing["session"]).get("cookies"):
            return False
        row = self.repository.reserve(user_id, "handshake")
        try:
            previous = self._previous_session(user_id, "handshake", row["session"])
            if previous.get("context_id") and previous["context_id"] != context_id:
                self._browser_factory().delete_context(previous["context_id"])
            self.repository.attach(user_id, "handshake", row["operation_id"],
                                   self._seal(user_id, "handshake", "session", {"context_id": context_id}),
                                   self._seal(user_id, "handshake", "login", {}))
            self.repository.cancel(user_id, "handshake", row["operation_id"])
            return True
        except Exception:
            self.repository.cancel(user_id, "handshake", row["operation_id"])
            raise

    def cancel(self, user_id: UUID, provider: str, operation_id: UUID) -> None:
        row, login = self._login(user_id, provider, operation_id)
        self._browser_factory().release(login["session_id"])
        self.repository.cancel(user_id, provider, row["operation_id"])

    def disconnect(self, user_id: UUID, provider: str) -> None:
        self._provider(provider)
        row = self.repository.revoke(user_id, provider)
        if not row:
            return
        # Revoking blocks tool calls immediately, including while vendor cleanup retries.
        login = self._open(user_id, provider, "login", row["login"])
        session = self._open(user_id, provider, "session", row["session"])
        browser = self._browser_factory()
        if login.get("session_id"):
            browser.release(login["session_id"])
        if session.get("context_id"):
            browser.delete_context(session["context_id"])
        self.repository.delete(user_id, provider, row["operation_id"])

    def _session(self, user_id: UUID, provider: str):
        self._provider(provider)
        row = self.repository.get(user_id, provider)
        if not row or row["state"] != "connected":
            raise AppError(f"Connect your {provider.title()} account first.", "needs_login", 409)
        try:
            session = self._open(user_id, provider, "session", row["session"])
        except AppError as exc:
            if exc.code == "session_invalid":
                self.repository.mark_needs_login(user_id, provider, row["session"])
            raise
        if not session.get("cookies"):
            raise AppError("Your session is missing. Reconnect this source.", "needs_login", 409)
        return row, session

    def verify(self, user_id: UUID, provider: str) -> None:
        row, session = self._session(user_id, provider)
        try:
            self.runtime.run(provider, "verify", session=session)
        except AppError as error:
            if provider != "handshake" or error.code == "session_expired":
                self.repository.mark_needs_login(user_id, provider, row["session"])
            raise

    def tools(self, user_id: UUID, providers: list[str]) -> list[dict]:
        result = []
        for provider in dict.fromkeys(providers):
            _, session = self._session(user_id, provider)
            data = self.runtime.run(provider, "tools", session=session)
            result.extend({**tool, "name": f"{provider}__{tool['name']}"} for tool in data["tools"])
        return result

    def call(self, user_id: UUID, name: str, arguments: dict, providers: list[str]) -> str:
        provider, separator, tool = name.partition("__")
        if not separator or provider not in providers:
            raise AppError("This connector was not selected.", "connector_forbidden", 403)
        row, session = self._session(user_id, provider)
        try:
            result = self.runtime.run(provider, "call", session=session, tool=tool, arguments=arguments)
        except AppError as error:
            if provider != "handshake" or error.code == "session_expired":
                self.repository.mark_needs_login(user_id, provider, row["session"])
            raise
        from app.integrations.local import RESULT_CHARS
        text = json.dumps(result["content"])
        if len(text) > RESULT_CHARS:
            text = text[:RESULT_CHARS] + f"\n[Result shortened to {RESULT_CHARS:,} characters. Ask for a narrower range or one course/job for the rest.]"
        return text


@lru_cache(maxsize=1)
def get_connectors() -> HostedConnectors:
    settings = get_settings()
    return HostedConnectors(
        ConnectorRepository(get_database()),
        lambda: SessionVault(settings.connector_encryption_keys),
        lambda: ManagedBrowser(settings.browserbase_api_key, settings.browserbase_project_id),
        MCPRuntime(),
    )
