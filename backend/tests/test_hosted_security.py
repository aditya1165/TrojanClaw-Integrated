from contextlib import contextmanager
from dataclasses import replace
import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, Request

from app.api.routes import auth
from app.config import ConfigurationError, Settings, get_settings
from app.errors import AppError
from app.integrations.browserbase import ManagedBrowser
from app.integrations.runtime import MCPRuntime
from app.repositories.connectors import ConnectorRepository


def test_provider_recording_logs_and_automatic_captcha_solving_are_disabled():
    browser = ManagedBrowser("test-api-key", str(uuid4()))
    browser._request = MagicMock(return_value={})
    browser.create_session(str(uuid4()))
    settings = browser._request.call_args.kwargs["json"]["browserSettings"]
    assert settings["recordSession"] is False
    assert settings["logSession"] is False
    assert settings["solveCaptchas"] is False
    assert settings["context"]["persist"] is True


def test_live_view_cannot_expose_project_api_key():
    browser = ManagedBrowser("test-api-key", str(uuid4()))
    browser._request = MagicMock(return_value={"debuggerFullscreenUrl": "https://www.browserbase.com/live?apiKey=test-api-key"})
    with pytest.raises(AppError):
        browser.live_url(str(uuid4()))


@pytest.mark.parametrize("provider,action,piazza_runtime", [
    ("handshake", "verify", False), ("piazza", "verify", True),
    ("piazza", "tools", True), ("piazza", "call", True), ("piazza", "prepare", False),
])
def test_mcp_subprocess_has_no_server_secrets_or_cookie_arguments(monkeypatch, tmp_path, provider, action, piazza_runtime):
    runtime = MCPRuntime()
    runtime.project = tmp_path / "handshake"
    project = tmp_path / "piazza" if piazza_runtime else runtime.project
    python = project / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setenv("BROWSERBASE_API_KEY", "server-browser-key")
    monkeypatch.setenv("CONNECTOR_ENCRYPTION_KEYS", "server-encryption-key")
    monkeypatch.setenv("DATABASE_URL", "server-database-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "server-model-key")
    run = MagicMock(return_value=SimpleNamespace(stdout='{"ok":true,"connected":true}', returncode=0))
    monkeypatch.setattr("app.integrations.runtime.subprocess.run", run)
    runtime.run(provider, action, session={"cookies": "user-cookie"})
    assert run.call_args.args[0] == [str(python), "-m", "trojanclaw_piazza.bridge" if piazza_runtime else "trojanclaw_handshake.bridge"]
    assert "user-cookie" not in repr(run.call_args.args)
    assert "user-cookie" in run.call_args.kwargs["input"]
    assert all(key not in run.call_args.kwargs["env"] for key in (
        "BROWSERBASE_API_KEY", "CONNECTOR_ENCRYPTION_KEYS", "DATABASE_URL", "ANTHROPIC_API_KEY"))


@pytest.mark.parametrize('code,status', [
    ('verification_required', 409), ('session_expired', 409),
    ('connector_rate_limited', 429), ('connector_unavailable', 502),
])
def test_handshake_runtime_preserves_classified_errors(monkeypatch, tmp_path, code, status):
    runtime = MCPRuntime()
    runtime.project = tmp_path
    python = tmp_path / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr('app.integrations.runtime.subprocess.run', lambda *args, **kwargs: SimpleNamespace(
        stdout=json.dumps({'ok':False, 'code':code, 'error':'untrusted upstream details'}), returncode=1))
    with pytest.raises(AppError) as error:
        runtime.run('handshake', 'verify', session={})
    assert error.value.code == code and error.value.status_code == status
    assert 'untrusted' not in str(error.value)


def test_repository_sets_transaction_owner_and_filters_every_account_query():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    class Database:
        @contextmanager
        def connection(self):
            connection = MagicMock()
            connection.cursor.return_value = cursor
            yield connection
    repository = ConnectorRepository(Database())
    user = UUID(int=2)
    repository.get(user, "handshake")
    repository.list(user)
    for call in cursor.execute.call_args_list:
        sql, values = call.args
        assert str(user) in values
        if "user_connector_sessions" in sql:
            assert "WHERE user_id = %s" in sql
        else:
            assert "set_config('app.connector_user_id', %s, true)" in sql


def test_production_rejects_debug_and_non_https_origin(monkeypatch):
    settings = replace(get_settings(), app_env="production", connector_mode="browserbase", public_app_url="http://app.example.com")
    with pytest.raises(ConfigurationError): settings.validate()
    settings = replace(settings, public_app_url="https://app.example.com", cors_origins=["https://app.example.com"],
        google_calendar_frontend_url="https://app.example.com", google_calendar_redirect_uri="https://app.example.com/calendar/callback", debug=True)
    with pytest.raises(ConfigurationError): settings.validate()
    replace(settings, debug=False).validate()


def test_configuration_repr_does_not_expose_keys():
    settings = replace(get_settings(), browserbase_api_key="private-browser-key", connector_encryption_keys="private-encryption-key")
    assert "private-browser-key" not in repr(settings)
    assert "private-encryption-key" not in repr(settings)


def test_production_unsafe_cookie_requests_require_origin(monkeypatch):
    settings = replace(get_settings(), app_env="production")
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    with pytest.raises(HTTPException) as error:
        auth.require_trusted_origin(Request({"type": "http", "method": "POST", "headers": []}))
    assert error.value.status_code == 403


def test_production_auth_rate_limit_is_shared_in_database(monkeypatch):
    settings = replace(get_settings(), app_env="production")
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.return_value = (21,)
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    monkeypatch.setattr(auth, "get_database", lambda: SimpleNamespace(connection=lambda: connection))
    request = Request({"type": "http", "method": "POST", "headers": [], "client": ("203.0.113.10", 1234)})
    with pytest.raises(HTTPException) as error:
        auth.AuthRateLimiter().check(request)
    assert error.value.status_code == 429
    assert "ON CONFLICT" in cursor.execute.call_args_list[0].args[0]
    assert "203.0.113.10" not in repr(cursor.execute.call_args_list[0].args[1])
