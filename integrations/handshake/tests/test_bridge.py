import asyncio
import json
from types import SimpleNamespace

import pytest

from trojanclaw_handshake import bridge, profiles


def test_blank_session_directory_uses_home_default(monkeypatch):
    from pathlib import Path

    monkeypatch.setenv("TROJANCLAW_SESSION_DIR", "")
    assert profiles.session_root() == (Path.home() / ".trojanclaw" / "connectors").resolve()


def test_brightspace_session_is_separate_and_supplies_only_required_cookies(monkeypatch, tmp_path):
    monkeypatch.setenv("TROJANCLAW_SESSION_DIR", str(tmp_path))
    profiles.save_brightspace_cookies([
        {"name": "d2lSessionVal", "value": "first"},
        {"name": "d2lSecureSessionVal", "value": "second"},
        {"name": "unrelated", "value": "private"},
    ])
    assert profiles.brightspace_cookie_header() == "d2lSessionVal=first; d2lSecureSessionVal=second"
    assert not (tmp_path / "handshake").exists()


def test_brightspace_browser_reuses_persistent_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("TROJANCLAW_SESSION_DIR", str(tmp_path))
    launches = []

    class Context:
        pages = [SimpleNamespace(url="https://brightspace.usc.edu/d2l/home")]

        async def cookies(self, urls):
            return [{"name": "d2lSessionVal", "value": "first"},
                    {"name": "d2lSecureSessionVal", "value": "second"}]

        async def close(self):
            pass

    async def goto(url, **kwargs):
        assert url == "https://brightspace.usc.edu"
    Context.pages[0].goto = goto

    class Browser:
        async def launch_persistent_context(self, **kwargs):
            launches.append(kwargs)
            return Context()

    class Playwright:
        async def __aenter__(self):
            return SimpleNamespace(chromium=Browser())

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr("patchright.async_api.async_playwright", Playwright)
    asyncio.run(profiles.login_brightspace())
    asyncio.run(profiles.login_brightspace())
    assert launches == [{"user_data_dir": str(tmp_path / "brightspace" / "profile"), "headless": False}] * 2
    assert profiles.brightspace_cookie_header().startswith("d2lSessionVal=")


@pytest.mark.parametrize("provider,tool,args", [
    ("handshake", "hs_apply", {}), ("brightspace", "sign_in", {}),
    ("brightspace", "get_roster", {"includeStudents": True}),
    ("brightspace", "get_syllabus", {"outputPath": "file.pdf"}),
    ("brightspace", "download_file", {}), ("piazza", "get_post", {}),
])
def test_unapproved_calls_are_rejected_before_dispatch(provider, tool, args):
    with pytest.raises(ValueError):
        bridge.validate_call(provider, tool, args)


def test_brightspace_json_auth_errors_cannot_mark_session_connected():
    result = SimpleNamespace(content=[SimpleNamespace(
        type="text", model_dump=lambda **kwargs: {"type": "text", "text": json.dumps({"error": "expired"})},
    )])
    with pytest.raises(RuntimeError):
        bridge.validate_result(result)
