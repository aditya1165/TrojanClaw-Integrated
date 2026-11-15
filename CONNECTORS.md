# Chat connectors

The main chat supports Brightspace and Handshake with separate persistent Chromium profiles. Piazza is shown as **Coming soon** and cannot be selected or called.

## Local setup

Requirements: Git, uv, Python 3.11 for FastAPI, Python 3.12–3.14 for the connector helper, and Node.js 22 or newer for Brightspace and the frontend. Run the backend on your own computer, in a single process, bound to `127.0.0.1`. The persistent browser profiles belong to that computer's owner; this prototype is not a hosted multi-user authentication service.

From the repository root:

```sh
uv sync --locked --project integrations/handshake
uv run --locked --project integrations/handshake patchright install chromium
```

Configure `backend/.env` using `backend/.env.example`. `ANTHROPIC_API_KEY` is required for connected chat. Database/OpenAI configuration is only needed for the original campus-knowledge chat when no connectors are selected. No USC password goes into this file.

Start the backend in one terminal:

```sh
cd backend
uv run --no-project --python 3.11 --with-requirements requirements.txt uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Start the frontend in another terminal:

```sh
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

Open `http://127.0.0.1:5173`. The first Brightspace tool call may download the pinned `brightspace-mcp-server@5.2.3` through npx. Internet access is needed. The backend also recognizes a project-local Node installation under `.runtime/node-*-win-x64` if one is present.

## Connect and ask

1. Click **Connect** on Brightspace or Handshake.
2. A Chromium window opens with that connector's saved profile. Complete your usual school login and Duo/MFA yourself.
3. The helper saves the browser session automatically, closes the window, and verifies it through a read-only MCP call. The card changes from **Waiting for sign-in** to **Connected** only after that succeeds.
4. Ask in the main chat. Newly connected sources are enabled automatically. Use the **Use in chat** checkboxes to choose which sources the model can read.

Examples: "What's due this week?", "Show my latest course announcements", "Find software engineering internships on Handshake".

After an app restart, cards with stored session data show **Session saved**. Click **Check session** to verify it, or select **Use in chat** to try it directly. If a school expires the session, click **Sign in again**. Persisting a browser profile does not prevent session expiry or future MFA prompts.

## Persistent storage

Default base directory: `~/.trojanclaw/connectors` (Windows: `%USERPROFILE%\.trojanclaw\connectors`). Set `TROJANCLAW_SESSION_DIR` to change it; a blank value uses the default. Use an absolute path and the same value when launching the backend or a standalone connector.

```text
connectors/
  brightspace/
    profile/          # Persistent Chromium state
    cookies.json      # Automatically captured after reaching the USC dashboard
  handshake/
    profile/          # Upstream persistent Chromium state
    cookies.json      # Upstream automatically captured session
    origin.json       # Authenticated Handshake school address used by MCP
```

There is no shared cookie file and no manual cookie import. Brightspace receives only its own two session cookies through the child process environment (`D2L_SESSION_COOKIE`); Handshake reads only its own session directory. Cookies and browser profiles never pass through the frontend or HTTP API. Tool results are sent to Anthropic to answer selected-source questions; they are not written to the campus knowledge database.

Brightspace uses USC's `https://brightspace.usc.edu` endpoint. Its allowed read tools cover courses, assignments, deadlines, grades, announcements, course content, discussions, groups, rubrics, feedback, and transcripts. Downloads, session-management tools, and class lists are blocked. Handshake's allowlist is documented in [handshake.md](handshake.md). Tool names and parameters are checked again before dispatch; selecting one connector does not grant access to another.

## Endpoints and verification

- `GET /integrations`: status of the three cards.
- `POST /integrations/{provider}/connect`: starts a login job, returns 202, then the UI polls status.
- `POST /integrations/{provider}/verify`: checks a saved session.
- `POST /chat`: accepts optional `connectors: ["brightspace", "handshake"]` and runs a bounded read-only tool loop.

Login, verification, and connector chat requests require a loopback client and an allowed frontend origin. Keep `CORS_ORIGINS` limited to the local frontend. Only one sign-in job per connector can run in a backend process at a time.

```sh
uv run --locked --project integrations/handshake --group dev pytest integrations/handshake/tests -q
cd backend
uv run --no-project --python 3.11 --with-requirements requirements-dev.txt python -m pytest tests -q
```

Automated tests use fake account responses and browser contexts. A real USC/Handshake sign-in still needs the student present to verify institutional login compatibility.
