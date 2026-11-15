-- TrojanClaw core academic schema (hackathon demo: one user, schema already multi-user ready).
--
-- Data comes from:
--   * Brightspace via brightspace-mcp-server (see brightspace.md)
--   * Piazza via mcp-piazza (see piazza.md)
--   * Google Calendar (free/busy + TrojanClaw study blocks)
--   * The TrojanClaw agent itself (proposals, conversations, tool calls)
--
-- Design:
--   * Every row carries user_id. The demo uses the single user created in
--     20261106010000_single_user_backend.sql.
--   * Raw MCP results are kept in source_records so data can be re-normalized
--     without calling Brightspace or Piazza again.
--   * Typed tables (courses, deliverables, grades, ...) are what the app and the
--     agent's tools query. content_chunks holds embeddings over all of them.
--   * No login secrets (Brightspace cookies, Piazza session, passwords) are ever
--     stored here. They stay on the laptop running the MCP servers.

create extension if not exists btree_gist with schema extensions;

-- ---------------------------------------------------------------------------
-- Shared helpers
-- ---------------------------------------------------------------------------

create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = public
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- 1. User and connections
-- ---------------------------------------------------------------------------

alter table public.app_users
    add column if not exists email text unique,
    add column if not exists timezone text not null default 'America/Los_Angeles',
    add column if not exists study_preferences jsonb not null default '{
        "preferred_start": "17:00",
        "preferred_end": "22:00",
        "max_block_hours": 2,
        "days_active": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Sunday"]
    }'::jsonb;

-- integration_connections: one row per user and provider, now including Google.
alter table public.integration_connections
    drop constraint if exists integration_connections_provider_check;
alter table public.integration_connections
    add constraint integration_connections_provider_check
    check (provider in ('brightspace', 'piazza', 'google'));
alter table public.integration_connections
    add column if not exists last_error text,
    add column if not exists last_success_at timestamptz;
alter table public.integration_connections
    drop constraint if exists integration_connections_status_check;
alter table public.integration_connections
    add constraint integration_connections_status_check
    check (status in ('not_configured', 'connected', 'needs_login', 'error'));

insert into public.integration_connections (user_id, provider)
values ('00000000-0000-0000-0000-000000000001', 'google')
on conflict (user_id, provider) do nothing;

-- Google OAuth for Calendar. The refresh token is encrypted by the backend
-- (app-level key) before it is written here.
create table if not exists public.google_credentials (
    user_id uuid primary key references public.app_users(id) on delete cascade,
    google_email text not null,
    refresh_token_encrypted text not null,
    scopes text[] not null default '{}',
    trojanclaw_calendar_id text, -- dedicated "TrojanClaw" calendar, created on first sync
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- 2. Sync bookkeeping
-- ---------------------------------------------------------------------------

create table if not exists public.sync_runs (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.app_users(id) on delete cascade,
    provider text not null check (provider in ('brightspace', 'piazza', 'google', 'demo', 'ics')),
    trigger text not null default 'manual' check (trigger in ('manual', 'scheduled', 'agent', 'startup')),
    status text not null default 'queued'
        check (status in ('queued', 'running', 'succeeded', 'partial', 'failed', 'needs_login')),
    records_fetched integer not null default 0,
    items_upserted integer not null default 0,
    items_changed integer not null default 0,
    error text,
    requested_at timestamptz not null default now(),
    started_at timestamptz,
    finished_at timestamptz
);
create index if not exists idx_sync_runs_user_provider
    on public.sync_runs (user_id, provider, requested_at desc);

-- Where each incremental step left off (e.g. Piazza recent_activity "since").
create table if not exists public.sync_cursors (
    user_id uuid not null references public.app_users(id) on delete cascade,
    provider text not null,
    step text not null,
    cursor jsonb not null,
    updated_at timestamptz not null default now(),
    primary key (user_id, provider, step)
);

-- Raw MCP tool results, one row per source object. Lets the normalizer be
-- re-run (or fixed) without calling Brightspace/Piazza again.
create table if not exists public.source_records (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.app_users(id) on delete cascade,
    provider text not null check (provider in ('brightspace', 'piazza', 'demo', 'ics')),
    tool text not null,                -- e.g. get_upcoming_due_dates, get_posts
    external_id text not null,         -- the object's id inside that tool's output
    payload jsonb not null,
    payload_hash text not null,        -- unchanged payloads are skipped
    sync_run_id uuid references public.sync_runs(id) on delete set null,
    fetched_at timestamptz not null default now(),
    unique (user_id, provider, tool, external_id)
);
create index if not exists idx_source_records_user_tool
    on public.source_records (user_id, provider, tool);

-- ---------------------------------------------------------------------------
-- 3. Courses (one row per real course, linked to each platform's copy)
-- ---------------------------------------------------------------------------

create table if not exists public.courses (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.app_users(id) on delete cascade,
    code text not null,                -- normalized, e.g. 'CSCI 570'
    name text,
    term text,                         -- e.g. 'Fall 2026'
    is_active boolean not null default true,
    color text,                        -- derived from code by the frontend if null
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (user_id, code, term)
);

-- Brightspace orgUnit / Piazza network for each course. Lets the two be matched.
create table if not exists public.course_sources (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.app_users(id) on delete cascade,
    course_id uuid not null references public.courses(id) on delete cascade,
    provider text not null check (provider in ('brightspace', 'piazza')),
    external_id text not null,         -- Brightspace orgUnitId / Piazza network id
    external_name text,
    role text,                         -- student, TA, ...
    folders text[],                    -- Piazza folders (hw1, midterm, ...)
    raw jsonb not null default '{}'::jsonb,
    last_synced_at timestamptz,
    unique (user_id, provider, external_id)
);
create index if not exists idx_course_sources_course on public.course_sources (course_id);

-- Instructors and TAs only (Piazza get_course_info, Brightspace get_roster
-- without includeStudents). Classmates are never stored.
create table if not exists public.course_staff (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.app_users(id) on delete cascade,
    course_id uuid not null references public.courses(id) on delete cascade,
    provider text not null check (provider in ('brightspace', 'piazza')),
    name text not null,
    role text,                         -- instructor, TA, ...
    email text,
    unique (course_id, provider, name)
);

-- Office hours as published (Piazza course info, syllabus). Parsed fields are
-- optional because the source is free text.
create table if not exists public.office_hours (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.app_users(id) on delete cascade,
    course_id uuid not null references public.courses(id) on delete cascade,
    provider text not null check (provider in ('brightspace', 'piazza')),
    staff_name text,
    raw_text text not null,
    weekday smallint check (weekday between 0 and 6), -- 0 = Monday
    start_time time,
    end_time time,
    location text,
    updated_at timestamptz not null default now()
);
create index if not exists idx_office_hours_course on public.office_hours (course_id);

-- ---------------------------------------------------------------------------
-- 4. Brightspace academic data
-- ---------------------------------------------------------------------------

-- Anything with a due date: assignments, quizzes, graded discussions
-- (get_upcoming_due_dates + get_assignments merge into one row per item).
create table if not exists public.deliverables (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.app_users(id) on delete cascade,
    course_id uuid references public.courses(id) on delete set null,
    provider text not null check (provider in ('brightspace', 'demo', 'ics')),
    external_id text not null,
    kind text not null default 'assignment'
        check (kind in ('assignment', 'quiz', 'discussion', 'exam', 'other')),
    title text not null,
    description text,
    available_at timestamptz,
    due_at timestamptz,
    points_possible numeric,
    submission_status text check (submission_status in ('not_submitted', 'submitted', 'graded')),
    has_rubric boolean not null default false,
    url text,
    raw jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (user_id, provider, external_id)
);
create index if not exists idx_deliverables_user_due on public.deliverables (user_id, due_at);
create index if not exists idx_deliverables_course on public.deliverables (course_id);

-- History of due-date changes, written by a trigger. Drives "replan on change".
create table if not exists public.deliverable_due_changes (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.app_users(id) on delete cascade,
    deliverable_id uuid not null references public.deliverables(id) on delete cascade,
    old_due_at timestamptz,
    new_due_at timestamptz,
    detected_at timestamptz not null default now(),
    handled_at timestamptz             -- set when the agent has replanned for it
);
create index if not exists idx_due_changes_unhandled
    on public.deliverable_due_changes (user_id) where handled_at is null;

create or replace function public.record_due_change()
returns trigger
language plpgsql
set search_path = public
as $$
begin
    if new.due_at is distinct from old.due_at then
        insert into public.deliverable_due_changes (user_id, deliverable_id, old_due_at, new_due_at)
        values (new.user_id, new.id, old.due_at, new.due_at);
    end if;
    return new;
end;
$$;

-- Grade items (get_my_grades). Feedback and rubrics are read live, not stored.
create table if not exists public.grades (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.app_users(id) on delete cascade,
    course_id uuid references public.courses(id) on delete set null,
    deliverable_id uuid references public.deliverables(id) on delete set null,
    provider text not null default 'brightspace' check (provider in ('brightspace', 'demo')),
    external_id text not null,
    item_name text not null,
    points_earned numeric,
    points_possible numeric,
    percentage numeric,
    weight numeric,                    -- share of the final grade, when published
    comments text,
    graded_at timestamptz,
    raw jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now(),
    unique (user_id, provider, external_id)
);
create index if not exists idx_grades_course on public.grades (course_id);

-- Exams, labs, review sessions, schedule changes (get_calendar_events).
create table if not exists public.course_events (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.app_users(id) on delete cascade,
    course_id uuid references public.courses(id) on delete set null,
    provider text not null check (provider in ('brightspace', 'demo', 'ics')),
    external_id text not null,
    kind text not null default 'event' check (kind in ('exam', 'lab', 'review', 'lecture', 'event')),
    title text not null,
    description text,
    starts_at timestamptz not null,
    ends_at timestamptz,
    location text,
    raw jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now(),
    unique (user_id, provider, external_id)
);
create index if not exists idx_course_events_user_start on public.course_events (user_id, starts_at);

-- Brightspace announcements (get_announcements). Piazza instructor notes are
-- posts and live in piazza_posts.
create table if not exists public.announcements (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.app_users(id) on delete cascade,
    course_id uuid references public.courses(id) on delete set null,
    provider text not null default 'brightspace' check (provider in ('brightspace', 'demo')),
    external_id text not null,
    title text not null,
    body text,
    author text,
    posted_at timestamptz,
    is_pinned boolean not null default false,
    has_attachments boolean not null default false,
    url text,
    raw jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now(),
    unique (user_id, provider, external_id)
);
create index if not exists idx_announcements_user_posted
    on public.announcements (user_id, posted_at desc);

-- Syllabus, content modules/topics, discussion forums/topics/posts
-- (get_syllabus, get_course_content, get_discussions). Tree via parent_external_id.
create table if not exists public.course_documents (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.app_users(id) on delete cascade,
    course_id uuid references public.courses(id) on delete cascade,
    provider text not null check (provider in ('brightspace', 'piazza', 'demo')),
    kind text not null check (kind in (
        'syllabus', 'content_module', 'content_topic',
        'discussion_forum', 'discussion_topic', 'discussion_post', 'course_info'
    )),
    external_id text not null,
    parent_external_id text,
    title text not null,
    body_text text,
    url text,
    raw jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now(),
    unique (user_id, provider, kind, external_id)
);
create index if not exists idx_course_documents_course on public.course_documents (course_id, kind);

-- The student's own groups (get_my_groups). Member names only.
create table if not exists public.course_groups (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.app_users(id) on delete cascade,
    course_id uuid references public.courses(id) on delete cascade,
    external_id text not null,
    name text not null,
    category text,
    member_names text[] not null default '{}',
    updated_at timestamptz not null default now(),
    unique (user_id, external_id)
);

-- ---------------------------------------------------------------------------
-- 5. Piazza
-- ---------------------------------------------------------------------------

-- Full threads (get_posts / recent_activity / list_instructor_notes).
create table if not exists public.piazza_posts (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.app_users(id) on delete cascade,
    course_id uuid references public.courses(id) on delete set null,
    external_id text not null,         -- Piazza post id
    post_number integer,               -- the @123 number shown in Piazza
    post_type text check (post_type in ('question', 'note', 'poll')),
    subject text not null,
    body text,
    folders text[] not null default '{}',
    is_instructor_note boolean not null default false,
    is_pinned boolean not null default false,
    is_resolved boolean,
    instructor_answer text,
    student_answer text,
    followups jsonb not null default '[]'::jsonb,
    followup_count integer not null default 0,
    created_at_source timestamptz,
    last_activity_at timestamptz,
    url text,
    raw jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now(),
    unique (user_id, external_id)
);
create index if not exists idx_piazza_posts_user_activity
    on public.piazza_posts (user_id, last_activity_at desc);
create index if not exists idx_piazza_posts_course on public.piazza_posts (course_id);
create index if not exists idx_piazza_posts_notes
    on public.piazza_posts (user_id, last_activity_at desc) where is_instructor_note;

-- ---------------------------------------------------------------------------
-- 6. Semantic search over everything above
-- ---------------------------------------------------------------------------

create table if not exists public.content_chunks (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.app_users(id) on delete cascade,
    course_id uuid references public.courses(id) on delete cascade,
    source_table text not null check (source_table in (
        'deliverables', 'announcements', 'piazza_posts', 'course_documents',
        'course_events', 'office_hours'
    )),
    source_id uuid not null,
    chunk_index integer not null default 0,
    chunk_text text not null,
    content_hash text not null,        -- re-embed only when this changes
    embedding extensions.vector(1536),
    created_at timestamptz not null default now(),
    unique (source_table, source_id, chunk_index)
);
create index if not exists idx_content_chunks_user on public.content_chunks (user_id, course_id);
create index if not exists content_chunks_embedding_hnsw_idx
    on public.content_chunks using hnsw (embedding extensions.vector_cosine_ops);

create or replace function public.match_content_chunks(
    query_embedding extensions.vector(1536),
    filter_user_id uuid,
    match_count integer default 8,
    min_similarity double precision default 0.3,
    filter_course_id uuid default null
)
returns table (
    chunk_id uuid,
    source_table text,
    source_id uuid,
    course_id uuid,
    chunk_text text,
    similarity double precision
)
language sql
stable
set search_path = public, extensions
as $$
    select
        c.id,
        c.source_table,
        c.source_id,
        c.course_id,
        c.chunk_text,
        1 - (c.embedding <=> query_embedding) as similarity
    from public.content_chunks c
    where c.user_id = filter_user_id
      and c.embedding is not null
      and (filter_course_id is null or c.course_id = filter_course_id)
      and 1 - (c.embedding <=> query_embedding) >= min_similarity
    order by c.embedding <=> query_embedding
    limit match_count;
$$;

-- ---------------------------------------------------------------------------
-- 7. Study planning and Google Calendar
-- ---------------------------------------------------------------------------

-- Agent runs and chat history (also shown in the UI as tool activity).
create table if not exists public.conversations (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.app_users(id) on delete cascade,
    title text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.conversation_messages (
    id uuid primary key default gen_random_uuid(),
    conversation_id uuid not null references public.conversations(id) on delete cascade,
    role text not null check (role in ('user', 'assistant')),
    content jsonb not null,            -- Anthropic content blocks, stored as returned
    created_at timestamptz not null default now()
);
create index if not exists idx_conversation_messages_conv
    on public.conversation_messages (conversation_id, created_at);

create table if not exists public.agent_runs (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.app_users(id) on delete cascade,
    conversation_id uuid references public.conversations(id) on delete set null,
    goal text not null,                -- chat, plan_week, replan_on_change, weekly_brief
    model text not null,
    status text not null default 'running' check (status in ('running', 'succeeded', 'failed', 'stopped')),
    input_tokens integer,
    output_tokens integer,
    error text,
    started_at timestamptz not null default now(),
    finished_at timestamptz
);
create index if not exists idx_agent_runs_user on public.agent_runs (user_id, started_at desc);

create table if not exists public.agent_tool_calls (
    id uuid primary key default gen_random_uuid(),
    run_id uuid not null references public.agent_runs(id) on delete cascade,
    tool text not null,
    arguments jsonb not null default '{}'::jsonb,
    result_summary text,
    is_error boolean not null default false,
    duration_ms integer,
    created_at timestamptz not null default now()
);
create index if not exists idx_agent_tool_calls_run on public.agent_tool_calls (run_id, created_at);

create table if not exists public.study_plan_proposals (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.app_users(id) on delete cascade,
    agent_run_id uuid references public.agent_runs(id) on delete set null,
    goal text not null,
    summary text,
    rejected jsonb not null default '[]'::jsonb, -- validator rejections with reasons
    status text not null default 'proposed'
        check (status in ('proposed', 'applied', 'dismissed', 'expired')),
    created_at timestamptz not null default now(),
    applied_at timestamptz
);
create index if not exists idx_proposals_user on public.study_plan_proposals (user_id, created_at desc);

-- Each study block. Times always come from the slot engine, never from the model.
-- Applied blocks can never overlap each other (exclusion constraint).
create table if not exists public.study_blocks (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.app_users(id) on delete cascade,
    proposal_id uuid not null references public.study_plan_proposals(id) on delete cascade,
    deliverable_id uuid references public.deliverables(id) on delete set null,
    title text not null,
    reason text,
    starts_at timestamptz not null,
    ends_at timestamptz not null,
    status text not null default 'proposed'
        check (status in ('proposed', 'applied', 'skipped', 'removed')),
    google_event_id text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (ends_at > starts_at),
    constraint study_blocks_no_overlap exclude using gist (
        user_id with =,
        tstzrange(starts_at, ends_at, '[)') with &&
    ) where (status = 'applied')
);
create index if not exists idx_study_blocks_user_start on public.study_blocks (user_id, starts_at);

-- ---------------------------------------------------------------------------
-- 8. Campus (shared, not per user)
-- ---------------------------------------------------------------------------

create table if not exists public.campus_events (
    id uuid primary key default gen_random_uuid(),
    source_id text not null,           -- id from config/campus_sources.yaml
    external_key text not null,
    title text not null,
    host text,
    starts_at timestamptz,
    location text,
    url text,
    description text,
    fetched_at timestamptz not null default now(),
    unique (source_id, external_key)
);
create index if not exists idx_campus_events_start on public.campus_events (starts_at);

-- ---------------------------------------------------------------------------
-- 9. Views for the dashboard and the agent's tools
-- ---------------------------------------------------------------------------

create or replace view public.upcoming_deadlines
with (security_invoker = true) as
select
    d.id,
    d.user_id,
    d.course_id,
    c.code as course_code,
    d.kind,
    d.title,
    d.due_at,
    d.submission_status,
    d.points_possible,
    d.url
from public.deliverables d
left join public.courses c on c.id = d.course_id
where d.due_at >= now() - interval '1 day'
  and coalesce(d.submission_status, 'not_submitted') <> 'graded';

-- One feed of everything new: announcements, instructor notes, due-date changes.
create or replace view public.activity_feed
with (security_invoker = true) as
select a.user_id, a.course_id, 'announcement'::text as item_type, a.id as item_id,
       a.title, a.posted_at as happened_at, a.is_pinned
from public.announcements a
union all
select p.user_id, p.course_id, 'instructor_note', p.id,
       p.subject, coalesce(p.last_activity_at, p.created_at_source), p.is_pinned
from public.piazza_posts p
where p.is_instructor_note
union all
select ch.user_id, d.course_id, 'due_date_change', ch.id,
       d.title, ch.detected_at, false
from public.deliverable_due_changes ch
join public.deliverables d on d.id = ch.deliverable_id;

-- ---------------------------------------------------------------------------
-- 10. Triggers
-- ---------------------------------------------------------------------------

drop trigger if exists deliverables_due_change on public.deliverables;
create trigger deliverables_due_change
    after update of due_at on public.deliverables
    for each row execute function public.record_due_change();

do $$
declare
    t text;
begin
    foreach t in array array[
        'google_credentials', 'courses', 'deliverables', 'grades', 'course_events',
        'announcements', 'course_documents', 'course_groups', 'piazza_posts',
        'office_hours', 'conversations', 'study_blocks'
    ]
    loop
        execute format('drop trigger if exists %I_updated_at on public.%I', t, t);
        execute format(
            'create trigger %I_updated_at before update on public.%I '
            'for each row execute function public.set_updated_at()', t, t
        );
    end loop;
end;
$$;

-- ---------------------------------------------------------------------------
-- 11. Access: server-managed tables, same policy as the existing migrations
-- ---------------------------------------------------------------------------

do $$
declare
    t text;
begin
    foreach t in array array[
        'google_credentials', 'sync_runs', 'sync_cursors', 'source_records',
        'courses', 'course_sources', 'course_staff', 'office_hours',
        'deliverables', 'deliverable_due_changes', 'grades', 'course_events',
        'announcements', 'course_documents', 'course_groups', 'piazza_posts',
        'content_chunks', 'conversations', 'conversation_messages',
        'agent_runs', 'agent_tool_calls', 'study_plan_proposals', 'study_blocks',
        'campus_events'
    ]
    loop
        execute format('alter table public.%I enable row level security', t);
        execute format('revoke all on table public.%I from anon, authenticated', t);
    end loop;
end;
$$;

revoke all on public.upcoming_deadlines from anon, authenticated;
revoke all on public.activity_feed from anon, authenticated;
revoke execute on function public.match_content_chunks(extensions.vector, uuid, integer, double precision, uuid)
    from anon, authenticated, public;
