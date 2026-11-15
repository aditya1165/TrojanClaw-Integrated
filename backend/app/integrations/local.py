"""Local-only connector processes, persistent sessions, and login jobs.

The newer browser/MCP dependencies run in the connector's isolated environment,
not the FastAPI environment. No credentials are sent through the HTTP API.
"""

from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import json
import os
from pathlib import Path
import subprocess
from threading import RLock

from app.config import BACKEND_DIR
from app.errors import AppError
from app.integrations.models import IntegrationProvider, IntegrationState, IntegrationStatus


class LocalConnectors:
    def __init__(self):
        self.project = BACKEND_DIR.parent / "integrations" / "handshake"
        self.root = Path(os.environ.get("TROJANCLAW_SESSION_DIR", "").strip() or Path.home() / ".trojanclaw" / "connectors").expanduser().resolve()
        self._states: dict[str, tuple[IntegrationState, str | None]] = {}
        self._lock = RLock()
        self._jobs = ThreadPoolExecutor(max_workers=2, thread_name_prefix="connector-login")

    def statuses(self) -> list[IntegrationStatus]:
        results = []
        with self._lock:
            for provider in IntegrationProvider:
                if provider == IntegrationProvider.piazza:
                    state, message = IntegrationState.unavailable, "Coming soon"
                else:
                    saved = (self.root / provider.value / "cookies.json").exists()
                    state, message = self._states.get(provider.value, (
                        IntegrationState.saved if saved else IntegrationState.not_configured, None,
                    ))
                results.append(IntegrationStatus(provider=provider, state=state,
                                                 transport="stdio", message=message))
        return results

    def _require_provider(self, provider: str) -> None:
        if provider not in {"handshake", "brightspace"}:
            raise AppError("This connector is coming soon.", "connector_unavailable", 400)

    def _run(self, provider: str, action: str, **payload) -> dict:
        self._require_provider(provider)
        python = self.project / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python.exists():
            raise AppError("Install the local connectors first; see the setup guide.", "connector_not_installed", 503)
        env = os.environ.copy()
        env.update({"TROJANCLAW_SESSION_DIR": str(self.root),
                    "HANDSHAKE_DIR": str(self.root / "handshake"),
                    "PYTHONIOENCODING": "utf-8"})
        # Also recognize the optional project-local Node installation.
        node_root = BACKEND_DIR.parent / ".runtime"
        candidates = sorted(node_root.glob("node-*-win-x64")) if node_root.exists() else []
        if candidates:
            env["PATH"] = str(candidates[-1]) + os.pathsep + env.get("PATH", "")
        try:
            process = subprocess.run(
                [str(python), "-m", "trojanclaw_handshake.bridge"],
                input=json.dumps({"provider": provider, "action": action, **payload}),
                capture_output=True, text=True, encoding="utf-8", env=env,
                timeout=365 if action == "connect" else 100,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            result = json.loads(process.stdout)
            if process.returncode or not result.get("ok"):
                login_errors = {
                    "login_closed": "The Handshake window closed before sign-in was confirmed. Reconnect and leave it open until it closes automatically.",
                    "login_timeout": "Handshake did not confirm a signed-in account within five minutes. Reconnect and finish signing in in Chromium.",
                }
                if result.get("code") in login_errors:
                    raise AppError(login_errors[result["code"]], result["code"], 502)
                raise ValueError("Connector failed")
            return result
        except (OSError, subprocess.TimeoutExpired, ValueError):
            raise AppError(
                f"Could not read {provider.title()}. Reconnect and finish signing in. "
                "If the browser does not open, check the connector setup guide.",
                "connector_failed", 502,
            ) from None

    def connect(self, provider: str) -> None:
        self._require_provider(provider)
        with self._lock:
            if self._states.get(provider, (None,))[0] == IntegrationState.connecting:
                return
            self._states[provider] = (IntegrationState.connecting, "Finish signing in in the Chromium window.")
            self._jobs.submit(self._finish_connection, provider)

    def set_state(self, provider: str, state: IntegrationState, message: str | None = None) -> None:
        """Record progress made outside a Connect click (the setup after Sign in with USC)."""
        with self._lock:
            self._states[provider] = (state, message)

    def _finish_connection(self, provider: str) -> None:
        try:
            self._run(provider, "connect")
            state = (IntegrationState.connected, None)
        except AppError as exc:
            state = (IntegrationState.needs_login, exc.message)
        with self._lock:
            self._states[provider] = state

    def verify(self, provider: str) -> None:
        self._require_provider(provider)
        with self._lock:
            if self._states.get(provider, (None,))[0] == IntegrationState.connecting:
                raise AppError("Finish signing in first.", "connector_busy", 409)
        try:
            self._run(provider, "verify")
        except AppError:
            with self._lock:
                self._states[provider] = (IntegrationState.needs_login, "Please reconnect.")
            raise
        with self._lock:
            self._states[provider] = (IntegrationState.connected, None)

    def tools(self, providers: list[str]) -> list[dict]:
        tools = []
        for provider in dict.fromkeys(providers):
            self._require_provider(provider)
            with self._lock:
                state = self._states.get(provider, (None,))[0]
            if state == IntegrationState.connecting:
                raise AppError("Finish signing in before using this connector.", "connector_busy", 409)
            try:
                response = self._run(provider, "tools")
            except AppError:
                with self._lock:
                    self._states[provider] = (IntegrationState.needs_login, "Please reconnect.")
                raise
            for tool in response["tools"]:
                tools.append({**tool, "name": f"{provider}__{tool['name']}"})
        return tools

    def call(self, name: str, arguments: dict, providers: list[str]) -> str:
        provider, separator, tool = name.partition("__")
        if not separator or provider not in providers:
            raise AppError("This connector was not selected for chat.", "connector_forbidden", 403)
        try:
            result = self._run(provider, "call", tool=tool, arguments=arguments)
        except AppError:
            with self._lock:
                self._states[provider] = (IntegrationState.needs_login, "Please reconnect.")
            raise
        return json.dumps(result["content"])[:60000]


@lru_cache(maxsize=1)
def get_local_connectors() -> LocalConnectors:
    return LocalConnectors()
