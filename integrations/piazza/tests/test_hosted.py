import asyncio
import json
from unittest.mock import patch

from fastmcp.exceptions import ToolError
import pytest
import requests

from trojanclaw_piazza.bridge import execute
from trojanclaw_piazza.server import HostedPiazzaClient, PiazzaSession, READ_TOOLS, session_cookies


def session(value="test-only"):
    return {"origin": "https://piazza.com", "cookies": [
        {"name": "session_id", "value": value, "domain": ".piazza.com"},
        {"name": "piazza_session", "value": "auth-test-only", "domain": ".piazza.com"}]}


def run(action, **kwargs):
    return asyncio.run(execute({"provider": "piazza", "action": action, "session": session(), **kwargs}))


def test_upstream_tools_are_discoverable_without_network_or_cookie_files(tmp_path, monkeypatch):
    monkeypatch.setenv("PIAZZA_COOKIE_FILE", str(tmp_path / "private.json"))
    monkeypatch.setenv("PIAZZA_PASSWORD", "must-not-be-used")
    with patch.object(requests.Session, "request", side_effect=AssertionError("No network for discovery")):
        result = run("tools")
    assert {tool["name"] for tool in result["tools"]} == READ_TOOLS
    assert "get_attachment" not in READ_TOOLS
    assert not list(tmp_path.iterdir())


def test_real_mcp_list_courses_and_verification_use_only_supplied_cookie():
    calls = []
    def respond(self, method, url, **kwargs):
        calls.append((url, json.loads(kwargs["data"]), self.cookies.get_dict(), kwargs))
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({"result": {"id": "user-a", "networks": [
            {"id": "class-a", "course_number": "CSCI 101", "name": "Introduction", "status": "active"}
        ]}}).encode()
        return response
    with patch.object(requests.Session, "request", respond):
        assert run("verify") == {"connected": True}
        result = run("call", tool="list_courses", arguments={})
    assert "CSCI 101" in json.dumps(result)
    assert all(body["method"] == "user.status" and cookies == {"session_id": "test-only", "piazza_session": "auth-test-only"}
               and options["allow_redirects"] is False and options["timeout"] == (10, 25)
               for _, body, cookies, options in calls)


def test_expired_sessions_fail_closed_and_never_start_local_login(tmp_path):
    client = HostedPiazzaClient(session(), str(tmp_path))
    for action in (lambda: client._login(True), lambda: client.save_cookies(),
                   lambda: client.password_login("email", "password"), lambda: client.cookie_login({})):
        with pytest.raises(ToolError, match="Reconnect Piazza"):
            action()
    assert client.login_page is None and client.settings.keep_unread and client.settings.fence_content
    assert not list(tmp_path.iterdir())
    client.close()


@pytest.mark.parametrize("payload", [
    {"origin": "https://evil.piazza.com", "cookies": []},
    {"origin": "https://brightspace.usc.edu", "cookies": []},
    {"origin": "https://piazza.com", "cookies": [{"name": "session_id", "domain": "evil.com", "value": "test"}]},
    session("test;injection"), session("test\r\ninjection"), session(""),
])
def test_wrong_provider_or_malformed_session_is_rejected(payload):
    with pytest.raises(ValueError):
        session_cookies(payload)


@pytest.mark.parametrize("missing", ["session_id", "piazza_session"])
def test_both_piazza_cookies_are_required(missing):
    payload = session()
    payload["cookies"] = [cookie for cookie in payload["cookies"] if cookie["name"] != missing]
    with pytest.raises(ValueError, match="Missing"):
        session_cookies(payload)


def test_piazza_cookie_pair_excludes_tracking_and_other_provider_cookies():
    payload = session()
    payload["cookies"].extend([
        {"name": "AWSALB", "domain": "piazza.com", "value": "excluded"},
        {"name": "session_id", "domain": "brightspace.usc.edu", "value": "excluded"},
    ])
    assert session_cookies(payload) == {"session_id": "test-only", "piazza_session": "auth-test-only"}


@pytest.mark.parametrize("tool", ["get_attachment", "create_post", "password_login"])
def test_unapproved_tools_fail_before_loading_session(tool):
    with pytest.raises(ValueError, match="not allowed"):
        asyncio.run(execute({"provider": "piazza", "action": "call", "tool": tool}))


@pytest.mark.parametrize("url,body", [
    ("https://evil.com/logic/api", {"method": "user.status"}),
    ("https://piazza.com.evil.com/logic/api", {"method": "user.status"}),
    ("https://piazza.com/logic/api", {"method": "content.create"}),
    ("http://piazza.com/logic/api", {"method": "user.status"}),
])
def test_transport_blocks_writes_and_credential_redirects(url, body):
    with pytest.raises(ValueError):
        PiazzaSession().post(url, data=json.dumps(body))


def test_cross_course_access_is_rejected(tmp_path):
    client = HostedPiazzaClient(session(), str(tmp_path))
    client._status = {"id": "user-a", "networks": [{"id": "course-a", "name": "Own course"}]}
    with pytest.raises(ToolError):
        client.resolve("course-b")
    client.close()


def test_full_post_read_restores_only_previously_unread_threads_before_close(tmp_path):
    client = HostedPiazzaClient(session(), str(tmp_path))
    methods = []
    def respond(self, method, url, **kwargs):
        body = json.loads(kwargs["data"])
        methods.append(body)
        result = {"network.filter_feed": {"feed": [{"nr": 1}]},
                  "content.get": {"id": "post-id"}, "content.mark_unread": True}[body["method"]]
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({"result": result}).encode()
        return response
    with patch.object(requests.Session, "request", respond):
        assert client.posts("own-course", [1, 2]) == [{"id": "post-id"}] * 2
        client.close()
    restores = [body for body in methods if body["method"] == "content.mark_unread"]
    assert len(restores) == 1 and restores[0]["params"] == {"nid": "own-course", "cid": "post-id"}
    assert all(not thread.is_alive() for thread in client.restore_threads)


def test_verification_rejects_anonymous_status_and_expired_sessions():
    def respond(self, method, url, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"result":{"networks":[]}}'
        return response
    with patch.object(requests.Session, "request", respond), pytest.raises(Exception):
        run("verify")
