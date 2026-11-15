"""One-port ASGI host for the compiled website, API, OAuth callbacks and remote MCP."""

from pathlib import Path

from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.middleware.exceptions import ExceptionMiddleware
from starlette.staticfiles import StaticFiles


class Website(StaticFiles):
    async def get_response(self, path, scope):
        path = path.replace("\\", "/")
        # SPA navigation may fall back to index.html. Missing assets and hidden
        # paths must stay 404 rather than returning HTML for a script request.
        if any(part.startswith(".") for part in Path(path).parts if part != "."):
            raise HTTPException(404)
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or Path(path).suffix or path.startswith("assets/"):
                raise
            response = await super().get_response("index.html", scope)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' data: https://fonts.gstatic.com; img-src 'self' data: https:; connect-src 'self'; "
            "frame-src https://*.browserbase.com; object-src 'none'; base-uri 'self'; form-action 'self'"
        )
        return response


class HostedApp:
    def __init__(self, api, directory):
        self.api = api
        self.website = ExceptionMiddleware(Website(directory=directory, html=True))

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            # The original FastAPI lifespan starts and stops the remote MCP SDK.
            return await self.api(scope, receive, send)
        path = scope.get("path", "")
        is_api = path == "/api" or path.startswith("/api/")
        if is_api:
            scope = {**scope, "path": path[4:] or "/", "raw_path": scope.get("raw_path", path.encode())[4:] or b"/"}
        direct = (path in {"/auth/google/callback", "/calendar/callback", "/healthcheck", "/ready",
                          "/authorize", "/token", "/register", "/revoke", "/mcp"}
                  or path.startswith(("/.well-known/", "/mcp/")))

        async def send_private_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("Referrer-Policy", "no-referrer")
                headers.setdefault("X-Content-Type-Options", "nosniff")
            await send(message)

        target = self.api if is_api or direct else self.website
        await target(scope, receive, send_private_headers)


def create_app():
    from app.main import app
    directory = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if not (directory / "index.html").is_file():
        raise RuntimeError("Build the website first: python3 scripts/replit.py build")
    return HostedApp(app, directory)
