"""Browserbase REST adapter. Never expose API keys or CDP URLs to a browser."""

from urllib.parse import urlparse
from uuid import UUID

import httpx

from app.config import ConfigurationError
from app.errors import AppError


class ManagedBrowser:
    def __init__(self, api_key: str | None, project_id: str | None):
        if not api_key or not project_id:
            raise ConfigurationError("BROWSERBASE_API_KEY and BROWSERBASE_PROJECT_ID are required for connector sign-in.")
        try:
            self.api_key, self.project_id = api_key, str(UUID(project_id))
        except ValueError:
            raise ConfigurationError("BROWSERBASE_PROJECT_ID must be a UUID.") from None

    def _request(self, method: str, path: str, allow_missing=False, **kwargs):
        try:
            with httpx.Client(timeout=30, follow_redirects=False) as client:
                response = client.request(method, "https://api.browserbase.com/v1/" + path,
                                          headers={"X-BB-API-Key": self.api_key}, **kwargs)
                if allow_missing and response.status_code in (404, 410):
                    return {}
                if response.status_code == 402:
                    raise AppError("Remote sign-in is unavailable because Browserbase billing or its usage limit needs attention.", "browser_payment_required", 503)
                response.raise_for_status()
                return response.json() if response.content else {}
        except (httpx.HTTPError, ValueError):
            raise AppError("The remote sign-in browser is unavailable. Please try again.", "browser_unavailable", 502) from None

    def create_context(self) -> str:
        return str(UUID(self._request("POST", "contexts", json={"projectId": self.project_id})["id"]))

    def create_session(self, context_id: str) -> dict:
        return self._request("POST", "sessions", json={
            "projectId": self.project_id, "timeout": 600, "keepAlive": True,
            "browserSettings": {
                "viewport": {"width": 1024, "height": 720},
                "context": {"id": str(UUID(context_id)), "persist": True},
                "recordSession": False, "logSession": False, "solveCaptchas": False,
            },
        })

    def live_url(self, session_id: str) -> str:
        data = self._request("GET", f"sessions/{UUID(session_id)}/debug", params={"expiresIn": 600})
        url = data["debuggerFullscreenUrl"]
        parsed = urlparse(url)
        if (parsed.scheme != "https" or not (parsed.hostname or "").endswith(".browserbase.com")
                or parsed.username or parsed.password or self.api_key in url):
            raise AppError("The remote browser returned an invalid sign-in link.", "browser_unavailable", 502)
        return url

    def release(self, session_id: str) -> None:
        self._request("POST", f"sessions/{UUID(session_id)}", allow_missing=True, json={"projectId": self.project_id, "status": "REQUEST_RELEASE"})

    def delete_context(self, context_id: str) -> None:
        self._request("DELETE", f"contexts/{UUID(context_id)}", allow_missing=True)
