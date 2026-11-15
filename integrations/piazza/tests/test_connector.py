import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

from fastmcp.exceptions import ToolError
import pytest
import requests

from trojanclaw_piazza import browser
from trojanclaw_piazza.bridge import execute
from trojanclaw_piazza.server import AccountClient, PiazzaSession, READ_TOOLS, directory, save_cookies, saved_cookies, session_cookies


def cookies():
    return [{'name':name,'value':'test-' + name,'domain':'.piazza.com'} for name in ('session_id','piazza_session')]


@pytest.fixture(autouse=True)
def account(monkeypatch, tmp_path):
    monkeypatch.setenv('TROJANCLAW_SESSION_DIR', str(tmp_path / 'user-a'))
    monkeypatch.setenv('PIAZZA_COOKIE_FILE', str(tmp_path / 'must-not-read.json'))
    monkeypatch.setenv('PIAZZA_PASSWORD', 'must-not-use')


def test_discovery_uses_actual_upstream_tools_without_network():
    save_cookies(cookies())
    with patch.object(requests.Session, 'request', side_effect=AssertionError('No network during discovery')):
        result = asyncio.run(execute({'provider':'piazza','action':'tools'}))
    assert {tool['name'] for tool in result['tools']} == READ_TOOLS
    assert 'get_attachment' not in READ_TOOLS


def test_real_mcp_verification_requires_both_cookies():
    save_cookies(cookies())
    def respond(self, method, url, **kwargs):
        assert self.cookies.get_dict() == {c['name']:c['value'] for c in cookies()}
        assert kwargs['timeout'] == (10, 25) and kwargs['allow_redirects'] is False
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"result":{"id":"user-a","networks":[]}}'
        return response
    with patch.object(requests.Session, 'request', respond):
        assert asyncio.run(execute({'provider':'piazza','action':'verify'})) == {'connected':True}


def test_accounts_do_not_share_saved_cookies_or_profiles(monkeypatch, tmp_path):
    save_cookies(cookies())
    first = directory()
    monkeypatch.setenv('TROJANCLAW_SESSION_DIR', str(tmp_path / 'user-b'))
    assert directory() != first
    with pytest.raises(FileNotFoundError): saved_cookies()
    with pytest.raises(FileNotFoundError):
        asyncio.run(execute({'provider':'piazza','action':'verify'}))


@pytest.mark.parametrize('missing', ['session_id','piazza_session'])
def test_cookie_pair_required(missing):
    with pytest.raises(ValueError): session_cookies([c for c in cookies() if c['name'] != missing])


def test_unrelated_domains_and_tracking_are_excluded():
    extras = [{'name':'session_id','value':'excluded','domain':'other.com'},
              {'name':'AWSALB','value':'excluded','domain':'piazza.com'}]
    assert session_cookies(cookies() + extras) == {c['name']:c['value'] for c in cookies()}


def test_no_password_login_or_global_cookie_fallback():
    client = AccountClient(cookies())
    for action in (lambda: client._login(True), lambda: client.save_cookies(),
                   lambda: client.password_login('email','password'), lambda: client.cookie_login({})):
        with pytest.raises(ToolError): action()
    assert client.login_page is None and client.settings.keep_unread and client.settings.fence_content
    client.close()


@pytest.mark.parametrize('tool', ['create_post','get_attachment','password_login'])
def test_forbidden_tools_fail_before_loading_any_cookies(tool):
    with pytest.raises(ValueError):
        asyncio.run(execute({'provider':'piazza','action':'call','tool':tool}))


@pytest.mark.parametrize('url,method', [('https://evil.com/logic/api','user.status'),
                                      ('https://piazza.com/logic/api','content.create')])
def test_transport_blocks_writes_and_foreign_hosts(url, method):
    with pytest.raises(ValueError): PiazzaSession().post(url, data=json.dumps({'method':method}))


def test_visible_browser_reuses_only_account_profile_and_saves_verified_pair(monkeypatch):
    launches = []
    verified = []
    class Page:
        url = 'https://piazza.com/class/course-a'
        async def goto(self, url, **kwargs): assert url == 'https://piazza.com/account/login'
    class Context:
        pages = [Page()]
        async def cookies(self, urls): return cookies() + [{'name':'tracking','value':'excluded'}]
        async def close(self): pass
    class Chromium:
        async def launch_persistent_context(self, **kwargs):
            launches.append(kwargs)
            return Context()
    class Playwright:
        async def __aenter__(self): return SimpleNamespace(chromium=Chromium())
        async def __aexit__(self, *args): pass
    monkeypatch.setattr('patchright.async_api.async_playwright', Playwright)
    monkeypatch.setattr(browser, 'verified', lambda values: verified.append(values))
    asyncio.run(browser.login())
    assert launches == [{'user_data_dir':str(directory() / 'profile'), 'headless':False}]
    assert verified and session_cookies(saved_cookies()) == {c['name']:c['value'] for c in cookies()}
