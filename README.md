<div align="center">

<img src="docs/images/trojanclaw-cover.png" alt="TrojanClaw" width="220">

# TrojanClaw

**Your USC semester, in one chat.**

One USC login connects Brightspace, Handshake, Piazza, WebReg and Google Calendar.<br>
Then just ask: *what's due, what's next, what fits your week.*

![Python](https://img.shields.io/badge/Python-3.11-990000?style=flat-square&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-backend-990000?style=flat-square&logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-19-FFCC00?style=flat-square&logo=react&logoColor=black)
![Claude](https://img.shields.io/badge/Claude-Opus_5.5-FFCC00?style=flat-square&logo=anthropic&logoColor=black)
![Supabase](https://img.shields.io/badge/Supabase-Postgres_+_Storage-990000?style=flat-square&logo=supabase&logoColor=white)
![Platforms](https://img.shields.io/badge/runs_on-macOS_·_Windows-FFCC00?style=flat-square)

[Features](#-features) · [How it works](#-how-it-works) · [Quick start](#-quick-start) · [Configuration](#-configuration) · [Project map](#-project-map) · [Docs](#-docs)

</div>

---

## ✨ Features

| | |
| :--- | :--- |
| 🔐 **One USC login** | Sign in with your NetID and Duo once. TrojanClaw connects Brightspace, Handshake and your WebReg course bin in the background. **Continue with USC Google** adds Google Calendar too. |
| 💬 **Chat that checks for you** | Replies stream in word by word, with a live status line ("Checking Brightspace: upcoming due dates") and source links under each answer. |
| 🗂️ **Saved chats** | Every chat has its own link, an AI-written title, and can be searched, pinned, renamed or deleted. |
| 🔌 **Sources you control** | Turn Brightspace, Handshake, Piazza and campus events on or off from the chat box. If a session expires, a banner offers **Reconnect**. |
| 📅 **Google Calendar** | Ask what's on your calendar, or have chat draft an event, a repeating series or a change. Nothing is saved until you press **Approve**. |
| 🎓 **Next term's classes** | Your WebReg course bin (planned and registered sections) is read during sign-in, so you can ask about next term. Read-only: it never registers or drops. |
| 📎 **Files and 🎤 voice** | Attach PDFs, images, notes, Word, PowerPoint or Excel (5 per message, 5 MB each), or dictate your question. |
| 🌗 **Made for students** | Light and dark themes, keyboard shortcuts (⌘K new chat, ⌘/ type, ⌘⇧F search), a resizable sidebar, and a phone-friendly layout. |

> [!NOTE]
> Every source is **read-only**. TrojanClaw never submits work, applies to jobs, registers for classes or changes your calendar without your click.

---

## 🧭 How it works

```mermaid
flowchart LR
    You([🧑‍🎓 You]) --> UI["React app<br/>127.0.0.1:5173"]
    UI -- "/api" --> API["FastAPI backend<br/>127.0.0.1:8000"]
    API --> Agent{{"Claude agent<br/>(tool runner)"}}
    Agent --> MCP["TrojanClaw MCP tools<br/>deadlines · announcements<br/>course search · campus info"]
    Agent --> Live["Live connectors<br/>Brightspace · Handshake · Piazza"]
    Agent --> Cal["Google Calendar<br/>read · draft · approve"]
    Agent --> Reg["WebReg course bin<br/>(read-only snapshot)"]
    MCP --> DB[("Supabase<br/>Postgres + pgvector")]
    API --> Files[("Supabase Storage<br/>private chat uploads")]
```

1. **You ask** in the chat. The browser sends only your new message (and any files).
2. **The agent decides** which tools it needs: your synced course data, a live connector, your calendar, WebReg, or public USC information.
3. **The reply streams back** with what it checked and where each fact came from. Your chat is saved for next time.

Connector logins run in a real Chrome window on **your** computer, and each TrojanClaw account keeps its own sessions.

---

## 🚀 Quick start

### What you need

| Tool | Version | Used for |
| :--- | :--- | :--- |
| [Python](https://www.python.org/downloads/) | 3.11 | Backend |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | latest | Connector helpers (installs Python 3.12 for them) |
| [Node.js](https://nodejs.org/) | 22.12+ | Frontend and Google Calendar |
| [Google Chrome](https://www.google.com/chrome/) | latest | Sign in with USC and connector logins |

### 1 · Set up the backend

<details open>
<summary><b>macOS</b></summary>

```sh
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

</details>

<details>
<summary><b>Windows (PowerShell)</b></summary>

```powershell
cd backend
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

</details>

Then fill in `backend/.env` (see [Configuration](#-configuration)) and apply every file in `supabase/migrations/`, in order, to your Supabase project.

### 2 · Install the connectors (once, from the repository root)

```sh
uv sync --locked --project integrations/handshake
uv sync --locked --project integrations/piazza --python 3.12
uv run --locked --project integrations/handshake patchright install chromium
npm ci --prefix integrations/google-calendar
```

### 3 · Run it (two terminals)

```sh
# Terminal 1: backend/ (virtual environment active)
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

```sh
# Terminal 2: frontend/  (first time: copy .env.example to .env, then npm install)
npm run dev
```

Open **http://127.0.0.1:5173** and click **Sign in with USC**. 🎉

> [!IMPORTANT]
> Always use **`127.0.0.1`**, not `localhost`. The browser keeps sign-in cookies per host, and mixing the two breaks sign-in.

---

## ⚙️ Configuration

All settings live in `backend/.env` (template: [`backend/.env.example`](backend/.env.example)). The agent's model, effort and tools are in [`config/agent.yaml`](config/agent.yaml); its instructions are in [`prompts/ai_instructions.md`](prompts/ai_instructions.md).

| Setting | What it's for | Required |
| :--- | :--- | :---: |
| `DATABASE_URL` | Supabase Postgres (Session pooler connection string) | ✅ |
| `SUPABASE_URL`, `SUPABASE_KEY` | Supabase project URL and publishable key | ✅ |
| `SUPABASE_SECRET_KEY` | Secret key for the private file-upload bucket (backend only) | for 📎 |
| `ANTHROPIC_API_KEY` | Claude | ✅ |
| `GEMINI_API_KEY` | Embeddings for course and campus search | ✅ |
| `GOOGLE_CALENDAR_*` | Google OAuth client, redirect URIs and the token encryption key | for 📅 |
| `CORS_ORIGINS` | Allowed frontend address (`http://127.0.0.1:5173`) | ✅ |

The frontend needs only `frontend/.env` with `VITE_API_BASE_URL=/api`. Vite forwards `/api` to the backend.

---

## 🗺️ Project map

```text
TrojanClaw_v2/
├── backend/                 FastAPI app
│   ├── app/agent/           Claude agent runtime (streaming, tool activity)
│   ├── app/mcp_server/      TrojanClaw MCP tools, built per student
│   ├── app/integrations/    Live connectors, WebReg, Google Calendar gateway
│   ├── app/services/        Chat, calendar, uploads, Sign in with USC
│   ├── app/api/routes/      HTTP endpoints
│   └── tests/               Backend tests
├── frontend/                React 19 + Vite + Tailwind (USC cardinal & gold)
│   └── src/                 Screens, chat, Sources, Settings
├── integrations/            Connector helpers: Handshake + Brightspace, Piazza, Google Calendar
├── config/agent.yaml        Model, effort and the agent's tools
├── prompts/                 The agent's instructions
├── supabase/migrations/     Database schema and storage bucket
└── docs/                    Guides for each integration
```

---

## 🧪 Tests

| Part | Command | Where |
| :--- | :--- | :--- |
| Backend | `python -m pytest tests -q` | `backend/` |
| Frontend | `npm run lint` · `npm run build` · `npm test` | `frontend/` |
| Handshake + Brightspace helper | `uv run --locked --project integrations/handshake --group dev pytest -q integrations/handshake/tests` | repo root |
| Piazza helper | `uv run --locked --project integrations/piazza --group dev pytest -q integrations/piazza/tests` | repo root |
| Google Calendar gateway | `npm test` | `integrations/google-calendar/` |

---

## 🩹 Troubleshooting

| You see | Fix |
| :--- | :--- |
| "This Google sign-in expired" | You mixed `localhost` and `127.0.0.1`. Open the app at `http://127.0.0.1:5173`. |
| "Install the local connectors first" | Run the `uv sync` lines in step 2. |
| "Install the Google Calendar connector dependencies first" | Run `npm ci --prefix integrations/google-calendar`. |
| "File uploads need SUPABASE_SECRET_KEY" | Add the secret key (Supabase → Project Settings → API Keys) to `backend/.env`. |
| A source shows **Sign-in needed** | Click **Reconnect** on the banner, or **Sign in again** in Sources. |
| `Address already in use` on port 8000 | An old backend is still running. Stop it, or use another port. |

---

## 📚 Docs

| Guide | What's inside |
| :--- | :--- |
| [CONNECTORS.md](CONNECTORS.md) | Sign in with USC, per-account sessions, WebReg, how chat uses connectors |
| [backend/README.md](backend/README.md) | Backend setup and the chat API (streaming, saved chats, uploads) |
| [docs/google-calendar.md](docs/google-calendar.md) | Google OAuth setup, drafts and approval |
| [docs/brightspace.md](docs/brightspace.md) · [docs/handshake.md](docs/handshake.md) · [docs/piazza.md](docs/piazza.md) | What each connector can read |
| [TrojanClaw_v2_Architecture_and_Spec.md](TrojanClaw_v2_Architecture_and_Spec.md) | Full architecture and roadmap |

---

<div align="center">

Made with ❤️ at USC · **Fight On!** ✌️

</div>
