"""Local-only connector processes, persistent sessions, and login jobs.

The newer browser/MCP dependencies run in the connector's isolated environment,
not the FastAPI environment. No credentials are sent through the HTTP API.

Every TrojanClaw account has its own connector sessions under
<base>/users/<user_id>/ and its own card states, so one account can never use
another account's Brightspace or Handshake session.
"""

from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import json
import os
from pathlib import Path
import subprocess
from threading import RLock
from uuid import UUID

from app.config import BACKEND_DIR
from app.errors import AppError
from app.integrations.models import IntegrationProvider, IntegrationState, IntegrationStatus


def connectors_base() -> Path:
    """TROJANCLAW_SESSION_DIR, else ~/.trojanclaw/connectors."""
    configured = os.environ.get("TROJANCLAW_SESSION_DIR", "").strip()
    return Path(configured or Path.home() / ".trojanclaw" / "connectors").expanduser().resolve()


def user_root(user_id: UUID | str) -> Path:
    """One account's connector folder. The ID is parsed as a UUID, so it can never escape the base folder."""
    return connectors_base() / "users" / str(UUID(str(user_id)))


RESULT_CHARS = 20_000  # about 5,000 tokens per connector result


class LocalConnectors:
    def __init__(self):
        self.project = BACKEND_DIR.parent / "integrations" / "handshake"
        self._states: dict[tuple[str, str], tuple[IntegrationState, str | None]] = {}
        self._lock = RLock()
        self._jobs = ThreadPoolExecutor(max_workers=2, thread_name_prefix="connector-login")

    def statuses(self, user_id: UUID | str) -> list[IntegrationStatus]:
        root, user = user_root(user_id), str(user_id)
        results = []
        with self._lock:
            for provider in IntegrationProvider:
                saved = (root / provider.value / "cookies.json").exists()
                state, message = self._states.get((user, provider.value), (
                    IntegrationState.saved if saved else IntegrationState.not_configured, None,
                ))
                results.append(IntegrationStatus(provider=provider, state=state,
                                                 transport="stdio", message=message))
        return results

    def _require_provider(self, provider: str) -> None:
        if provider not in {"handshake", "brightspace", "piazza"}:
            raise AppError("This connector is coming soon.", "connector_unavailable", 400)

    def _state(self, user_id: UUID | str, provider: str) -> IntegrationState | None:
        with self._lock:
            return self._states.get((str(user_id), provider), (None,))[0]

    def set_state(self, user_id: UUID | str, provider: str, state: IntegrationState, message: str | None = None) -> None:
        with self._lock:
            self._states[(str(user_id), provider)] = (state, message)

    def _run(self, user_id: UUID | str, provider: str, action: str, **payload) -> dict:
        self._require_provider(provider)
        project = self.project.parent / "piazza" if provider == "piazza" else self.project
        module = "trojanclaw_piazza.bridge" if provider == "piazza" else "trojanclaw_handshake.bridge"
        python = project / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python.exists():
            raise AppError("Install the local connectors first; see the setup guide.", "connector_not_installed", 503)
        root = user_root(user_id)
        env = os.environ.copy()
        if provider == "piazza":
            env = {key: value for key, value in env.items() if key.upper() in {
                "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "HOME", "USERPROFILE",
                "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR", "PLAYWRIGHT_BROWSERS_PATH",
            }}
        # The helper reads every session only from these two folders: this account's.
        env.update({"TROJANCLAW_SESSION_DIR": str(root),
                    "HANDSHAKE_DIR": str(root / "handshake"),
                    "PYTHONIOENCODING": "utf-8"})
        # Also recognize the optional project-local Node installation.
        node_root = BACKEND_DIR.parent / ".runtime"
        candidates = sorted(node_root.glob("node-*-win-x64")) if node_root.exists() else []
        if candidates:
            env["PATH"] = str(candidates[-1]) + os.pathsep + env.get("PATH", "")
        try:
            process = subprocess.run(
                [str(python), "-m", module],
                input=json.dumps({"provider": provider, "action": action, **payload}),
                capture_output=True, text=True, encoding="utf-8", env=env,
                timeout=365 if action == "connect" else 100,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            result = json.loads(process.stdout)
            if process.returncode or not result.get("ok"):
                login_errors = {
                    "login_closed": f"The {provider.title()} window closed before sign-in was confirmed. Reconnect and leave it open until it closes automatically.",
                    "login_timeout": f"{provider.title()} did not confirm a signed-in account within five minutes. Reconnect and finish signing in in Chromium.",
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

    def connect(self, user_id: UUID | str, provider: str) -> None:
        self._require_provider(provider)
        with self._lock:
            if self._state(user_id, provider) == IntegrationState.connecting:
                return
            self.set_state(user_id, provider, IntegrationState.connecting, "Finish signing in in the Chromium window.")
            self._jobs.submit(self._finish_connection, user_id, provider)

    def _finish_connection(self, user_id: UUID | str, provider: str) -> None:
        try:
            self._run(user_id, provider, "connect")
            state = (IntegrationState.connected, None)
        except AppError as exc:
            state = (IntegrationState.needs_login, exc.message)
        self.set_state(user_id, provider, *state)

    def verify(self, user_id: UUID | str, provider: str) -> None:
        self._require_provider(provider)
        if self._state(user_id, provider) == IntegrationState.connecting:
            raise AppError("Finish signing in first.", "connector_busy", 409)
        self.check_session(user_id, provider)

    def check_session(self, user_id: UUID | str, provider: str) -> None:
        """Verify a saved session with a read-only call and record the result (no busy check)."""
        try:
            self._run(user_id, provider, "verify")
        except AppError:
            self.set_state(user_id, provider, IntegrationState.needs_login, "Please reconnect.")
            raise
        self.set_state(user_id, provider, IntegrationState.connected)

    def tools(self, user_id: UUID | str, providers: list[str]) -> list[dict]:
        tools = []
        for provider in dict.fromkeys(providers):
            self._require_provider(provider)
            if self._state(user_id, provider) == IntegrationState.connecting:
                raise AppError("Finish signing in before using this connector.", "connector_busy", 409)
            try:
                response = self._run(user_id, provider, "tools")
            except AppError:
                self.set_state(user_id, provider, IntegrationState.needs_login, "Please reconnect.")
                raise
            for tool in response["tools"]:
                tools.append({**tool, "name": f"{provider}__{tool['name']}"})
        return tools

    def call(self, user_id: UUID | str, name: str, arguments: dict, providers: list[str]) -> str:
        provider, separator, tool = name.partition("__")
        if not separator or provider not in providers:
            raise AppError("This connector was not selected for chat.", "connector_forbidden", 403)
        try:
            result = self._run(user_id, provider, "call", tool=tool, arguments=arguments)
        except AppError:
            self.set_state(user_id, provider, IntegrationState.needs_login, "Please reconnect.")
            raise
        text = json.dumps(result["content"])
        if len(text) > RESULT_CHARS:  # every character is re-sent on each later tool round
            text = text[:RESULT_CHARS] + f"\n[Result shortened to {RESULT_CHARS:,} characters. Ask for a narrower range or one course/job for the rest.]"
        return text


@lru_cache(maxsize=1)
def get_local_connectors() -> LocalConnectors:
    return LocalConnectors()
