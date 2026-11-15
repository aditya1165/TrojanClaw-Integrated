-- Browser clients never receive Google credentials or write these tables directly.
create table public.google_calendar_connections (
    user_id uuid primary key references public.app_users(id) on delete cascade,
    id uuid not null unique,
    calendar_id text not null,
    calendar_label text not null,
    encrypted_tokens text not null
);
create table public.google_calendar_oauth_states (
    state_hash text primary key,
    user_id uuid not null references public.app_users(id) on delete cascade,
    session_hash text not null,
    verifier text not null,
    expires_at timestamptz not null
);
create index google_calendar_oauth_user_idx on public.google_calendar_oauth_states(user_id);
create index google_calendar_oauth_expiry_idx on public.google_calendar_oauth_states(expires_at);

create table public.calendar_proposals (
    id uuid primary key,
    user_id uuid not null references public.app_users(id) on delete cascade,
    connection_id uuid not null references public.google_calendar_connections(id) on delete cascade,
    version integer not null default 1 check (version > 0),
    event jsonb not null check (jsonb_typeof(event) = 'object'),
    status text not null default 'pending' check (status in ('pending', 'approved', 'applied', 'cancelled')),
    created_at timestamptz not null default now(),
    expires_at timestamptz not null default now() + interval '24 hours',
    approved_at timestamptz,
    processing_until timestamptz,
    event_url text
);
create index calendar_proposals_user_created_idx on public.calendar_proposals(user_id, created_at desc);
create index calendar_proposals_connection_idx on public.calendar_proposals(connection_id);

alter table public.google_calendar_connections enable row level security;
alter table public.google_calendar_oauth_states enable row level security;
alter table public.calendar_proposals enable row level security;
revoke all on public.google_calendar_connections, public.google_calendar_oauth_states,
    public.calendar_proposals from public, anon, authenticated;
