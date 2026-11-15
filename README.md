# TrojanClaw v2

An AI academic copilot for USC students, built on Claude, MCP, FastAPI, React and Supabase (pgvector).

## What it does

- **Sign in with USC**: one USC login (password + Duo) signs the student in and connects Brightspace and Handshake automatically. **Continue with USC Google** also connects Google Calendar. Username and password accounts still work. Details: [CONNECTORS.md](CONNECTORS.md).
- **Chat** runs one Claude agent (`config/agent.yaml`, `prompts/ai_instructions.md`) with live, read-only tools for Brightspace, Handshake and Piazza, plus TrojanClaw's own tools for deadlines, announcements, course materials, USC documents and public campus events (EngageSC). Piazza has its own sign-in; see [docs/piazza.md](docs/piazza.md).
  - Replies stream in word by word. While TrojanClaw looks something up, a status line shows what it is checking, for example "Checking Brightspace: upcoming due dates".
  - Links the reply relied on appear as chips under it, such as "Brightspace · ASN-04". A link is shown only if a tool returned it and the reply cites it.
- **Attach files** (📎, drag-and-drop or paste): PDF, images, text and code, Word, PowerPoint and Excel; up to 5 files of 5 MB each per message. PDFs and images go to Claude as they are; Word, PowerPoint and Excel as their text. Files are kept in a private Supabase Storage bucket (`chat-uploads`) and stay with the chat; deleting the chat deletes them. Needs `SUPABASE_SECRET_KEY` in `backend/.env`.
- **Voice input** (🎤): the browser's built-in speech recognition types what you say into the message box. Works in Chrome, Edge and Safari (the button is hidden in Firefox); Chrome sends the audio to Google to recognise it.
- **Multiple chats**, like Claude: saved per account, each with its own address (`/chat/<id>`), an AI-written title, search, pin, rename and delete.
- **Google Calendar**: chat can read the student's events, and draft one new event or one change to an existing event (including repeating events and reminders) at a time. Nothing changes in Google Calendar until the student approves the card. Drafts belong to the chat that made them. See [docs/google-calendar.md](docs/google-calendar.md).
- **Sources**: the **Sources** button in the chat box turns sources on or off for chat; **Sources** in the sidebar connects, reconnects and checks them. If USC signs the student out of a source, a banner offers **Reconnect**.
- **Settings** (gear beside the name in the sidebar): light or dark theme, time zone, default sources, keyboard shortcuts, sign out.
- **Keyboard shortcuts**: ⌘K new chat, ⌘/ type a message, ⌘⇧F search chats, Esc closes a window. Use Ctrl on Windows.
- Works on phones: the sidebar becomes a drawer opened from the top bar.
- **How it works** page for new visitors, linked from the landing page and the bottom of the chat sidebar.

## Docs

- [CONNECTORS.md](CONNECTORS.md): Sign in with USC, per-account connector sessions, endpoints
- [backend/README.md](backend/README.md): backend setup, chat API (streaming, chats, sources), EngageSC
- [docs/brightspace.md](docs/brightspace.md): `brightspace-mcp-server@5.2.3`, sign-in, tool allowlist
- [handshake.md](docs/handshake.md): Handshake MCP connector and allowlist
- [docs/piazza.md](docs/piazza.md): `mcp-piazza`, login methods, tool allowlist (Piazza cannot use the USC login)
- [docs/google-calendar.md](docs/google-calendar.md): Google OAuth setup, event drafts, and mandatory approval

## Layout

| Path | Contents |
| :--- | :--- |
| `backend/` | FastAPI app (Python 3.11) |
| `frontend/` | React 19 + Vite + TypeScript + Tailwind v4 |
| `integrations/handshake/` | Connector helper (Brightspace + Handshake MCP, Python 3.12+) |
| `integrations/google-calendar/` | Google Calendar MCP gateway (Node 22.12+) |
| `integrations/piazza/` | Account-scoped Piazza MCP and persistent Chromium sign-in (Python 3.12+) |
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

## Run locally (macOS or Windows)

Needs: Python 3.11 (backend), [uv](https://docs.astral.sh/uv/) (connector helpers, Python 3.12), Node.js 22.12 or newer, and Google Chrome (Sign in with USC and the connectors open the installed Chrome). Run every block from the repository root.

**1. Backend** (first time)

```sh
# macOS
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # then fill in the values
```

```powershell
# Windows (PowerShell)
cd backend
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env   # then fill in the values
```

**2. Connector helpers and Google Calendar** (first time, same on both)

```sh
uv sync --locked --project integrations/handshake
uv sync --locked --project integrations/piazza --python 3.12
uv run --locked --project integrations/handshake patchright install chromium
npm ci --prefix integrations/google-calendar
```

**3. Run** (two terminals)

```sh
# Terminal 1, in backend/ with the virtual environment active
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# Terminal 2, in frontend/ (first time: copy .env.example to .env, then npm install)
npm run dev
```

**Tests**: `python -m pytest tests -q` in `backend/`; `npm run lint`, `npm run build` and `npm test` in `frontend/`.

If a source never opens a sign-in window, its helper isn't installed: the Sources menu shows "Install the local connectors first". If Google Calendar says "Install the Google Calendar connector dependencies first", run the `npm ci` line above.

Open the app at **http://127.0.0.1:5173**. The frontend calls the API at `/api`, and Vite forwards it to `127.0.0.1:8000` (`frontend/vite.config.ts`), so the browser sees one host and sign-in cookies work; keep `VITE_API_BASE_URL=/api`. Google redirect URIs and `GOOGLE_CALENDAR_FRONTEND_URL` must also use `127.0.0.1`, not `localhost`: the browser keeps cookies per host, so mixing them breaks Google sign-in with "This Google sign-in expired".

Apply every file in `supabase/migrations/` in order. The newest ones add saved chats' calendar drafts (`20261121000000`), pinned chats (`20261122000000`) and the private `chat-uploads` Storage bucket (`20261123000000`).
