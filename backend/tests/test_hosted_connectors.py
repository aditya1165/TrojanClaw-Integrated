"""Hosted connector ownership, encryption, concurrency, and request boundaries."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest

from app.config import ConfigurationError
from app.errors import AppError
from app.integrations.hosted import HostedConnectors
from app.api.routes.integrations import get_connectors
from app.integrations.vault import SessionVault
from app.main import app
from app.models.chat import ChatRequest
from app.services.chat import ChatService



A, B = UUID(int=2), UUID(int=3)


class MemoryRepository:
    def __init__(self):
        self.rows = {}

    def list(self, user_id):
        return [row.copy() for (owner, _), row in self.rows.items() if owner == user_id]

    def get(self, user_id, provider):
        row = self.rows.get((user_id, provider))
        return row.copy() if row else None

    def reserve(self, user_id, provider):
        row = self.rows.setdefault((user_id, provider), {
            "provider": provider, "state": "not_configured", "session": None, "login": None,
        })
        if row["state"] in {"connecting", "revoking"}:
            raise AppError("Busy", "connector_busy", 409)
        row.update(state="connecting", operation_id=uuid4(), expires_at=datetime.now(timezone.utc) + timedelta(minutes=10))
        return row.copy()

    def _match(self, user_id, provider, operation_id):
        row = self.rows[(user_id, provider)]
        if row.get("operation_id") != operation_id:
            raise AppError("Replaced", "login_expired", 409)
        return row

    def attach(self, user_id, provider, operation_id, session, login):
        self._match(user_id, provider, operation_id).update(session=session, login=login)

    def complete(self, user_id, provider, operation_id, session):
        self._match(user_id, provider, operation_id).update(state="connected", session=session, login=None, operation_id=None)

    def cancel(self, user_id, provider, operation_id):
        self._match(user_id, provider, operation_id).update(state="needs_login", login=None, operation_id=None)

    def mark_needs_login(self, user_id, provider, session):
        row = self.rows[(user_id, provider)]
        if row["session"] == session:
            row["state"] = "needs_login"

    def revoke(self, user_id, provider):
        row = self.rows.get((user_id, provider))
        if row:
            row.update(state="revoking", operation_id=uuid4())
            return row.copy()

    def delete(self, user_id, provider, operation_id):
        self._match(user_id, provider, operation_id)
        del self.rows[(user_id, provider)]


@pytest.fixture
def hosted():
    repository = MemoryRepository()
    vault = SessionVault(Fernet.generate_key().decode())
    browser, runtime = MagicMock(), MagicMock()
    browser.create_context.side_effect = lambda: str(uuid4())
    browser.create_session.side_effect = lambda context: {"id": str(uuid4()), "connectUrl": "wss://connect.browserbase.com?secret=test"}
    browser.live_url.return_value = "https://www.browserbase.com/live?token=private-test"
    runtime.run.side_effect = lambda provider, action, **kwargs: {
        "capture": {"session": {"origin": "https://usc.joinhandshake.com", "cookies": [{"name": "session", "value": "private-cookie"}]}},
        "tools": {"tools": [{"name": "hs_get_profile", "description": "Profile", "input_schema": {"type": "object"}}]},
        "call": {"content": [{"type": "text", "text": "private data"}]},
    }.get(action, {"ok": True})
    return HostedConnectors(repository, lambda: vault, lambda: browser, runtime)


def connected(service, user=A, provider="handshake"):
    login = service.connect(user, provider)
    service.finish(user, provider, UUID(login["login_id"]))
    return login


def test_usc_import_has_distinct_encrypted_provider_contexts_and_reuses_own_context(hosted):
    for provider in ("brightspace", "handshake"):
        hosted.import_session(A, provider, {"origin": "https://brightspace.usc.edu" if provider == "brightspace" else "https://usc.joinhandshake.com",
                                            "cookies": [{"name": "session", "value": provider + "-cookie"}]})
    first = hosted._session(A, "brightspace")[1]
    second = hosted._session(A, "handshake")[1]
    assert first["context_id"] != second["context_id"]
    assert b"brightspace-cookie" not in hosted.repository.get(A, "brightspace")["session"]
    assert hosted.repository.get(B, "handshake") is None
    hosted.import_session(A, "handshake", {"origin": second["origin"], "cookies": second["cookies"]})
    assert hosted._session(A, "handshake")[1]["context_id"] == second["context_id"]
    assert hosted._browser_factory().create_context.call_count == 2


def test_users_never_share_connections_or_browser_contexts(hosted):
    connected(hosted, A)
    assert next(row for row in hosted.statuses(B) if row.provider == "handshake").state == "not_configured"
    connected(hosted, B)
    first = hosted._open(A, "handshake", "session", hosted.repository.get(A, "handshake")["session"])
    second = hosted._open(B, "handshake", "session", hosted.repository.get(B, "handshake")["session"])
    assert first["context_id"] != second["context_id"]
    assert "private-cookie" not in repr(hosted.repository.rows)


@pytest.mark.parametrize("provider", ["brightspace", "handshake", "piazza"])
def test_verified_reauthentication_replaces_unreadable_session_only_for_owner(hosted, provider):
    connected(hosted, A, provider)
    connected(hosted, B, provider)
    other = hosted.repository.get(B, provider)
    row = hosted.repository.rows[(A, provider)]
    old = SessionVault(Fernet.generate_key().decode())
    row["session"] = old.seal(A, provider, "session", {"cookies": [{"name": "session", "value": "lost-key-cookie"}]})
    fresh = {"origin": "https://piazza.com" if provider == "piazza" else "https://brightspace.usc.edu" if provider == "brightspace" else "https://usc.joinhandshake.com",
             "cookies": [{"name": "session", "value": "new-verified-cookie"}]}
    hosted.import_session(A, provider, fresh)
    assert hosted._session(A, provider)[1]["cookies"] == fresh["cookies"]
    assert hosted.repository.get(B, provider) == other


def test_unreadable_session_fails_closed_but_explicit_connect_starts_fresh(hosted):
    connected(hosted)
    hosted.repository.rows[(A, "handshake")]["session"] = b"unreadable-old-ciphertext"
    hosted.runtime.run.reset_mock()
    with pytest.raises(AppError) as exc:
        hosted.tools(A, ["handshake"])
    assert exc.value.code == "session_invalid"
    assert hosted.repository.get(A, "handshake")["state"] == "needs_login"
    hosted.runtime.run.assert_not_called()
    login = hosted.connect(A, "handshake")
    hosted.finish(A, "handshake", UUID(login["login_id"]))
    assert hosted._session(A, "handshake")[1]["cookies"]


def test_failed_fresh_verification_does_not_replace_unreadable_record(hosted):
    connected(hosted)
    hosted.repository.rows[(A, "handshake")]["session"] = b"unreadable-old-ciphertext"
    hosted.runtime.run.side_effect = AppError("Verification failed", "verification_required", 409)
    with pytest.raises(AppError):
        hosted.import_session(A, "handshake", {"cookies": []})
    assert hosted.repository.get(A, "handshake")["session"] == b"unreadable-old-ciphertext"
    assert hosted.repository.get(A, "handshake")["state"] == "connected"


def test_skipped_handshake_replaces_unreadable_saved_context_after_usc_verification(hosted):
    connected(hosted)
    hosted.repository.rows[(A, "handshake")]["session"] = b"unreadable-old-ciphertext"
    context = str(uuid4())
    assert hosted.import_handshake_login_context(A, context)
    assert hosted.owns_handshake_context(A, context)
    assert hosted.repository.get(A, "handshake")["state"] == "needs_login"


def test_handshake_adopts_usc_browser_without_copying_or_losing_idp_state(hosted):
    context = str(uuid4())
    hosted.import_session(A, 'handshake', {'origin':'https://usc.joinhandshake.com', 'cookies':[{'name':'session','value':'test'}]},
                          managed_context_id=context)
    assert hosted._session(A, 'handshake')[1]['context_id'] == context
    hosted._browser_factory().create_session.assert_not_called()
    assert hosted.owns_handshake_context(A, context)
    assert not hosted.owns_handshake_context(B, context)
    hosted.disconnect(A, 'handshake')
    hosted._browser_factory().delete_context.assert_called_once_with(context)


def test_skipped_handshake_retains_only_own_usc_browser_without_claiming_connection(hosted):
    context = str(uuid4())
    assert hosted.import_handshake_login_context(A, context)
    assert hosted.repository.get(A, 'handshake')['state'] == 'needs_login'
    assert hosted.owns_handshake_context(A, context)
    assert not hosted.owns_handshake_context(B, context)
    hosted.connect(A, 'handshake')
    hosted._browser_factory().create_session.assert_called_once_with(context)


def test_other_user_cannot_resume_or_finish_login(hosted):
    login = hosted.connect(A, "handshake")
    for action in (lambda: hosted.resume(B, "handshake"),
                   lambda: hosted.finish(B, "handshake", UUID(login["login_id"]))):
        with pytest.raises(AppError):
            action()
    assert not any(call.args[1] == "capture" for call in hosted.runtime.run.call_args_list)


def test_old_login_attempt_cannot_replace_newer_login(hosted):
    login = hosted.connect(A, "handshake")
    with pytest.raises(AppError):
        hosted.finish(A, "handshake", uuid4())
    assert hosted.resume(A, "handshake")["login_id"] == login["login_id"]


def test_concurrent_workers_cannot_create_second_login(hosted):
    hosted.connect(A, "handshake")
    second_worker = HostedConnectors(hosted.repository, hosted._vault_factory, hosted._browser_factory, hosted.runtime)
    with pytest.raises(AppError) as error:
        second_worker.connect(A, "handshake")
    assert error.value.code == "connector_busy"
    assert hosted._browser_factory().create_session.call_count == 1


def test_finish_verifies_before_marking_connected_and_preserves_incomplete_login(hosted):
    login = hosted.connect(A, "handshake")
    hosted.runtime.run.side_effect = AppError("Finish MFA", "login_incomplete", 409)
    with pytest.raises(AppError):
        hosted.finish(A, "handshake", UUID(login["login_id"]))
    assert hosted.repository.get(A, "handshake")["state"] == "connecting"
    hosted._browser_factory().release.assert_not_called()


def test_disconnect_deletes_only_owner_context_and_immediately_blocks_reads(hosted):
    connected(hosted, A)
    connected(hosted, B)
    hosted._browser_factory().delete_context.side_effect = AppError("Unavailable", "browser_unavailable", 502)
    with pytest.raises(AppError):
        hosted.disconnect(A, "handshake")
    assert hosted.repository.get(A, "handshake")["state"] == "revoking"
    with pytest.raises(AppError):
        hosted.tools(A, ["handshake"])
    assert hosted.repository.get(B, "handshake")["state"] == "connected"
    hosted._browser_factory().delete_context.side_effect = None
    hosted.disconnect(A, "handshake")
    assert hosted.repository.get(A, "handshake") is None


def test_tool_call_cannot_use_other_account_or_unselected_source(hosted):
    connected(hosted, A)
    with pytest.raises(AppError):
        hosted.call(B, "handshake__hs_get_profile", {}, ["handshake"])
    with pytest.raises(AppError):
        hosted.call(A, "handshake__hs_get_profile", {}, ["brightspace"])
    assert "private data" in hosted.call(A, "handshake__hs_get_profile", {}, ["handshake"])


def test_saved_session_survives_service_restart(hosted):
    connected(hosted)
    new_worker = HostedConnectors(hosted.repository, hosted._vault_factory, hosted._browser_factory, hosted.runtime)
    assert new_worker.tools(A, ["handshake"])[0]["name"] == "handshake__hs_get_profile"


@pytest.mark.parametrize('action', ['verify', 'call'])
@pytest.mark.parametrize('code,expected', [
    ('verification_required', 'connected'), ('connector_rate_limited', 'connected'),
    ('connector_failed', 'connected'), ('session_expired', 'needs_login'),
])
def test_handshake_failures_only_invalidate_a_confirmed_expired_session(hosted, action, code, expected):
    connected(hosted)
    hosted.runtime.run.side_effect = AppError('Safe provider error', code, 409)
    with pytest.raises(AppError):
        if action == 'verify':
            hosted.verify(A, 'handshake')
        else:
            hosted.call(A, 'handshake__hs_get_profile', {}, ['handshake'])
    assert hosted.repository.get(A, 'handshake')['state'] == expected


@pytest.mark.parametrize("user,provider,purpose", [(B, "handshake", "session"), (A, "brightspace", "session"), (A, "handshake", "login")])
def test_encrypted_data_cannot_be_reassigned_to_another_owner_or_purpose(user, provider, purpose):
    vault = SessionVault(Fernet.generate_key().decode())
    token = vault.seal(A, "handshake", "session", {"cookies": "secret"})
    with pytest.raises(AppError):
        vault.open(user, provider, purpose, token)


def test_key_rotation_and_tamper_rejection():
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    token = SessionVault(old).seal(A, "handshake", "session", {"cookies": "secret"})
    rotated = SessionVault(new + "," + old)
    assert rotated.open(A, "handshake", "session", token) == {"cookies": "secret"}
    with pytest.raises(AppError):
        rotated.open(A, "handshake", "session", token[:-5] + b"xxxxx")
    with pytest.raises(ConfigurationError):
        SessionVault("")


@pytest.mark.parametrize("path,method", [("", "get"), ("/handshake/connect", "post"), ("/handshake/login", "get"),
    ("/handshake/finish", "post"), ("/handshake/verify", "post"), ("/handshake/disconnect", "post")])
def test_all_connector_endpoints_require_login(path, method):
    response = getattr(TestClient(app), method)("/integrations" + path)
    assert response.status_code == 401


def test_routes_use_session_owner_and_do_not_cache_live_links(signed_in_account, hosted):
    app.dependency_overrides[get_connectors] = lambda: hosted
    try:
        response = TestClient(app).post("/integrations/handshake/connect", json={"user_id": str(B)}, headers={"Origin": "http://127.0.0.1:5173"})
        assert response.status_code == 201
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "connectUrl" not in response.text and "wss://" not in response.text
        assert hosted.repository.get(B, "handshake") is None
        assert hosted.repository.get(A, "handshake") is not None
    finally:
        app.dependency_overrides.pop(get_connectors, None)


def test_cross_site_connection_is_rejected(signed_in_account, hosted):
    app.dependency_overrides[get_connectors] = lambda: hosted
    try:
        response = TestClient(app).post("/integrations/handshake/connect", headers={"Origin": "https://untrusted.example"})
        assert response.status_code == 403
        assert not hosted.repository.rows
    finally:
        app.dependency_overrides.pop(get_connectors, None)


def test_all_three_connectors_are_selectable_in_chat():
    request = ChatRequest(messages=[{"role": "user", "content": "Hello"}],
                          connectors=["brightspace", "handshake", "piazza"])
    assert request.connectors == ["brightspace", "handshake", "piazza"]
    with pytest.raises(ValueError):
        ChatRequest(messages=request.messages, connectors=["unknown"])


def test_piazza_lifecycle_is_owner_bound_and_isolated_from_other_providers(hosted):
    connected(hosted, A, "piazza")
    connected(hosted, B, "piazza")
    connected(hosted, A, "brightspace")
    sessions = [hosted._session(owner, provider)[1]["context_id"]
                for owner, provider in [(A, "piazza"), (B, "piazza"), (A, "brightspace")]]
    assert len(set(sessions)) == 3
    assert next(row for row in hosted.statuses(A) if row.provider == "piazza").state == "connected"
    with pytest.raises(AppError):
        hosted.call(A, "piazza__list_courses", {}, ["handshake"])
    hosted.disconnect(A, "piazza")
    assert hosted.repository.get(A, "piazza") is None
    assert hosted.repository.get(B, "piazza")["state"] == "connected"
    assert hosted.repository.get(A, "brightspace")["state"] == "connected"
