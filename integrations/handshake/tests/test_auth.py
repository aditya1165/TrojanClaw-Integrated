"""Verify persistent-browser authentication wiring without opening a browser."""

import json
import asyncio
import sys
from types import SimpleNamespace

import pytest

import trojanclaw_handshake as connector
from trojanclaw_handshake import profiles


def test_login_detects_authenticated_popup_on_any_dashboard_route(monkeypatch, tmp_path):
    monkeypatch.setenv("HANDSHAKE_DIR", str(tmp_path))
    launches = []

    class Page:
        def __init__(self, url, authenticated):
            self.url, self.authenticated = url, authenticated

        async def goto(self, *args, **kwargs):
            pass

        async def evaluate(self, script):
            assert "currentUser { id }" in script
            return self.authenticated

    class Context:
        pages = [Page("https://app.joinhandshake.com/access", False),
                 Page("https://usc.joinhandshake.com/new-dashboard", True)]
        closed = False

        async def cookies(self, urls):
            assert urls == ["https://usc.joinhandshake.com"]
            return [{"name": "session", "value": "test-value"}]

        async def close(self):
            self.closed = True

    context = Context()

    class Browser:
        async def launch_persistent_context(self, **kwargs):
            launches.append(kwargs)
            return context

    class Playwright:
        async def __aenter__(self):
            return SimpleNamespace(chromium=Browser())

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr("patchright.async_api.async_playwright", Playwright)
    asyncio.run(profiles.login_handshake())
    assert json.loads((tmp_path / "cookies.json").read_text()) == [{"name": "session", "value": "test-value"}]
    assert launches == [{"user_data_dir": str(tmp_path / "profile"), "headless": False}]
    assert context.closed
    assert json.loads((tmp_path / "origin.json").read_text()) == {"origin": "https://usc.joinhandshake.com"}


def test_mcp_api_uses_saved_school_origin(monkeypatch, tmp_path):
    monkeypatch.setenv("HANDSHAKE_DIR", str(tmp_path))
    (tmp_path / "origin.json").write_text('{"origin": "https://usc.joinhandshake.com"}')
    profiles.configure_handshake_api()
    assert connector.handshake_client.GQL_URL == "https://usc.joinhandshake.com/hs/graphql"
    assert connector.handshake_client.API_BASE == "https://usc.joinhandshake.com/stu"
    assert connector.handshake_client.BASE_URL == "https://usc.joinhandshake.com"


@pytest.mark.parametrize("url", [
    "http://usc.joinhandshake.com", "https://joinhandshake.com.example.org",
    "https://usc.joinhandshake.com:8443", "https://user:pass@usc.joinhandshake.com",
])
def test_handshake_origin_rejects_untrusted_urls(url):
    assert profiles.handshake_origin(url) is None


def test_dashboard_url_alone_does_not_prove_authentication():
    class Page:
        url = "https://app.joinhandshake.com/explore"

        async def evaluate(self, script):
            return False

    assert asyncio.run(profiles.handshake_page_authenticated(Page())) is False


def test_browser_cloudflare_challenge_is_not_reported_as_bad_credentials():
    class Page:
        url = 'https://usc.joinhandshake.com/explore'
        async def evaluate(self, script): return 'verification_required'
    with pytest.raises(profiles.LoginError) as error:
        asyncio.run(profiles.handshake_page_authenticated(Page()))
    assert error.value.code == 'verification_required'


def test_unrelated_hostname_is_not_probed():
    class Page:
        url = "https://app.joinhandshake.com.example.org/explore"

        async def evaluate(self, script):
            pytest.fail("Do not send authenticated requests to other hosts")

    assert asyncio.run(profiles.handshake_page_authenticated(Page())) is False


@pytest.mark.parametrize("action", ["login", "logout"])
def test_auth_actions_delegate_persistent_browser(monkeypatch, action):
    called = []

    async def fake_action():
        called.append(action)

    monkeypatch.setattr(connector, f"run_{action}", fake_action)
    monkeypatch.setattr(sys, "argv", ["trojanclaw-handshake", action])
    connector.main()
    assert called == [action]


def test_saved_browser_session_is_reused_after_server_creation(monkeypatch, tmp_path):
    path = tmp_path / "cookies.json"
    path.write_text(json.dumps([
        {"name": "session", "value": "test-persistent-session"},
    ]), encoding="utf-8")
    monkeypatch.setattr("handshake_mcp.auth.COOKIES_FILE", path)
    connector.create_server()
    assert connector.handshake_client.load_cookies() == {"session": "test-persistent-session"}
    connector.create_server()
    assert connector.handshake_client.load_cookies() == {"session": "test-persistent-session"}


def test_status_verifies_saved_session(monkeypatch, capsys):
    async def fake_gql(query):
        assert query == "{ currentUser { id } }"
        return {"currentUser": {"id": "test-user"}}

    monkeypatch.setattr(connector.handshake_client, "gql", fake_gql)
    monkeypatch.setattr(sys, "argv", ["trojanclaw-handshake", "status"])
    connector.main()
    assert "authentication verified" in capsys.readouterr().out


@pytest.mark.parametrize("data", [None, {}, {"currentUser": None}])
def test_status_rejects_unverified_session(monkeypatch, data):
    async def fake_gql(query):
        return data

    monkeypatch.setattr(connector.handshake_client, "gql", fake_gql)
    monkeypatch.setattr(sys, "argv", ["trojanclaw-handshake", "status"])
    with pytest.raises(SystemExit) as result:
        connector.main()
    assert result.value.code == 1


def test_auth_failure_does_not_disclose_upstream_details(monkeypatch, capsys):
    async def fake_gql(query):
        raise RuntimeError("private-session-value")

    monkeypatch.setattr(connector.handshake_client, "gql", fake_gql)
    monkeypatch.setattr(sys, "argv", ["trojanclaw-handshake", "status"])
    with pytest.raises(SystemExit):
        connector.main()
    error = capsys.readouterr().err
    assert "private-session-value" not in error
    assert "trojanclaw-handshake login" in error
