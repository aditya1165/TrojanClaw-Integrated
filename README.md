# TrojanClaw v2

An AI academic copilot for USC students, built on Claude, MCP, FastAPI, React and Supabase (pgvector).

## What it does

- **Sign in with USC**: one USC login (password + Duo) signs the student in and connects Brightspace and Handshake automatically. **Continue with USC Google** also connects Google Calendar. Username and password accounts still work. Details: [CONNECTORS.md](CONNECTORS.md).
- **Chat** with live, read-only answers from Brightspace and Handshake, plus public USC campus events (EngageSC) and USC documents.
  - Replies stream in word by word. While TrojanClaw looks something up, a status line shows what it is checking, for example "Checking Brightspace: upcoming due dates".
  - Links the reply relied on appear as chips under it, such as "Brightspace · ASN-04". A link is shown only if a tool returned it and the reply cites it.
- **Multiple chats**, like Claude: saved per account, each with its own address (`/chat/<id>`), an AI-written title, search, pin, rename and delete.
- **Google Calendar drafts**: chat can draft one event at a time; nothing is added until the student presses **Approve addition**. Drafts belong to the chat that made them. See [docs/google-calendar.md](docs/google-calendar.md).
- **Sources**: the **Sources** button in the chat box turns sources on or off for chat; **Sources** in the sidebar connects, reconnects and checks them. If USC signs the student out of a source, a banner offers **Reconnect**.
- **Settings** (gear beside the name in the sidebar): light or dark theme, time zone, default sources, keyboard shortcuts, sign out.
- **Keyboard shortcuts**: ⌘K new chat, ⌘/ type a message, ⌘⇧F search chats, Esc closes a window. Use Ctrl on Windows.
- Works on phones: the sidebar becomes a drawer opened from the top bar.
- **How it works** page for new visitors, linked from the landing page and the bottom of the chat sidebar.

## Docs

- [CONNECTORS.md](CONNECTORS.md): Sign in with USC, per-account connector sessions, endpoints
- [backend/README.md](backend/README.md): backend setup, chat API (streaming, chats, sources), EngageSC
- [docs/brightspace.md](docs/brightspace.md): `brightspace-mcp-server@5.2.3`, sign-in, tool allowlist
- [handshake.md](handshake.md): Handshake MCP connector and allowlist
- [docs/piazza.md](docs/piazza.md): `mcp-piazza`, login methods, tool allowlist (Piazza cannot use the USC login)
- [docs/google-calendar.md](docs/google-calendar.md): Google OAuth setup, event drafts, and mandatory approval

## Layout

| Path | Contents |
| :--- | :--- |
| `backend/` | FastAPI app (Python 3.11) |
| `frontend/` | React 19 + Vite + TypeScript + Tailwind v4 |
| `integrations/handshake/` | Connector helper (Brightspace + Handshake MCP, Python 3.12+) |
| `integrations/google-calendar/` | Google Calendar MCP gateway (Node 22.12+) |
| `supabase/migrations/` | Database schema |
| `docs/` | Integration notes |

Frontend map:

| File | What it is |
| :--- | :--- |
| `src/App.tsx` | Landing page when signed out, chat when signed in, How it works |
| `src/screens/Chat.tsx` | Sidebar (chats, Sources, settings), the chat, phone drawer, shortcuts |
| `src/lib/chatStream.ts` | Sends a message and reads the streamed reply |
| `src/components/ChatList.tsx` | Chat list with search, pin, rename and delete |
| `src/components/Sources.tsx`, `src/lib/sources.ts` | Sources menu in the chat box, Sources window, connection state |
| `src/components/SettingsDialog.tsx`, `src/lib/settings.ts` | Settings window; preferences saved in this browser |
| `src/components/Toaster.tsx`, `src/lib/toast.ts` | Short confirmations such as "Chat renamed" |
| `src/components/Calendar.tsx` | Calendar draft cards for the open chat |
| `src/index.css` | Colour tokens (USC cardinal and gold, light and dark) |

## Run locally

```sh
# Backend
cd backend && python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # fill in DATABASE_URL, SUPABASE_URL, SUPABASE_KEY, API keys
uvicorn app.main:app --reload

# Frontend
cd frontend && cp .env.example .env && npm install && npm run dev
```

Open the app at **http://127.0.0.1:5173**. Use `127.0.0.1` everywhere (frontend URL, `VITE_API_BASE_URL`, Google redirect URIs), never a mix with `localhost`: the browser keeps cookies per host, so mixing them breaks Google sign-in with "This Google sign-in expired".

Apply every file in `supabase/migrations/` in order. The newest ones add saved chats' calendar drafts (`20261121000000`) and pinned chats (`20261122000000`).
