# TrojanClaw v2

An AI academic copilot for USC students, built on Claude, MCP, FastAPI, React and Supabase (pgvector).

## Current focus

**Sign in with USC**: one USC login (password + Duo) signs the student in and connects Brightspace and Handshake automatically. Setup, design and endpoints: [CONNECTORS.md](CONNECTORS.md).

Connector notes:

- [docs/brightspace.md](docs/brightspace.md): `brightspace-mcp-server@5.2.3`, sign-in, tool allowlist
- [handshake.md](handshake.md): Handshake MCP connector and allowlist
- [docs/piazza.md](docs/piazza.md): `mcp-piazza`, login methods, tool allowlist (Piazza cannot use the USC login)
- [docs/google-calendar.md](docs/google-calendar.md): Google OAuth setup, event proposals, and mandatory approval

## Layout

| Path | Contents |
| :--- | :--- |
| `backend/` | FastAPI app (Python 3.11) |
| `frontend/` | React 19 + Vite + TypeScript |
| `integrations/handshake/` | Connector helper (Brightspace + Handshake MCP, Python 3.12+) |
| `integrations/piazza/` | Account-scoped Piazza MCP and persistent Chromium sign-in (Python 3.12+) |
| `supabase/migrations/` | Database schema |
| `docs/` | Integration notes |

## Run locally

```sh
# Backend
cd backend && python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # fill in DATABASE_URL, SUPABASE_URL, SUPABASE_KEY, API keys
uvicorn app.main:app --reload

# Frontend
cd frontend && npm install && npm run dev
```
