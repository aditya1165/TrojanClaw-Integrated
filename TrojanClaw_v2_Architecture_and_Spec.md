# TrojanClaw v2: Engineering Architecture & Technical Specification

> **Version:** 2.0  
> **Repository:** [`aditya1165/TrojanClaw_v2`](https://github.com/aditya1165/TrojanClaw_v2)  
> **Target Delivery:** Hackathon / Sprint Deadline  
> **Core Stack:** Anthropic Claude (Claude 3.5 Sonnet) • Supabase (`pgvector`) • React • FastAPI • Google Calendar API  

---

## 1. Executive Summary & System Architecture

**TrojanClaw v2** is an AI academic copilot and autonomous time-blocking scheduler engineered specifically for USC students. It unifies academic obligations (**Brightspace** assignments, **Piazza** announcements) and campus life (**EngageSC** club events) into an intelligent timeline that automatically schedules dedicated study blocks directly into **Google Calendar** using **Claude 3.5 Sonnet**.

USC protects Brightspace with Shibboleth SSO and Duo 2FA, so TrojanClaw never stores or types a student's USC password. Instead, the student signs in **themselves**, in a real browser window opened by a small **TrojanClaw Sync Helper** running on their own computer. Read-only MCP servers (`brightspace-mcp-server` and `mcp-piazza`) then pull the student's Brightspace and Piazza data, and the helper uploads it to **Supabase `pgvector`** for semantic retrieval and long-term context storage.

### Deployment Model: Multi-User, Hosted App + Local Sync Helper

TrojanClaw v2 is a **multi-user application**. Any USC student can sign in with Google and get their own workspace. It has two parts:

| Part | Runs on | Responsibilities |
| :--- | :--- | :--- |
| **Hosted app** | TrojanClaw server | React UI, FastAPI, Supabase, Google OAuth & Calendar, free-slot engine, Claude scheduler & chat, EngageSC scraping, `.ics` fallback |
| **Sync Helper** | Each student's computer | Opens the USC SSO / Duo and Piazza login windows, runs the read-only MCP servers, normalizes the results, and uploads them to that student's account |

* Every student is a row in `public.users`, created on first Google sign-in.
* All ingested data (`student_context_items`, `item_embeddings`, `synced_calendar_events`) carries a `user_id`, and every query, vector search and calendar sync is scoped to the signed-in user.
* **Why a local helper:** SSO + Duo login needs a visible browser window in front of the student, and a hosted server cannot open one on a student's laptop. Login sessions therefore stay on the student's machine. Only normalized academic records (deadlines, announcements, posts) leave it.
* **MCP connector details** (read-only tool allowlist, blocked tools, `mcp-piazza` settings) follow `SINGLE_USER_MCP_IMPLEMENTATION_PLAN.md` §6 and §15. That plan's single-owner login is replaced here by per-student Google sign-in, and its server-side sync worker is replaced by the Sync Helper (§2.2).

### End-to-End System Workflow

```mermaid
flowchart TD
    subgraph Student_Machine["Student's Computer: TrojanClaw Sync Helper"]
        Login["Visible Browser Login (USC SSO + Duo, Piazza)"]
        BSMCP["brightspace-mcp-server (read-only)"]
        PZMCP["mcp-piazza (read-only)"]
        Helper["Normalizer & Uploader"]
    end

    subgraph Data_Sources["Server-Side Sources"]
        ENG["EngageSC (Public Events Scraper)"]
        ICS["Brightspace .ics Feed (fallback)"]
        Demo["Demo Seed Dataset (judges)"]
    end

    subgraph Backend_Pipeline["FastAPI Backend & VectorDB"]
        SyncAPI["Sync API (/sync/upload, /sync/jobs)"]
        Parser["Parser & Normalizer Engine"]
        Embedder["Embedding Generator (1536-dim)"]
        SupaDB[("Supabase (PostgreSQL + pgvector)")]
        GCalSync["Google Calendar Free/Busy Engine"]
        SlotEngine["Free-Slot Engine (deterministic)"]
        Claude["Anthropic Claude 3.5 Sonnet"]
        Validator["Schedule Validator"]
    end

    subgraph Client_Layer["Frontend Layer (React)"]
        UI_Auth["Google OAuth 2.0 Login"]
        UI_Onboard["Onboarding & Connectors Hub"]
        UI_Dash["Dashboard (Timeline + Copilot Chat)"]
    end

    Login --> BSMCP
    Login --> PZMCP
    BSMCP --> Helper
    PZMCP --> Helper
    Helper -->|Normalized Records + Sync Token| SyncAPI
    SyncAPI --> Parser
    SyncAPI -->|Queued Sync Now Jobs| Helper

    ENG --> Parser
    ICS --> Parser
    Demo --> Parser

    Parser --> Embedder
    Embedder --> SupaDB

    UI_Auth -->|Store Refresh Token| SupaDB
    UI_Onboard -->|Pair Helper, Preferences, Fallback Feeds| SyncAPI
    UI_Dash -->|Sync Now| SyncAPI

    UI_Dash -->|User Schedule Request| Claude
    GCalSync -->|Busy Windows| SlotEngine
    SlotEngine -->|Candidate Slots with IDs| Claude
    SupaDB -->|Vector Context (Deadlines & Posts)| Claude
    Claude -->|Slot Assignments as JSON| Validator
    Validator -->|Validated Study Blocks| GCalSync
    GCalSync -->|Create Events '[TrojanClaw]'| GCal[("Google Calendar")]
    GCalSync -->|Render Updated Agenda| UI_Dash
```

---

## 2. LOGIN & AUTHENTICATION SPECIFICATION

Automated headless browser scripts (e.g. Playwright / Selenium) entering USC credentials will fail due to USC's Shibboleth SSO and Duo Mobile 2FA push notifications, and TrojanClaw should never hold a student's USC password in the first place. So the **student logs in themselves** in a visible browser window on their own computer, and TrojanClaw reads data through that session with read-only MCP servers. Three logins stay separate:

1. **TrojanClaw account:** Google OAuth (§2.1).
2. **USC Brightspace:** USC SSO + Duo in a visible browser opened by the Sync Helper (§2.2, §2.3).
3. **Piazza:** Piazza login (including SSO) in a visible browser opened by the Sync Helper (§2.2, §2.4).

### 2.1 Google OAuth 2.0 (App Identity & Calendar Permissions)
* **Strategy:** Use Google OAuth as the primary authentication provider.
* **Requested OAuth Scopes:**
  * `openid`, `email`, `profile`
  * `https://www.googleapis.com/auth/calendar.readonly` (Reads free/busy schedule slots)
  * `https://www.googleapis.com/auth/calendar.events` (Inserts and manages `[TrojanClaw]` study blocks)
* **Token Management:**
  * Supabase stores the user's `google_refresh_token` encrypted at rest.
  * When executing background schedule runs, the backend exchanges the refresh token for a short-lived `access_token` using Google's token endpoint (`https://oauth2.googleapis.com/token`).

### 2.2 User Sync via the TrojanClaw Sync Helper (Primary Path)

The Sync Helper (`trojanclaw-sync`) is a small Python program the student runs on their own computer. It owns the USC and Piazza login sessions and the MCP server processes.

1. **Pair the computer (once):**
   * In onboarding, the student clicks **"Connect this computer"**. The dashboard shows a short one-time pairing code (valid for 10 minutes).
   * The student runs `trojanclaw-sync pair <code>`. The helper exchanges the code for a per-device **sync token**, stored in the OS keychain. The backend stores only a hash of the token (`sync_devices`, §3.1), and the student can revoke the device from the dashboard.
2. **Log in (first sync, and whenever a session expires):**
   * The helper starts `brightspace-mcp-server` with a visible browser (`D2L_HEADLESS=false`). The student completes the **USC SSO + Duo** login in that window.
   * The helper runs `mcp-piazza login --browser`. The student logs in to Piazza in that window (works for Google/SSO Piazza accounts, so no Piazza password is needed).
   * Sessions are saved locally by each MCP server. The USC password and session cookies never reach TrojanClaw's servers or Claude.
3. **Pull and upload:**
   * The helper calls only the **read-only allowlisted** MCP tools (§2.3, §2.4), parses the JSON results, and normalizes each record into the `student_context_items` shape with a stable `external_id` (Brightspace item ID or Piazza post ID).
   * It uploads the batch with `POST /sync/upload` and its sync token. The backend resolves the token to the student's `user_id`, upserts on `(user_id, source, external_id)`, and re-embeds only changed items. Re-running a sync never creates duplicates.
4. **Triggers:**
   * **"Sync now"** in the dashboard queues a row in `sync_jobs`. The running helper polls `GET /sync/jobs/next` every ~30 seconds and picks it up.
   * While the helper is running, it also syncs on a schedule (default every 2 hours).
   * If a USC or Piazza session has expired, the helper reopens the login window and the dashboard shows **"Re-login needed"** with the time of the last good sync.

### 2.3 Brightspace via `brightspace-mcp-server`

* npm package `brightspace-mcp-server` (MIT, Node >= 22), run over stdio. **Pin an exact version**: it releases very often.
* There is no USC preset. Use the generic SSO path with Duo options (`D2L_HEADLESS=false`, `D2L_DUO_PASSCODE` if needed).
* Tool results are JSON inside text blocks plus extra "update notice" blocks. The helper parses the JSON and discards the notices.
* **Read-only allowlist:** courses, assignments and due dates, quizzes, calendar events, announcements/news, content module listings (see the MCP plan §6).
* **Blocked:** `get-classlist-emails` and `get-roster` (classmates' personal data), all `download-*` tools (they write files), `sign-in`, `sign-out`, `mfa-relay`, and the server's HTTP mode.
* **Gate before building on it:** USC SSO + Duo through this server is unproven (MCP plan §15.3). Before the connector work starts, run its `setup` once against USC and record whether login succeeds, which Duo method works, and how long the session lasts. If it fails, the `.ics` fallback (§2.5) still covers deadlines.

### 2.4 Piazza via `mcp-piazza`

* Source: `github.com/jadenpxrk/mcp-piazza`, run with `uvx mcp-piazza` over stdio. Read-only, returns structured output.
* SSO-friendly login with `login --browser` (or `--cookie`), so no Piazza password is stored.
* Always run with **`PIAZZA_KEEP_UNREAD=true`**, because `get_post` otherwise marks posts as read on the student's account.
* It is built on the unofficial `piazza-api` and can break without notice. The demo dataset (§2.5) is the safety net for judging.

### 2.5 Fallback Paths

* **Brightspace `.ics` feed (no helper needed):** for students who skip the Sync Helper, or if USC SSO through MCP turns out not to work. The student pastes their Brightspace calendar feed URL (`Brightspace -> Calendar -> Settings -> Enable Calendar Feeds -> Subscribe -> Copy URL`), and the hosted backend fetches it (§5.1). It covers due dates only, not announcements. Store the URL encrypted, since anyone holding it can read the feed.
* **Pre-Loaded USC Demo Dataset (mandatory for hackathon judges):** a one-click toggle in onboarding labeled **"Load Sample USC Student Profile (CSCI 567 & CSCI 570)"** that seeds realistic assignments, deadline extensions, and exam notices with no USC or Piazza login.

---

## 3. DATABASE & VECTORDB SPECIFICATION (Supabase + `pgvector`)

### 3.1 Relational & Vector Schema

Run the following SQL migration in your Supabase project:

```sql
-- 1. Enable pgvector
create extension if not exists vector;

-- 2. Users Table
create table public.users (
    id uuid default gen_random_uuid() primary key,
    email text unique not null,
    google_refresh_token text,
    study_preferences jsonb default '{
        "preferred_start": "17:00",
        "preferred_end": "22:00",
        "max_block_hours": 2,
        "days_active": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Sunday"]
    }'::jsonb,
    created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- 3. Normalized Context Items (Assignments, Announcements, Club Events)
create table public.student_context_items (
    id uuid default gen_random_uuid() primary key,
    user_id uuid references public.users(id) on delete cascade not null,
    source text not null check (source in ('brightspace', 'piazza', 'engagesc')),
    external_id text not null, -- Brightspace item ID, Piazza post ID, or EngageSC event ID
    course_or_org text,
    title text not null,
    content text not null,
    event_timestamp timestamp with time zone,
    raw_metadata jsonb default '{}'::jsonb,
    created_at timestamp with time zone default timezone('utc'::text, now()) not null,
    updated_at timestamp with time zone default timezone('utc'::text, now()) not null,
    unique (user_id, source, external_id) -- lets every sync upsert instead of duplicating
);

-- 4. Embeddings Storage for Vector Search
create table public.item_embeddings (
    id uuid default gen_random_uuid() primary key,
    item_id uuid references public.student_context_items(id) on delete cascade not null,
    user_id uuid references public.users(id) on delete cascade not null,
    chunk_content text not null,
    embedding vector(1536), -- 1536-dimensional vectors
    created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- 5. HNSW Cosine Distance Index
create index on public.item_embeddings using hnsw (embedding vector_cosine_ops);

-- 6. Calendar Sync Log (Enables updating/deleting scheduled blocks)
create table public.synced_calendar_events (
    id uuid default gen_random_uuid() primary key,
    user_id uuid references public.users(id) on delete cascade not null,
    item_id uuid references public.student_context_items(id) on delete set null,
    google_event_id text not null,
    summary text not null,
    start_time timestamp with time zone not null,
    end_time timestamp with time zone not null,
    created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- 7. Paired Sync Helper Devices (one row per student computer)
create table public.sync_devices (
    id uuid default gen_random_uuid() primary key,
    user_id uuid references public.users(id) on delete cascade not null,
    device_name text not null,
    token_hash text unique not null, -- SHA-256 of the sync token; the raw token lives only in the device keychain
    last_seen_at timestamp with time zone,
    revoked_at timestamp with time zone,
    created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- 8. Sync Jobs ("Sync now" requests and scheduled runs, with status for the dashboard)
create table public.sync_jobs (
    id uuid default gen_random_uuid() primary key,
    user_id uuid references public.users(id) on delete cascade not null,
    device_id uuid references public.sync_devices(id) on delete set null,
    status text not null default 'queued'
        check (status in ('queued', 'running', 'succeeded', 'failed', 'needs_login')),
    sources text[] not null default array['brightspace', 'piazza'],
    items_upserted int default 0,
    error text,
    requested_at timestamp with time zone default timezone('utc'::text, now()) not null,
    started_at timestamp with time zone,
    finished_at timestamp with time zone
);
create index on public.sync_jobs (user_id, status, requested_at);

-- 9. Supabase RPC Function for Cosine Similarity Search
create or replace function match_student_context(
    query_embedding vector(1536),
    match_threshold float,
    match_count int,
    filter_user_id uuid
)
returns table (
    id uuid,
    item_id uuid,
    chunk_content text,
    similarity float
)
language plpgsql
as $$
begin
    return query
    select
        item_embeddings.id,
        item_embeddings.item_id,
        item_embeddings.chunk_content,
        1 - (item_embeddings.embedding <=> query_embedding) as similarity
    from item_embeddings
    where item_embeddings.user_id = filter_user_id
      and 1 - (item_embeddings.embedding <=> query_embedding) > match_threshold
    order by item_embeddings.embedding <=> query_embedding
    limit match_count;
end;
$$;
```

---

## 4. FRONTEND SPECIFICATION (React)

The frontend is built as a responsive single-page application using **React (Vite) + Tailwind CSS + Lucide Icons**.

```text
frontend/src/
├── components/
│   ├── Navbar.jsx                # User profile, Google avatar, sync status indicator
│   ├── PriorityFeed.jsx          # Sorted list of upcoming deadlines & Piazza alerts
│   ├── CalendarTimeline.jsx      # Day/Week agenda view with free vs scheduled slots
│   ├── ClaudeChatDrawer.jsx      # Chat copilot interface with prompt suggestions
│   └── OnboardingModal.jsx       # Pair Sync Helper, EngageSC interests, .ics/demo fallbacks
├── pages/
│   ├── Landing.jsx               # Pitch, features overview, "Sign in with Google"
│   ├── Onboarding.jsx            # Account connection cards + demo data loader
│   └── Dashboard.jsx             # Main dual-pane workspace (Feed + Calendar + Chat)
├── services/
│   ├── api.js                    # Axios client connecting to FastAPI
│   └── supabaseClient.js         # Supabase client for auth session listener
├── App.jsx                       # Routing (/ , /onboarding, /dashboard)
└── main.jsx
```

### Core Views & Behaviors

1. **Landing Page (`/`):**
   * Clear value proposition: *"Turn your Brightspace deadlines into an automated, stress-free Google Calendar study schedule with Claude AI."*
   * Primary CTA: **"Sign In with Google"** with explicit calendar permission badges.

2. **Onboarding Page (`/onboarding`):**
   * **Connect USC Card (Sync Helper):** Install instructions for `trojanclaw-sync`, a **"Connect this computer"** button that shows the one-time pairing code, and live status for the first sync: *Paired → Waiting for USC login → Waiting for Piazza login → Synced N items*.
   * **Fallback Card:** Tab 1, paste box for the Brightspace `.ics` URL with an accordion showing where to find it. Tab 2, **"Load Sample USC CS Student Feed"** with one click.
   * **EngageSC Card:** Multi-select chips for student interests (*AI/ML, Web Dev, Entrepreneurship, Career Fairs, Social*).
   * **Preferences Card:** Time range slider for study windows (e.g., 5 PM – 9 PM) and maximum continuous block length (default 2 hrs).

3. **Command Center Dashboard (`/dashboard`):**
   * **Top Bar Sync Status:** last successful sync time per source, a **"Sync now"** button (queues a `sync_jobs` row), and a **"Re-login needed"** banner when the helper reports an expired USC or Piazza session.
   * **Left Column (Academic Priority Feed):**
     * Highlights urgent items (due in < 48 hours).
     * Color-coded course badges (e.g. `[CSCI 570]` in red, `[CSCI 567]` in gold).
     * Pinned Piazza alerts (e.g. *"Midterm date confirmed for Nov 20"*).
   * **Center Column (Interactive Calendar View):**
     * Visual schedule showing existing Google Calendar commitments in gray and proposed `[TrojanClaw]` study blocks in gold.
     * Top-level button: **"Sync All to Google Calendar"** with real-time feedback.
   * **Right Column / Floating Drawer (Claude Study Copilot):**
     * Conversational interface powered by Claude 3.5 Sonnet.
     * Pre-built prompt chips:
       * *"Plan study sessions for my upcoming CSCI 570 homework."*
       * *"I have an interview on Thursday—adjust my study schedule."*
       * *"Summarize this week's Piazza announcements."*

---

## 5. INTEGRATIONS SPECIFICATION

### 5.1 Brightspace `.ics` Fallback Parser (`backend/app/connectors/brightspace.py`)

Runs on the hosted backend for students using the `.ics` fallback (§2.5). Students using the Sync Helper get Brightspace data through `brightspace-mcp-server` instead (§2.3, §5.8).

```python
import httpx
from icalendar import Calendar
import recurring_ical_events
from datetime import datetime, timezone, timedelta

def parse_brightspace_feed(ics_url: str) -> list[dict]:
    """Fetches and parses a student's private Brightspace iCal feed."""
    response = httpx.get(ics_url, timeout=12.0)
    response.raise_for_status()
    
    cal = Calendar.from_ical(response.text)
    now = datetime.now(timezone.utc)
    lookahead = now + timedelta(days=45)
    
    occurrences = recurring_ical_events.of(cal).between(now, lookahead)
    extracted = []
    
    for event in occurrences:
        summary = str(event.get('summary', 'Untitled Assignment'))
        description = str(event.get('description', ''))
        dtend = event.get('dtend') or event.get('dtstart')
        
        due_iso = None
        if dtend and hasattr(dtend, 'dt'):
            dt = dtend.dt
            if not hasattr(dt, 'hour'):  # Handle date-only objects
                dt = datetime.combine(dt, datetime.min.time(), tzinfo=timezone.utc)
            due_iso = dt.isoformat()
            
        uid = str(event.get('uid', ''))
        extracted.append({
            "source": "brightspace",
            "external_id": f"ics:{uid}:{due_iso}",  # uid alone repeats across recurring occurrences
            "title": summary,
            "content": f"Assignment: {summary}\nDue Date: {due_iso}\nDetails: {description}",
            "event_timestamp": due_iso,
            "raw_metadata": {
                "summary": summary,
                "location": str(event.get('location', '')),
                "uid": uid
            }
        })
    return extracted
```

---

### 5.2 Piazza Demo Seed (`backend/app/connectors/piazza_demo.py`)

Live Piazza data comes from `mcp-piazza` through the Sync Helper (§2.4, §5.8); the hosted backend never logs in to Piazza and never stores a Piazza password. This module only seeds the demo profile (§2.5).

```python
from datetime import datetime, timezone, timedelta

def get_demo_piazza_data() -> list[dict]:
    """Returns curated fallback data for demoing without real Piazza credentials."""
    return [
        {
            "source": "piazza",
            "external_id": "demo:csci570:hw4-extension",
            "course_or_org": "CSCI 570",
            "title": "[CSCI 570] Homework 4 Deadline Extended",
            "content": "Instructor Announcement: HW4 due date has been extended by 48 hours. New due date is Friday at 11:59 PM PT.",
            "event_timestamp": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
            "raw_metadata": {"pinned": True, "type": "instructor_note"}
        },
        {
            "source": "piazza",
            "external_id": "demo:csci567:midterm-review",
            "course_or_org": "CSCI 567",
            "title": "[CSCI 567] Midterm Review Session",
            "content": "The midterm review session will be held this Wednesday from 6 PM to 8 PM on Zoom. Recording will be posted.",
            "event_timestamp": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "raw_metadata": {"pinned": True, "type": "announcement"}
        }
    ]
```

---

### 5.3 EngageSC Scraper (`backend/app/connectors/engagesc.py`)

```python
import httpx
from bs4 import BeautifulSoup

def scrape_engagesc_events() -> list[dict]:
    """Scrapes public USC club events without requiring user authentication."""
    url = "https://engage.usc.edu/events"
    headers = {"User-Agent": "TrojanClaw-Academic-Bot/2.0"}
    
    try:
        res = httpx.get(url, headers=headers, timeout=10.0)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        
        events = []
        cards = soup.select(".event-card, .list-group-item, article")
        for card in cards[:10]:
            title_el = card.select_one("h3, .title, .event-title")
            org_el = card.select_one(".organization, .host-organization")
            time_el = card.select_one("time, .event-date")
            
            if title_el:
                title = title_el.get_text(strip=True)
                org = org_el.get_text(strip=True) if org_el else "USC Campus Life"
                when = time_el.get_text(strip=True) if time_el else "Check EngageSC"
                events.append({
                    "source": "engagesc",
                    "external_id": f"engagesc:{org}|{title}|{when}",
                    "course_or_org": org,
                    "title": title,
                    "content": f"Campus Club Event: {title}\nHosted by: {org}\nTime: {when}",
                    "raw_metadata": {"host": org, "time_string": when}
                })
        return events
    except Exception:
        # Fallback sample club events
        return [
            {
                "source": "engagesc",
                "external_id": "demo:engagesc:cais-tech-talk",
                "course_or_org": "USC Center for AI in Society (CAIS)",
                "title": "CAIS Student Branch Tech Talk & Pizza",
                "content": "Guest speaker from Google DeepMind discussing LLM agents. Free food provided.",
                "raw_metadata": {"host": "CAIS", "time_string": "Thursday 7:00 PM"}
            }
        ]
```

---

### 5.4 Google Calendar Sync Engine (`backend/app/calendar_sync.py`)

```python
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime, timezone, timedelta

def build_calendar_service(refresh_token: str, client_id: str, client_secret: str):
    creds = Credentials(
        None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret
    )
    return build('calendar', 'v3', credentials=creds)

def query_user_freebusy(service, days=7) -> list[dict]:
    """Retrieves all confirmed busy windows over the next N days."""
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)
    
    body = {
        "timeMin": now.isoformat(),
        "timeMax": end.isoformat(),
        "items": [{"id": "primary"}]
    }
    res = service.freebusy().query(body=body).execute()
    return res.get('calendars', {}).get('primary', {}).get('busy', [])

def insert_trojanclaw_study_block(service, title: str, start_iso: str, end_iso: str, description: str) -> str:
    """Inserts a branded study session block into the student's Google Calendar."""
    event_body = {
        'summary': f"[TrojanClaw] {title}",
        'description': f"{description}\n\nAuto-scheduled by TrojanClaw v2",
        'start': {'dateTime': start_iso},
        'end': {'dateTime': end_iso},
        'colorId': '5'  # Yellow/Gold calendar highlight
    }
    result = service.events().insert(calendarId='primary', body=event_body).execute()
    return result.get('id')
```

---

### 5.5 Free-Slot Engine (`backend/app/agent/slots.py`)

**Design rule: Claude never computes times.** Language models are unreliable at interval arithmetic: they produce overlapping blocks, wrong time zones, and times outside the student's study window. TrojanClaw therefore splits scheduling into three steps:

1. **Compute (Python, deterministic):** turn Google free/busy data and the student's study preferences into a list of candidate slots, each with a stable `slot_id`.
2. **Decide (Claude):** Claude chooses which slot goes to which assignment and explains why. It returns only `slot_id`s, never timestamps.
3. **Validate (Python, deterministic):** map each chosen `slot_id` back to its real start and end times, and reject anything invalid before it reaches Google Calendar (§5.7).

```python
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

USC_TZ = ZoneInfo("America/Los_Angeles")
SLOT_GRANULARITY = timedelta(minutes=15)

def parse_iso(ts: str) -> datetime:
    """Parses Google/Brightspace ISO timestamps (including a trailing 'Z') into USC local time."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(USC_TZ)

def _round_up(dt: datetime) -> datetime:
    """Rounds up to the next 15-minute boundary so blocks start at clean times."""
    discard = timedelta(minutes=dt.minute % 15, seconds=dt.second, microseconds=dt.microsecond)
    return dt if not discard else dt - discard + SLOT_GRANULARITY

def compute_free_slots(
    busy_windows: list[dict],
    preferences: dict,
    days: int = 7,
    min_block_minutes: int = 60,
    buffer_minutes: int = 15,
    now: datetime | None = None,
) -> list[dict]:
    """Turns Google free/busy windows + study preferences into bookable study slots.

    Slots fall inside the student's preferred window on active days, never overlap a busy
    window (padded by a buffer), and are between min_block_minutes and max_block_hours long.
    Assumes preferred_end is later than preferred_start on the same day.
    """
    now = now or datetime.now(USC_TZ)
    min_block = timedelta(minutes=min_block_minutes)
    max_block = timedelta(hours=preferences.get("max_block_hours", 2))
    buffer = timedelta(minutes=buffer_minutes)
    window_start = time.fromisoformat(preferences["preferred_start"])
    window_end = time.fromisoformat(preferences["preferred_end"])
    active_days = set(preferences["days_active"])

    # Pad busy windows so study blocks never butt up against classes or meetings.
    busy = sorted((parse_iso(b["start"]) - buffer, parse_iso(b["end"]) + buffer) for b in busy_windows)
    slots: list[dict] = []

    def emit(gap_start: datetime, gap_end: datetime) -> None:
        cursor = _round_up(gap_start)
        while gap_end - cursor >= min_block:
            block_end = min(cursor + max_block, gap_end)
            slots.append({
                "slot_id": f"S{len(slots) + 1}",
                "start_iso": cursor.isoformat(),
                "end_iso": block_end.isoformat(),
                "weekday": cursor.strftime("%A"),
                "minutes": int((block_end - cursor).total_seconds() // 60),
            })
            cursor = _round_up(block_end + buffer)  # break between back-to-back blocks

    for offset in range(days):
        day = (now + timedelta(days=offset)).date()
        if day.strftime("%A") not in active_days:
            continue
        cursor = max(datetime.combine(day, window_start, USC_TZ), now)
        day_end = datetime.combine(day, window_end, USC_TZ)
        for b_start, b_end in busy:
            if b_end <= cursor or b_start >= day_end:
                continue
            if b_start > cursor:
                emit(cursor, b_start)
            cursor = max(cursor, b_end)
        if cursor < day_end:
            emit(cursor, day_end)

    return slots
```

Example output passed to Claude:

```json
[
  {"slot_id": "S1", "start_iso": "2026-11-16T17:00:00-08:00", "end_iso": "2026-11-16T19:00:00-08:00", "weekday": "Monday", "minutes": 120},
  {"slot_id": "S2", "start_iso": "2026-11-16T20:15:00-08:00", "end_iso": "2026-11-16T22:00:00-08:00", "weekday": "Monday", "minutes": 105}
]
```

---

### 5.6 Claude AI Scheduling Agent (`backend/app/agent/scheduler.py`)

Claude receives the candidate slots from §5.5 and the student's deadlines (each with an `item_id` and `due_iso`). Its job is the judgment call (what to study, when, and why), not the arithmetic.

```python
import anthropic
import json

client = anthropic.Anthropic()

CLAUDE_SYSTEM_PROMPT = """
You are TrojanClaw, an autonomous academic scheduling assistant for University of Southern California (USC) students.
Your mission is to construct an optimal, realistic study schedule that guarantees deadlines are met while respecting the student's personal calendar commitments and focus preferences.

RULES:
1. You may ONLY schedule study blocks by choosing slot_ids from AVAILABLE STUDY SLOTS. Never invent, shift, split or resize times.
2. Use each slot_id at most once.
3. A study block for an assignment must use a slot that ends before that assignment's due_iso.
4. Prioritize nearer deadlines and larger assignments, and spread work for one assignment across several days instead of cramming the night before.
5. It is fine to leave slots unused. Do not fill every free slot.
6. Flag critical announcements from Piazza (such as deadline changes, exam dates, or office hours).

OUTPUT FORMAT:
Return strictly a valid JSON object matching this schema:
{
  "assignments": [
    {
      "slot_id": "string (must match an AVAILABLE STUDY SLOTS entry)",
      "item_id": "string (must match an UPCOMING BRIGHTSPACE DEADLINES entry)",
      "course": "string",
      "title": "string",
      "reason": "string"
    }
  ],
  "piazza_alerts": [
    "string"
  ],
  "campus_recommendations": [
    {
      "title": "string",
      "host": "string",
      "reason": "string"
    }
  ]
}
"""

def generate_optimized_schedule(preferences: dict, deadlines: list, piazza_items: list, slots: list) -> dict:
    """Asks Claude to assign deadlines to precomputed slots. `slots` comes from compute_free_slots()."""
    prompt = f"""
    STUDENT PREFERENCES:
    {json.dumps(preferences, indent=2)}

    UPCOMING BRIGHTSPACE DEADLINES (each has item_id and due_iso):
    {json.dumps(deadlines, indent=2)}

    PIAZZA ANNOUNCEMENTS & THREADS:
    {json.dumps(piazza_items, indent=2)}

    AVAILABLE STUDY SLOTS (choose from these by slot_id only):
    {json.dumps(slots, indent=2)}

    Generate the study schedule in strictly valid JSON format.
    """
    
    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=2500,
        temperature=0.2,
        system=CLAUDE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    
    raw_text = response.content[0].text.strip()
    # Strip markdown code fencing if present
    if raw_text.startswith("```json"):
        raw_text = raw_text[7:]
    if raw_text.endswith("```"):
        raw_text = raw_text[:-3]
        
    return json.loads(raw_text.strip())
```

---

### 5.7 Schedule Validator (`backend/app/agent/validator.py`)

Every start and end time written to Google Calendar comes from the slot engine, never from model output. The validator maps Claude's picks back to real times and drops anything that breaks a rule. Rejected picks are logged and their count is shown in the dashboard ("2 suggestions skipped").

```python
from app.agent.slots import parse_iso

def materialize_study_blocks(plan: dict, slots: list[dict], deadlines: list[dict]) -> tuple[list[dict], list[dict]]:
    """Turns Claude's slot assignments into concrete study blocks. Returns (blocks, rejected)."""
    slots_by_id = {s["slot_id"]: s for s in slots}
    due_by_item = {d["item_id"]: d.get("due_iso") for d in deadlines}
    used: set[str] = set()
    blocks, rejected = [], []

    for a in plan.get("assignments", []):
        slot = slots_by_id.get(a.get("slot_id"))
        if slot is None:
            rejected.append({**a, "why": "unknown slot_id"})
            continue
        if a["slot_id"] in used:
            rejected.append({**a, "why": "slot already used"})
            continue
        if a.get("item_id") not in due_by_item:
            rejected.append({**a, "why": "unknown item_id"})
            continue
        due = due_by_item[a["item_id"]]
        if due and parse_iso(slot["end_iso"]) > parse_iso(due):
            rejected.append({**a, "why": "slot ends after the deadline"})
            continue

        used.add(a["slot_id"])
        blocks.append({
            "item_id": a["item_id"],
            "course": a.get("course"),
            "title": a.get("title"),
            "start_iso": slot["start_iso"],  # times always come from the slot engine
            "end_iso": slot["end_iso"],
            "reason": a.get("reason", ""),
        })
    return blocks, rejected

def overlaps_busy(block: dict, busy_windows: list[dict]) -> bool:
    """True if a block collides with any busy window (used for the sync-time re-check)."""
    start, end = parse_iso(block["start_iso"]), parse_iso(block["end_iso"])
    return any(parse_iso(b["start"]) < end and start < parse_iso(b["end"]) for b in busy_windows)
```

**End-to-end scheduling run:**

1. `busy = query_user_freebusy(service, days=7)` (§5.4)
2. `slots = compute_free_slots(busy, preferences)` (§5.5)
3. `plan = generate_optimized_schedule(preferences, deadlines, piazza_items, slots)` (§5.6)
4. `blocks, rejected = materialize_study_blocks(plan, slots, deadlines)` (§5.7)
5. **Sync-time re-check:** the student's calendar may change between planning and clicking "Sync All to Google Calendar". Immediately before inserting, call `query_user_freebusy` again and skip any block where `overlaps_busy(block, fresh_busy)` is true. Insert the rest with `insert_trojanclaw_study_block` and record them in `synced_calendar_events`.

---

### 5.8 TrojanClaw Sync Helper (`sync_helper/`)

Runs on the student's computer (§2.2). It is the only component that talks to USC Brightspace or Piazza with the student's login.

```text
sync_helper/
├── pyproject.toml              # installs the `trojanclaw-sync` command
└── trojanclaw_sync/
    ├── cli.py                  # pair | login | run (poll jobs + scheduled sync) | sync-once | status
    ├── keychain.py             # stores the sync token in the OS keychain (`keyring` package)
    ├── mcp_clients.py          # starts both MCP servers over stdio and enforces the allowlist
    ├── normalize.py            # MCP JSON -> student_context_items records with external_id
    └── uploader.py             # talks to the Sync API below
```

**Sync API (hosted backend, `backend/app/api/routes/sync.py`):**

| Endpoint | Caller | Purpose |
| :--- | :--- | :--- |
| `POST /sync/pairing-codes` | Dashboard (Google session) | Create a one-time pairing code (10-minute expiry) |
| `POST /sync/pair` | Helper | Exchange the pairing code for a sync token; creates a `sync_devices` row |
| `GET /sync/jobs/next` | Helper (sync token) | Claim the oldest `queued` job for this student, if any |
| `POST /sync/jobs/{id}/status` | Helper (sync token) | Report `running`, `succeeded`, `failed` or `needs_login` |
| `POST /sync/upload` | Helper (sync token) | Upsert one source's normalized records |
| `POST /sync/jobs` | Dashboard (Google session) | "Sync now": queue a job |

```json
POST /sync/upload
Authorization: Bearer <sync token>
{
  "job_id": "uuid-or-null",
  "source": "brightspace",
  "complete_snapshot": true,
  "items": [
    {
      "external_id": "d2l:assignment:123456",
      "course_or_org": "CSCI 570",
      "title": "Homework 4",
      "content": "Assignment: Homework 4\nDue Date: 2026-11-20T23:59:00-08:00\nDetails: ...",
      "event_timestamp": "2026-11-20T23:59:00-08:00",
      "raw_metadata": {"type": "assignment"}
    }
  ]
}
```

Backend rules for `/sync/upload`:
* `user_id` comes **only** from the sync token, never from the request body. Revoked devices get `401`.
* Upsert on `(user_id, source, external_id)` and re-embed only items whose `content` changed.
* `complete_snapshot: true` means the helper fetched every course for that source successfully. Only then may the backend remove that student's items for the source that are missing from the batch (for example, a deleted assignment). Partial fetches send `false` and nothing is removed.
* Cap each batch (for example 2,000 items / 2 MB) and reject unknown `source` values.
* Treat uploaded text as untrusted data. It is shown to Claude as context, never as instructions.

**MCP clients with an enforced allowlist (`mcp_clients.py`):**

```python
import json
import os
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BRIGHTSPACE_SERVER = StdioServerParameters(
    command="npx",
    args=["-y", "brightspace-mcp-server@<pinned-version>"],
    env={**os.environ, "D2L_HEADLESS": "false"},  # visible browser for USC SSO + Duo
)
PIAZZA_SERVER = StdioServerParameters(
    command="uvx",
    args=["mcp-piazza"],
    env={**os.environ, "PIAZZA_KEEP_UNREAD": "true"},  # never mark posts as read
)

# Read-only tools only (§2.3, §2.4). Fill in the exact names from each server's
# tools/list output during the USC gate test; anything not listed here is refused.
ALLOWED_TOOLS: dict[str, set[str]] = {"brightspace": set(), "piazza": set()}

async def call_allowed(session: ClientSession, source: str, tool: str, args: dict) -> list:
    if tool not in ALLOWED_TOOLS[source]:
        raise PermissionError(f"{tool} is not on the read-only allowlist for {source}")
    result = await session.call_tool(tool, args)
    payloads = []
    for block in result.content:
        if block.type != "text":
            continue
        try:
            payloads.append(json.loads(block.text))
        except json.JSONDecodeError:
            pass  # drops the server's non-JSON "update notice" blocks
    return payloads

@asynccontextmanager
async def open_session(server: StdioServerParameters):
    """Usage: async with open_session(BRIGHTSPACE_SERVER) as session: ..."""
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session
```

---

## 6. TEAM SPRINT ROSTER & TASK BREAKDOWN

To divide the work across teammates and parallel AI agents, use this execution split:

| Teammate / Agent | Ownership Area | Deliverables |
| :--- | :--- | :--- |
| **Teammate 1 (Backend & Auth)** | Google OAuth & Calendar Sync | • Google Cloud OAuth setup<br>• `freebusy` & event insertion endpoints<br>• Free-slot engine (`slots.py`) & schedule validator (`validator.py`)<br>• Supabase token persistence |
| **Teammate 2 (Connectors & DB)** | Sync Helper, ingestion & Supabase `pgvector` | • USC SSO gate test for `brightspace-mcp-server` (§2.3), done first<br>• Sync Helper: pairing, visible-browser login, MCP allowlist, normalizer, uploader (§5.8)<br>• Sync API: pairing, jobs, `/sync/upload` upserts (§5.8)<br>• `.ics` fallback parser & demo seed dataset<br>• EngageSC scraper<br>• SQL migration incl. `sync_devices` & `sync_jobs` |
| **Teammate 3 (Frontend & AI)** | React UI & Claude Agent | • Vite + Tailwind + Lucide setup<br>• Landing, Onboarding, and Dashboard views<br>• Claude 3.5 Sonnet prompt engineering & chat UI |

---

## 7. ENVIRONMENT VARIABLES TEMPLATE (`.env.example`)

### Backend (`backend/.env`)
```env
# Anthropic Claude
ANTHROPIC_API_KEY=sk-ant-api03-...

# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJh...
SUPABASE_ANON_KEY=eyJh...

# Google OAuth (from Google Cloud Console)
GOOGLE_CLIENT_ID=xxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxx
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback

# Embeddings API Key (Voyage AI or OpenAI)
EMBEDDING_API_KEY=...
```

### Sync Helper (student's computer, `~/.config/trojanclaw-sync/config.env`)
```env
# Hosted TrojanClaw backend the helper uploads to
TROJANCLAW_API_URL=https://your-trojanclaw-backend.example.com

# Brightspace MCP (pin an exact version; USC uses the generic SSO + Duo path)
BRIGHTSPACE_MCP_VERSION=<pinned-version>
D2L_HEADLESS=false

# Piazza MCP (never mark posts as read)
PIAZZA_KEEP_UNREAD=true

# Scheduled sync interval while the helper is running
SYNC_INTERVAL_MINUTES=120
```
The sync token is not in this file. It lives in the OS keychain after `trojanclaw-sync pair`.

### Frontend (`frontend/.env`)
```env
VITE_API_BASE_URL=http://localhost:8000
VITE_SUPABASE_URL=https://your-project.supabase.co
VITE_SUPABASE_ANON_KEY=eyJh...
```
