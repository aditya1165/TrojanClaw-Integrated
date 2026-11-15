# TrojanClaw v2

An AI academic copilot for USC students, built on Claude, MCP, FastAPI, React and Supabase (pgvector).

## Current focus

Connecting **Brightspace** and **Piazza** through read-only MCP servers:

- [docs/brightspace.md](docs/brightspace.md): `brightspace-mcp-server@5.2.3`, cookie sign-in, tool allowlist
- [docs/piazza.md](docs/piazza.md): `mcp-piazza`, login methods, tool allowlist

## Layout

| Path | Contents |
| :--- | :--- |
| `backend/` | FastAPI app (Python 3.11) |
| `frontend/` | React 19 + Vite + TypeScript |
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
