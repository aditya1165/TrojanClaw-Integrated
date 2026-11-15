"""Replit: build, validate secrets, and serve the website plus API on one port.

Run from any directory: python3 scripts/replit.py [run|build|start|check]
No database migrations are applied and no secrets are generated or printed.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".runtime" / "replit"
MARKER = STATE / "build.json"
UV_VERSION = "0.11.16"


def python_in(folder):
    return folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def environment(source):
    env = dict(source)
    # Preview and publishing have different origins. Never send preview OAuth
    # callbacks to the published site, even if its URL is saved in Secrets.
    preview = env.get("REPLIT_DEV_DOMAIN", "").strip()
    origin = ("https://" + preview if preview and env.get("REPLIT_DEPLOYMENT") != "1"
              else env.get("PUBLIC_APP_URL", "").strip()).rstrip("/")
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query
            or parsed.fragment or parsed.username or parsed.password):
        raise ValueError("Set PUBLIC_APP_URL to your published HTTPS origin, without a path. Preview can use REPLIT_DEV_DOMAIN automatically.")
    env.update({
        "APP_ENV": "production", "APP_DEBUG": "false", "CONNECTOR_MODE": "browserbase",
        "PUBLIC_APP_URL": origin, "CORS_ORIGINS": origin,
        "GOOGLE_CALENDAR_FRONTEND_URL": origin,
        "GOOGLE_CALENDAR_REDIRECT_URI": origin + "/calendar/callback",
        "MCP_PUBLIC_URL": origin, "SIRI_PUBLIC_URL": origin + "/api",
        "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1", "PUPPETEER_SKIP_DOWNLOAD": "true",
        "PYTHONUNBUFFERED": "1", "VITE_API_BASE_URL": "/api",
    })
    return env


def checked(command, *, env=None):
    # Never echo the environment or exception command arguments into the console.
    subprocess.run([str(part) for part in command], cwd=ROOT, env=env, check=True)


def fingerprint():
    digest = hashlib.sha256()
    files = [Path(__file__), ROOT / "backend/requirements.txt"]
    for provider in ("handshake", "piazza"):
        files.extend(ROOT / "integrations" / provider / name for name in ("pyproject.toml", "uv.lock"))
    for provider in ("brightspace", "google-calendar"):
        project = ROOT / "integrations" / provider
        files.extend(path for path in project.iterdir() if path.is_file())
    frontend = ROOT / "frontend"
    files.extend(path for path in frontend.iterdir() if path.is_file() and not path.name.startswith(".env"))
    for name in ("src", "public"):
        files.extend(path for path in (frontend / name).rglob("*") if path.is_file())
    for path in sorted(set(files)):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def artifacts_exist():
    return all(path.exists() for path in (
        python_in(ROOT / "backend/.venv"), ROOT / "frontend/dist/index.html",
        python_in(ROOT / "integrations/handshake/.venv"),
        python_in(ROOT / "integrations/piazza/.venv"),
        ROOT / "integrations/brightspace/node_modules/brightspace-mcp-server/package.json",
        ROOT / "integrations/google-calendar/.generated/registry.mjs",
    ))


def built():
    try:
        return artifacts_exist() and json.loads(MARKER.read_text())["fingerprint"] == fingerprint()
    except (OSError, ValueError, KeyError):
        return False


def build():
    npm = shutil.which("npm")
    node = shutil.which("node")
    if not npm or not node or not shutil.which("git"):
        raise ValueError("Replit needs the nodejs-22 and python-3.12 modules and Git. Re-import the included .replit configuration.")
    node_version = subprocess.check_output([node, "--version"], text=True).strip().lstrip("v")
    major, minor = (int(part) for part in node_version.split(".")[:2])
    if major < 22 or major == 22 and minor < 12:
        raise ValueError("Use Node.js 22.12 or newer for the frontend build.")
    STATE.mkdir(parents=True, exist_ok=True)
    tools_python = python_in(STATE / "tools")
    if not tools_python.exists():
        checked([sys.executable, "-m", "venv", STATE / "tools"])
    checked([tools_python, "-m", "pip", "install", "--disable-pip-version-check", "uv==" + UV_VERSION])
    uv = [tools_python, "-m", "uv"]
    env = {**os.environ, "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1", "PUPPETEER_SKIP_DOWNLOAD": "true", "VITE_API_BASE_URL": "/api"}
    backend_python = python_in(ROOT / "backend/.venv")
    if not backend_python.exists():
        checked([*uv, "venv", "--python", "3.12", "--no-managed-python", ROOT / "backend/.venv"], env=env)
    print("Installing backend and isolated MCP helpers…", flush=True)
    checked([*uv, "pip", "install", "--python", backend_python, "-r", ROOT / "backend/requirements.txt"], env=env)
    for provider in ("handshake", "piazza"):
        checked([*uv, "sync", "--locked", "--no-dev", "--python", "3.12", "--no-managed-python", "--project", ROOT / "integrations" / provider], env=env)
    for provider in ("brightspace", "google-calendar"):
        checked([npm, "ci", "--omit=dev", "--prefix", ROOT / "integrations" / provider], env=env)
    print("Building the website…", flush=True)
    checked([npm, "ci", "--include=dev", "--prefix", ROOT / "frontend"], env=env)
    checked([npm, "run", "build", "--prefix", ROOT / "frontend"], env=env)
    MARKER.write_text(json.dumps({"fingerprint": fingerprint()}), encoding="utf-8")
    print("Replit build complete.", flush=True)


def check():
    # Called using the backend interpreter, where these dependencies are installed.
    from cryptography.fernet import Fernet
    from uuid import UUID
    sys.path.insert(0, str(ROOT / "backend"))
    from app.config import get_settings
    from app.integrations.vault import SessionVault
    required = (
        "DATABASE_URL", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
        "BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID", "CONNECTOR_ENCRYPTION_KEYS",
        "GOOGLE_CALENDAR_CLIENT_ID", "GOOGLE_CALENDAR_CLIENT_SECRET",
        "GOOGLE_CALENDAR_ENCRYPTION_KEY", "MCP_ENCRYPTION_KEY",
    )
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        raise ValueError("Add these in Replit Secrets / Production app secrets: " + ", ".join(missing))
    settings = get_settings()
    SessionVault(settings.connector_encryption_keys)
    for name in ("GOOGLE_CALENDAR_ENCRYPTION_KEY", "MCP_ENCRYPTION_KEY"):
        try:
            Fernet(os.environ[name].encode())
        except (ValueError, TypeError):
            raise ValueError(name + " must be a valid Fernet key. See REPLIT.md.") from None
    try:
        UUID(settings.browserbase_project_id)
    except ValueError:
        raise ValueError("BROWSERBASE_PROJECT_ID must be your Browserbase project UUID.") from None
    print("Hosted configuration and encryption keys are valid. Database migrations are managed separately.", flush=True)


def start():
    if not artifacts_exist():
        raise ValueError("Build files are missing. Run python3 scripts/replit.py build first.")
    env = environment(os.environ)
    try:
        port = int(env.get("PORT", "5000"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise ValueError("PORT must be a number between 1 and 65535.") from None
    backend_python = python_in(ROOT / "backend/.venv")
    checked([backend_python, Path(__file__), "check"], env=env)
    print(f"Serving TrojanClaw on port {port} with Browserbase and one API worker.", flush=True)
    os.chdir(ROOT / "backend")
    # Replit's TLS ingress owns forwarded headers. Only this port is exposed;
    # avoid query-bearing access logs because OAuth URLs contain private tokens.
    os.execve(str(backend_python), [str(backend_python), "-m", "uvicorn", "app.hosting:create_app",
        "--factory", "--host", "0.0.0.0", "--port", str(port), "--workers", "1",
        "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log"], env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=("run", "build", "start", "check"), default="run")
    action = parser.parse_args().action
    if action == "build":
        build()
    elif action == "check":
        os.environ.update(environment(os.environ))
        check()
    else:
        if action == "run" and not built():
            build()
        start()


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError:
        print("Replit setup failed at the step above. Fix it, then rerun the launcher.", file=sys.stderr)
        raise SystemExit(1) from None
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
