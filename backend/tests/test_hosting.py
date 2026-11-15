from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
import pytest
from starlette.responses import StreamingResponse

from app.hosting import HostedApp


@pytest.fixture
def hosted_site(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>TrojanClaw website</html>")
    (tmp_path / "assets/app.js").write_text("console.log('test')")
    lifecycle = []

    @asynccontextmanager
    async def lifespan(api):
        lifecycle.append("start")
        yield
        lifecycle.append("stop")

    api = FastAPI(lifespan=lifespan)

    @api.get("/")
    def info():
        return {"api": True}

    @api.post("/auth/google/start")
    def start(response: Response):
        response.set_cookie("attempt", "test-only", path="/", httponly=True, secure=True, samesite="lax")
        return {"started": True}

    @api.get("/auth/google/callback")
    @api.get("/calendar/callback")
    @api.get("/.well-known/oauth-authorization-server")
    @api.get("/healthcheck")
    def callback(request: Request):
        return {"path": request.url.path, "query": request.url.query, "cookie": request.cookies.get("attempt")}

    @api.post("/mcp")
    def mcp():
        async def events():
            yield "event: message\ndata: test-only\n\n"
        return StreamingResponse(events(), media_type="text/event-stream")

    return HostedApp(api, tmp_path), lifecycle


def test_website_api_and_lifespan_share_one_host(hosted_site):
    app, lifecycle = hosted_site
    with TestClient(app) as client:
        assert lifecycle == ["start"]
        assert "TrojanClaw website" in client.get("/").text
        assert client.get("/api/").json() == {"api": True}
        assert "TrojanClaw website" in client.get("/chat/conversation-id").text
    assert lifecycle == ["start", "stop"]


def test_google_cookie_survives_api_prefix_and_direct_callback(hosted_site):
    with TestClient(hosted_site[0], base_url="https://example.replit.app") as client:
        start = client.post("/api/auth/google/start")
        assert "Path=/;" in start.headers["set-cookie"]
        assert "HttpOnly" in start.headers["set-cookie"]
        callback = client.get("/auth/google/callback?code=test-only&state=test-state")
        assert callback.json() == {"path": "/auth/google/callback", "query": "code=test-only&state=test-state", "cookie": "test-only"}
        assert callback.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize("path", ["/calendar/callback", "/.well-known/oauth-authorization-server", "/healthcheck"])
def test_root_backend_endpoints_are_not_masked_by_spa(hosted_site, path):
    with TestClient(hosted_site[0]) as client:
        assert client.get(path).json()["path"] == path


@pytest.mark.parametrize("path", ["/api/not-a-route", "/assets/missing.js", "/.env", "/missing.css"])
def test_missing_api_assets_and_private_files_do_not_serve_spa(hosted_site, path):
    with TestClient(hosted_site[0]) as client:
        response = client.get(path)
        assert response.status_code == 404
        assert "TrojanClaw website" not in response.text


def test_static_cache_and_browserbase_frame_policy(hosted_site):
    with TestClient(hosted_site[0]) as client:
        index = client.get("/")
        asset = client.get("/assets/app.js")
        assert index.headers["cache-control"] == "no-cache"
        assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert "frame-src https://*.browserbase.com" in index.headers["content-security-policy"]


def test_mcp_streaming_remains_available_at_root_and_api_alias(hosted_site):
    with TestClient(hosted_site[0]) as client:
        for path in ("/mcp", "/api/mcp"):
            with client.stream("POST", path) as response:
                assert response.headers["content-type"].startswith("text/event-stream")
                assert "event: message" in "".join(response.iter_text())
