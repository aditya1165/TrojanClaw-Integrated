import importlib.util
from pathlib import Path
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("replit_launcher", ROOT / "scripts/replit.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_published_origin_replaces_old_ngrok_urls_without_changing_secrets():
    source = {"REPLIT_DEPLOYMENT": "1", "REPLIT_DEV_DOMAIN": "preview.replit.dev",
              "PUBLIC_APP_URL": "https://trojanclaw.replit.app/", "MCP_PUBLIC_URL": "https://old.ngrok.dev",
              "GOOGLE_CALENDAR_REDIRECT_URI": "http://localhost:8000/calendar/callback",
              "CONNECTOR_ENCRYPTION_KEYS": "private-test-key", "CONNECTOR_MODE": "local", "APP_DEBUG": "true"}
    result = launcher.environment(source)
    assert result["GOOGLE_CALENDAR_REDIRECT_URI"] == "https://trojanclaw.replit.app/calendar/callback"
    assert result["GOOGLE_CALENDAR_FRONTEND_URL"] == result["CORS_ORIGINS"] == result["MCP_PUBLIC_URL"] == result["PUBLIC_APP_URL"]
    assert result["SIRI_PUBLIC_URL"] == "https://trojanclaw.replit.app/api"
    assert result["CONNECTOR_ENCRYPTION_KEYS"] == source["CONNECTOR_ENCRYPTION_KEYS"]
    assert result["CONNECTOR_MODE"] == "browserbase" and result["APP_DEBUG"] == "false"
    assert source["CONNECTOR_MODE"] == "local"


def test_preview_uses_preview_domain_even_with_published_url_saved_in_secrets():
    result = launcher.environment({"REPLIT_DEV_DOMAIN": "preview.replit.dev", "PUBLIC_APP_URL": "https://published.replit.app"})
    assert result["PUBLIC_APP_URL"] == "https://preview.replit.dev"
    assert result["GOOGLE_CALENDAR_REDIRECT_URI"] == "https://preview.replit.dev/calendar/callback"


@pytest.mark.parametrize("url", ["", "http://example.com", "https://example.com/api", "https://user:pass@example.com", "https://example.com?key=test"])
def test_published_origin_must_be_explicit_https_origin(url):
    with pytest.raises(ValueError, match="PUBLIC_APP_URL"):
        launcher.environment({"PUBLIC_APP_URL": url, "REPLIT_DEPLOYMENT": "1"})


def test_build_installs_all_helpers_and_builds_frontend_without_browser_downloads(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    monkeypatch.setattr(launcher, "STATE", tmp_path / "runtime")
    monkeypatch.setattr(launcher, "MARKER", tmp_path / "runtime/build.json")
    monkeypatch.setattr(launcher, "fingerprint", lambda: "built-test")
    monkeypatch.setattr(launcher.shutil, "which", lambda name: name)
    monkeypatch.setattr(launcher.subprocess, "check_output", lambda *args, **kwargs: "v22.23.5")
    commands = []
    monkeypatch.setattr(launcher, "checked", lambda args, **kwargs: commands.append(([str(arg) for arg in args], kwargs)))
    launcher.build()
    flat = "\n".join(" ".join(args) for args, _ in commands)
    for project in ("handshake", "piazza", "brightspace", "google-calendar"):
        assert str(tmp_path / "integrations" / project) in flat
    assert "--locked --no-dev" in flat
    assert "--include=dev" in flat
    assert "patchright install" not in flat and "playwright install" not in flat
    assert all(options["env"]["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] == "1" for _, options in commands if "env" in options)
    assert launcher.MARKER.exists()


def test_replit_configuration_has_one_public_port_and_separate_build_start():
    config = tomllib.loads((ROOT / ".replit").read_text())
    assert config["ports"] == [{"localPort": 5000, "externalPort": 80}]
    assert config["deployment"]["build"][-1] == "build"
    assert config["deployment"]["run"][-1] == "start"
    assert config["deployment"]["deploymentTarget"] == "vm"
