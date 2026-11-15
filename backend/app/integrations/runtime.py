"""One isolated MCP helper process per request. Secrets travel only over stdin."""

import json
import os
from pathlib import Path
import subprocess
import tempfile

from app.config import BACKEND_DIR
from app.errors import AppError


class MCPRuntime:
    def __init__(self):
        self.project = BACKEND_DIR.parent / "integrations" / "handshake"

    def run(self, provider: str, action: str, **payload) -> dict:
        if provider not in {"brightspace", "handshake", "piazza"}:
            raise AppError("This connector is not available.", "connector_unavailable", 400)
        piazza = provider == "piazza" and action in {"tools", "verify", "call"}
        project = self.project.parent / "piazza" if piazza else self.project
        module = "trojanclaw_piazza.bridge" if piazza else "trojanclaw_handshake.bridge"
        python = project / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python.exists():
            raise AppError("The connector runtime is not installed on the server.", "runtime_unavailable", 503)
        env = {key: value for key, value in os.environ.items() if key.upper() in {
            "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "HOME", "USERPROFILE",
            "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR",
        }}
        env["PYTHONIOENCODING"] = "utf-8"
        node_root = BACKEND_DIR.parent / ".runtime"
        candidates = sorted(node_root.glob("node-*-win-x64")) if node_root.exists() else []
        if candidates:
            env["PATH"] = str(candidates[-1]) + os.pathsep + env.get("PATH", "")
        try:
            with tempfile.TemporaryDirectory(prefix="trojanclaw-runtime-") as temporary:
                result = subprocess.run(
                    [str(python), "-m", module],
                    input=json.dumps({"provider": provider, "action": action, **payload}),
                    capture_output=True, text=True, encoding="utf-8", env=env, cwd=temporary, timeout=100,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            data = json.loads(result.stdout)
            if result.returncode or not data.get("ok"):
                if data.get("code") == "login_incomplete":
                    raise AppError("Finish signing in in the remote browser, then try again.", "login_incomplete", 409)
                errors = {
                    "verification_required": ("Handshake requires verification in the remote browser. Your saved connection has been kept.", 409),
                    "session_expired": ("Your Handshake sign-in expired. Reconnect Handshake.", 409),
                    "connector_rate_limited": ("Handshake is limiting requests. Wait a little before trying again.", 429),
                    "connector_unavailable": ("Handshake could not verify this request. Try again later; your saved connection has been kept.", 502),
                }
                code = data.get("code")
                if provider == "handshake" and code in errors:
                    message, status = errors[code]
                    raise AppError(message, code, status)
                raise ValueError
            return data
        except (OSError, ValueError, subprocess.TimeoutExpired):
            raise AppError("The connector could not read your account. Reconnect and try again.", "connector_failed", 502) from None
