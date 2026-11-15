import asyncio
from types import SimpleNamespace

import pytest

from trojanclaw_handshake import bridge
from trojanclaw_handshake.remote_login import remote_login, validate_connect_url
from trojanclaw_handshake.session import session_cookies


@pytest.mark.parametrize("url", ["ws://connect.browserbase.com", "wss://evil.example", "wss://connect.browserbase.com.example.org", "wss://user:pass@connect.browserbase.com"])
def test_cdp_cannot_connect_to_unapproved_hosts(url):
    with pytest.raises(ValueError):
        validate_connect_url(url)


def test_hosted_bridge_never_falls_back_to_owner_files(monkeypatch):
    monkeypatch.setattr(bridge, "client_for", lambda *args: pytest.fail("A missing account session must not reach MCP"))
    with pytest.raises(KeyError):
        asyncio.run(bridge.execute({"provider": "handshake", "action": "tools"}))


def test_provider_cookie_isolation_and_header_validation():
    session = {"origin": "https://usc.joinhandshake.com", "cookies": [
        {"domain": "usc.joinhandshake.com", "name": "session", "value": "own"},
        {"domain": "other.joinhandshake.com", "name": "session", "value": "other"},
    ]}
    assert session_cookies(session, "handshake") == {"session": "own"}
    with pytest.raises(ValueError):
        session_cookies(session, "brightspace")
    session["cookies"][0]["value"] = "bad\r\nheader"
    with pytest.raises(ValueError):
        session_cookies(session, "handshake")


def test_remote_capture_returns_school_cookies_without_writing_files(monkeypatch, tmp_path):
    class Page:
        url = "https://usc.joinhandshake.com/explore"
        async def evaluate(self, script): return True
    class Context:
        pages = [Page()]
        async def cookies(self, urls):
            assert urls == ["https://usc.joinhandshake.com"]
            return [{"domain": "usc.joinhandshake.com", "name": "session", "value": "private"}]
    class Browser:
        contexts = [Context()]
        async def close(self): pass
    class Chromium:
        async def connect_over_cdp(self, url): return Browser()
    class Playwright:
        async def __aenter__(self): return SimpleNamespace(chromium=Chromium())
        async def __aexit__(self, *args): pass
    monkeypatch.setattr("patchright.async_api.async_playwright", Playwright)
    monkeypatch.chdir(tmp_path)
    result = asyncio.run(remote_login("handshake", "capture", "wss://connect.browserbase.com?key=test"))
    assert result["session"]["origin"] == "https://usc.joinhandshake.com"
    assert result["session"]["cookies"][0]["value"] == "private"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("url,complete", [
    ("https://piazza.com/class/own-course", True),
    ("https://piazza.com/account/login", True),
    ("https://piazza.com.evil.com/class/own-course", False),
    ("http://piazza.com/class/own-course", False),
])
def test_piazza_capture_requires_dashboard_and_keeps_only_its_session_cookie(monkeypatch, url, complete):
    class Context:
        pages = [SimpleNamespace(url=url)]
        async def cookies(self, urls):
            assert urls == ["https://piazza.com"]
            return [{"name": "session_id", "value": "piazza-test", "domain": ".piazza.com"},
                    {"name": "piazza_session", "value": "auth-test", "domain": ".piazza.com"},
                    {"name": "tracking", "value": "excluded", "domain": ".piazza.com"}]
    class Browser:
        contexts = [Context()]
        async def close(self): pass
    class Chromium:
        async def connect_over_cdp(self, url): return Browser()
    class Playwright:
        async def __aenter__(self): return SimpleNamespace(chromium=Chromium())
        async def __aexit__(self, *args): pass
    monkeypatch.setattr("patchright.async_api.async_playwright", Playwright)
    from trojanclaw_handshake.profiles import LoginError
    if not complete:
        with pytest.raises(LoginError):
            asyncio.run(remote_login("piazza", "capture", "wss://connect.browserbase.com"))
    else:
        result = asyncio.run(bridge.execute({"provider": "piazza", "action": "capture", "connect_url": "wss://connect.browserbase.com"}))
        assert result["session"] == {"origin": "https://piazza.com", "cookies": [
            {"name": "session_id", "value": "piazza-test", "domain": ".piazza.com"},
            {"name": "piazza_session", "value": "auth-test", "domain": ".piazza.com"}]}


def test_seed_copies_only_the_selected_provider_into_a_new_context(monkeypatch):
    added = []
    class Context:
        async def add_cookies(self, cookies): added.extend(cookies)
    class Browser:
        contexts = [Context()]
        async def close(self): pass
    class Chromium:
        async def connect_over_cdp(self, url): return Browser()
    class Playwright:
        async def __aenter__(self): return SimpleNamespace(chromium=Chromium())
        async def __aexit__(self, *args): pass
    monkeypatch.setattr("patchright.async_api.async_playwright", Playwright)
    session = {"origin": "https://usc.joinhandshake.com", "cookies": [
        {"domain": "usc.joinhandshake.com", "name": "own", "value": "own-cookie"},
        {"domain": "login.usc.edu", "name": "sso", "value": "sso-cookie"},
        {"domain": "brightspace.usc.edu", "name": "d2lSessionVal", "value": "brightspace-cookie"},
    ]}
    asyncio.run(remote_login("handshake", "seed", "wss://connect.browserbase.com", session=session))
    assert added == [{"name": "own", "value": "own-cookie", "url": "https://usc.joinhandshake.com", "secure": True}]


def test_remote_identity_is_read_from_fixed_brightspace_api(monkeypatch):
    urls = []
    class Response:
        ok = True
        def __init__(self, data): self.data = data
        async def json(self): return self.data
    class Request:
        async def get(self, url):
            urls.append(url)
            return Response({"LatestVersion": "1.50"} if len(urls) == 1 else {"Identifier": "12345"})
    class Context:
        pages = [SimpleNamespace(url="https://brightspace.usc.edu/d2l/home", request=Request())]
    class Browser:
        contexts = [Context()]
        async def close(self): pass
    class Chromium:
        async def connect_over_cdp(self, url): return Browser()
    class Playwright:
        async def __aenter__(self): return SimpleNamespace(chromium=Chromium())
        async def __aexit__(self, *args): pass
    monkeypatch.setattr("patchright.async_api.async_playwright", Playwright)
    result = asyncio.run(remote_login("brightspace", "identity", "wss://connect.browserbase.com"))
    assert result == {"whoami": {"Identifier": "12345"}}
    assert urls == ["https://brightspace.usc.edu/d2l/api/lp/versions/", "https://brightspace.usc.edu/d2l/api/lp/1.50/users/whoami"]


@pytest.mark.parametrize("provider,action,expected", [
    ("piazza", "prepare", "https://piazza.com/account/login"),
    ("handshake", "prepare", "https://usc.joinhandshake.com/auth/saml/70/session/new?redirect_to_idp=true"),
    ("brightspace", "usc_prepare", "https://brightspace.usc.edu/d2l/login"),
    ("handshake", "usc_handshake", "https://usc.joinhandshake.com/auth/saml/70/session/new?redirect_to_idp=true"),
])
def test_usc_setup_uses_direct_school_sign_in_urls(monkeypatch, provider, action, expected):
    visited = []
    class Page:
        async def goto(self, url, **kwargs): visited.append(url)
        async def bring_to_front(self): pass
    class Context:
        pages = [Page()]
        async def new_page(self): pytest.fail('Reuse the live-view tab and its USC authentication')
    class Browser:
        contexts = [Context()]
        async def close(self): pass
    class Chromium:
        async def connect_over_cdp(self, url): return Browser()
    class Playwright:
        async def __aenter__(self): return SimpleNamespace(chromium=Chromium())
        async def __aexit__(self, *args): pass
    monkeypatch.setattr("patchright.async_api.async_playwright", Playwright)
    asyncio.run(remote_login(provider, action, "wss://connect.browserbase.com"))
    assert visited == [expected]
