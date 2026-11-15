"""Sign in with USC: account matching, the login-attempt lifecycle and the routes."""

from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from psycopg2.errors import UniqueViolation

from app.main import app
from app.models.auth import AccountProfile
from app.services.auth import AuthService, COOKIE_NAME, get_auth_service, usc_usernames
from app.services.hosted_usc import UscLogin, get_usc_login
from tests.test_auth import StubDatabase

WHOAMI = {"Identifier": "12345", "FirstName": "Tommy", "LastName": "Trojan", "UniqueName": "ttrojan@usc.edu"}


def test_usc_usernames_prefer_usc_username_then_brightspace_id():
    assert usc_usernames("ttrojan@usc.edu", "12345") == ["ttrojan", "usc_12345"]
    assert usc_usernames("T.Trojan", "12345") == ["t_trojan", "usc_12345"]
    assert usc_usernames("ab", "12345") == ["usc_12345"]


def test_existing_usc_account_signs_in_without_creating_a_user():
    user_id = str(uuid4())
    database = StubDatabase((user_id, "Tommy Trojan", "ttrojan"))
    profile, token = AuthService(database).usc_sign_in(WHOAMI, None)
    assert profile.username == "ttrojan"
    sql = [call.args[0] for call in database.cursor.execute.call_args_list]
    assert "brightspace_user_id" in sql[0]
    assert database.cursor.execute.call_args_list[0].args[1] == ("12345",)
    assert not any("INSERT INTO public.app_users" in statement for statement in sql)
    assert any("INSERT INTO public.account_sessions" in statement for statement in sql)
    assert token


def test_new_usc_account_is_created_with_brightspace_id():
    user_id = str(uuid4())
    database = StubDatabase()
    database.cursor.fetchone.side_effect = [None, (user_id, "Tommy Trojan", "ttrojan")]
    profile, _ = AuthService(database).usc_sign_in(WHOAMI, None)
    assert profile.name == "Tommy Trojan"
    insert = next(c.args[1] for c in database.cursor.execute.call_args_list if "INSERT INTO public.app_users" in c.args[0])
    assert insert[1:3] == ("Tommy Trojan", "ttrojan")
    assert '"brightspace_user_id": "12345"' in insert[3]


def test_taken_username_falls_back_to_brightspace_id():
    user_id = str(uuid4())
    database = StubDatabase()
    database.cursor.fetchone.side_effect = [None, (user_id, "Tommy Trojan", "usc_12345")]
    inserts = []

    def execute(sql, params=None):
        if "INSERT INTO public.app_users" in sql:
            inserts.append(params[2])
            if len(inserts) == 1:
                raise UniqueViolation()

    database.cursor.execute.side_effect = execute
    profile, _ = AuthService(database).usc_sign_in(WHOAMI, None)
    assert inserts == ["ttrojan", "usc_12345"]
    assert profile.username == "usc_12345"


def test_whoami_without_identifier_is_rejected():
    with pytest.raises(HTTPException) as exc:
        AuthService(StubDatabase()).usc_sign_in({"UniqueName": "ttrojan"}, None)
    assert exc.value.status_code == 502


from unittest.mock import MagicMock
from uuid import UUID
from cryptography.fernet import Fernet
from app.errors import AppError
from app.integrations.vault import SessionVault


class Attempts:
    def __init__(self):
        self.rows = {}

    def create(self, attempt_id, binding, payload):
        if any(row[0] == binding for row in self.rows.values()):
            raise HTTPException(409, "Busy")
        self.rows[attempt_id] = [binding, payload, False]

    def current(self, binding):
        return next(((key, row[1]) for key, row in self.rows.items() if row[0] == binding), None)

    def claim(self, attempt_id, binding):
        row = self.rows.get(attempt_id)
        if not row or row[0] != binding or row[2]:
            raise HTTPException(409, "Expired, busy or not yours")
        row[2] = True
        return row[1]

    def save(self, attempt_id, binding, payload):
        self.rows[attempt_id][1:] = [payload, False]

    def checkpoint(self, attempt_id, binding, payload):
        assert self.rows[attempt_id][0] == binding and self.rows[attempt_id][2]
        self.rows[attempt_id][1] = payload

    def unlock(self, attempt_id, binding):
        if attempt_id in self.rows:
            self.rows[attempt_id][2] = False

    def delete(self, attempt_id, binding):
        del self.rows[attempt_id]


@pytest.fixture
def remote_usc():
    connectors = MagicMock()
    vault = SessionVault(Fernet.generate_key().decode())
    connectors._vault_factory.return_value = vault
    connectors.owns_handshake_context.return_value = False
    connectors.import_handshake_login_context.return_value = True
    browser = connectors._browser_factory.return_value
    browser.create_context.return_value = str(uuid4())
    browser.create_session.return_value = {"id": str(uuid4()), "connectUrl": "wss://connect.browserbase.com?secret=cdp"}
    browser.live_url.return_value = "https://www.browserbase.com/live?token=private"

    def run(provider, action, **kwargs):
        if action == "identity":
            return {"whoami": WHOAMI}
        if action == "capture":
            return {"session": {"origin": "https://brightspace.usc.edu" if provider == "brightspace" else "https://usc.joinhandshake.com",
                                "cookies": [{"name": "session", "value": "secret-cookie"}]}}
        return {}
    connectors.runtime.run.side_effect = run
    login = UscLogin(Attempts(), connectors)
    auth = MagicMock()
    auth.usc_sign_in.return_value = (AccountProfile(id=uuid4(), name="Tommy", username="ttrojan"), "session-token")
    return login, connectors, auth


def test_reopening_usc_reuses_only_the_same_browser_attempt(remote_usc):
    login, connectors, _ = remote_usc
    first = login.start('browser-A')
    assert login.start('browser-A') == first
    assert connectors._browser_factory.return_value.create_session.call_count == 1
    other = login.start('browser-B')
    assert other['attempt_id'] != first['attempt_id']


def test_cancel_previous_usc_does_not_affect_another_browser(remote_usc):
    login, _, _ = remote_usc
    own = login.start('browser-A')
    other = login.start('browser-B')
    login.cancel_current('browser-A')
    assert UUID(own['attempt_id']) not in login.repository.rows
    assert UUID(other['attempt_id']) in login.repository.rows


def test_remote_usc_survives_worker_change_and_splits_provider_sessions(remote_usc):
    login, connectors, auth = remote_usc
    start = login.start("browser-A")
    attempt_id = UUID(start["attempt_id"])
    assert b"cdp" not in login.repository.rows[attempt_id][1]
    other_worker = UscLogin(login.repository, connectors)
    waiting, token = other_worker.finish(attempt_id, "browser-A", auth, None)
    assert waiting["status"] == "connecting" and waiting["phase"] == "brightspace" and token is None
    auth.usc_sign_in.assert_not_called()
    login.finish(attempt_id, "browser-A", auth, None)
    login.finish(attempt_id, "browser-A", auth, None)
    result, token = login.finish(attempt_id, "browser-A", auth, None)
    assert result["status"] == "done" and token == "session-token"
    assert [call.args[1] for call in connectors.import_session.call_args_list] == ["brightspace", "handshake"]
    assert not login.repository.rows
    connectors._browser_factory.return_value.delete_context.assert_not_called()
    handshake_call = connectors.import_session.call_args_list[-1]
    assert handshake_call.kwargs['managed_context_id'] == connectors._browser_factory.return_value.create_context.return_value
    with pytest.raises(HTTPException):
        login.finish(attempt_id, "browser-A", auth, None)


def test_attempt_id_alone_does_not_authorize_usc_sign_in(remote_usc):
    login, connectors, auth = remote_usc
    attempt_id = UUID(login.start("browser-A")["attempt_id"])
    with pytest.raises(HTTPException):
        login.finish(attempt_id, "browser-B", auth, None)
    auth.usc_sign_in.assert_not_called()
    connectors.import_session.assert_not_called()


def test_google_identity_mismatch_cannot_save_provider_sessions(remote_usc):
    login, connectors, auth = remote_usc
    attempt_id = UUID(login.start("browser-A", expected_email="someone_else@usc.edu")["attempt_id"])
    login.finish(attempt_id, "browser-A", auth, None, issue_session=False)
    with pytest.raises(HTTPException, match="different USC accounts"):
        login.finish(attempt_id, "browser-A", auth, None, issue_session=False)
    auth.find_or_create_usc_account.assert_not_called()
    auth.usc_sign_in.assert_not_called()
    connectors.import_session.assert_not_called()


def test_webreg_snapshot_is_saved_with_account_and_no_extra_app_session(remote_usc):
    login, connectors, auth = remote_usc
    auth.find_or_create_usc_account.return_value = auth.usc_sign_in.return_value[0]
    snapshot = {"updated_at": "2026-11-15T00:00:00Z", "terms": [{"term": "Fall 2026", "courses": []}]}
    login.webreg_reader = MagicMock(return_value=snapshot)
    attempt_id = UUID(login.start("browser-A", expected_email=WHOAMI["UniqueName"])["attempt_id"])
    for _ in range(4):
        result, token = login.finish(attempt_id, "browser-A", auth, None, issue_session=False)
    assert result["status"] == "done" and token is None
    login.webreg_reader.assert_called_once()
    captured = next(call.args[2] for call in connectors.import_session.call_args_list if call.args[1] == "brightspace")
    assert captured["webreg_snapshot"] == snapshot
    auth.usc_sign_in.assert_not_called()


def test_successful_usc_auth_returns_connecting_before_provider_setup(remote_usc):
    login, connectors, auth = remote_usc
    attempt_id = UUID(login.start("browser-A")["attempt_id"])
    connectors.runtime.run.reset_mock()
    result, token = login.finish(attempt_id, "browser-A", auth, None)
    assert result["status"] == "connecting" and token is None
    assert [(call.args[0], call.args[1]) for call in connectors.runtime.run.call_args_list] == [("brightspace", "capture")]
    assert "secret-cookie" not in str(result)
    auth.usc_sign_in.assert_not_called()


def test_handshake_waits_in_popup_then_offers_explicit_mfa_fallback(remote_usc, monkeypatch):
    login, connectors, auth = remote_usc
    monkeypatch.setattr("app.services.hosted_usc.time", lambda: 100)
    attempt_id = UUID(login.start("browser-A")["attempt_id"])
    login.finish(attempt_id, "browser-A", auth, None)
    login.finish(attempt_id, "browser-A", auth, None)
    original = connectors.runtime.run.side_effect
    def run(provider, action, **kwargs):
        if provider == "handshake" and action == "capture":
            raise AppError("MFA pending", "login_incomplete", 409)
        return original(provider, action, **kwargs)
    connectors.runtime.run.side_effect = run
    assert login.finish(attempt_id, "browser-A", auth, None)[0]["status"] == "connecting"
    monkeypatch.setattr("app.services.hosted_usc.time", lambda: 121)
    result, token = login.finish(attempt_id, "browser-A", auth, None)
    assert result["status"] == "interaction_required" and token is None
    assert result["connectors"][0]["state"] == "connected"
    assert result["connectors"][1]["state"] == "needs_login"
    auth.usc_sign_in.assert_not_called()


def test_incomplete_usc_login_remains_available_for_mfa(remote_usc):
    login, connectors, auth = remote_usc
    attempt_id = UUID(login.start("browser-A")["attempt_id"])
    connectors.runtime.run.side_effect = AppError("MFA pending", "login_incomplete", 409)
    result, token = login.finish(attempt_id, "browser-A", auth, None)
    assert result["status"] == "waiting" and token is None
    assert login.repository.rows[attempt_id][2] is False
    connectors._browser_factory.return_value.release.assert_not_called()


def test_handshake_can_be_skipped_without_losing_brightspace(remote_usc):
    login, connectors, auth = remote_usc
    attempt_id = UUID(login.start("browser-A")["attempt_id"])
    login.finish(attempt_id, "browser-A", auth, None)
    login.finish(attempt_id, "browser-A", auth, None)
    result, token = login.finish(attempt_id, "browser-A", auth, None, skip_handshake=True)
    assert result["status"] == "done"
    assert [call.args[1] for call in connectors.import_session.call_args_list] == ["brightspace"]


def test_handshake_open_failure_preserves_verified_brightspace(remote_usc):
    login, connectors, auth = remote_usc
    attempt_id = UUID(login.start("browser-A")["attempt_id"])
    original = connectors.runtime.run.side_effect
    def run(provider, action, **kwargs):
        if action == "usc_handshake":
            raise AppError("Navigation unavailable", "connector_failed", 502)
        return original(provider, action, **kwargs)
    connectors.runtime.run.side_effect = run
    login.finish(attempt_id, "browser-A", auth, None)
    result, token = login.finish(attempt_id, "browser-A", auth, None)
    assert result["phase"] == "handshake" and token is None
    assert login.finish(attempt_id, "browser-A", auth, None, skip_handshake=True)[0]["status"] == "done"
    assert [call.args[1] for call in connectors.import_session.call_args_list] == ["brightspace"]


def test_cancel_releases_browser_and_deletes_attempt(remote_usc):
    login, connectors, _ = remote_usc
    attempt_id = UUID(login.start("browser-A")["attempt_id"])
    browser = connectors._browser_factory.return_value
    browser.delete_context.side_effect = AppError("Unavailable", "browser_unavailable", 502)
    with pytest.raises(AppError):
        login.cancel(attempt_id, "browser-A")
    assert attempt_id in login.repository.rows and not login.repository.rows[attempt_id][2]
    browser.delete_context.side_effect = None
    login.cancel(attempt_id, "browser-A")
    assert not login.repository.rows


def test_attempt_repository_sets_private_owner_context_and_claims_atomically():
    from contextlib import contextmanager
    from hashlib import sha256
    from app.repositories.usc_attempts import UscAttemptRepository
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.return_value = (b"encrypted",)
    class Database:
        @contextmanager
        def connection(self):
            connection = MagicMock()
            connection.cursor.return_value = cursor
            yield connection
    attempt_id = uuid4()
    repository = UscAttemptRepository(Database())
    assert repository.claim(attempt_id, "private-cookie") == b"encrypted"
    statements = cursor.execute.call_args_list
    assert "set_config('app.usc_attempt_owner', %s, true)" in statements[0].args[0]
    assert statements[0].args[1] == (sha256(b"private-cookie").hexdigest(),)
    assert "owner_hash = %s AND expires_at > now() AND busy = false" in statements[1].args[0]
    assert "private-cookie" not in repr(statements)
    cursor.fetchone.return_value = None
    with pytest.raises(HTTPException):
        repository.claim(attempt_id, "other-cookie")
