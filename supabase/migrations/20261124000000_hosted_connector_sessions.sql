-- Private connector credentials. Never expose this table through the browser SDK.
create table public.user_connector_sessions (
    user_id uuid not null references public.app_users(id) on delete cascade,
    provider text not null check (provider in ('brightspace', 'handshake')),
    state text not null default 'not_configured'
        check (state in ('not_configured', 'connecting', 'connected', 'needs_login', 'revoking')),
    encrypted_session bytea,
    encrypted_login bytea,
    operation_id uuid,
    login_expires_at timestamptz,
    updated_at timestamptz not null default now(),
    primary key (user_id, provider),
    check (state <> 'connecting' or (operation_id is not null and login_expires_at is not null))
);

alter table public.user_connector_sessions enable row level security;
alter table public.user_connector_sessions force row level security;
revoke all on public.user_connector_sessions from public, anon, authenticated;

-- The trusted backend sets this transaction-local value from its authenticated
-- AccountProfile, never from request JSON. Explicit user predicates remain on
-- every query, including deployments using an administrative database role.
create policy connector_owner_only on public.user_connector_sessions
    for all
    using (user_id::text = current_setting('app.connector_user_id', true))
    with check (user_id::text = current_setting('app.connector_user_id', true));

grant select, insert, update, delete on public.user_connector_sessions to service_role;
create index user_connector_sessions_expiring_idx
    on public.user_connector_sessions (login_expires_at) where state = 'connecting';

-- Shared by all API replicas; no process-local production login rate limits.
create table public.auth_rate_limits (
    client_hash text primary key check (length(client_hash) = 64),
    window_started_at timestamptz not null default now(),
    attempts integer not null check (attempts > 0)
);
create index auth_rate_limits_window_idx on public.auth_rate_limits (window_started_at);
alter table public.auth_rate_limits enable row level security;
revoke all on public.auth_rate_limits from public, anon, authenticated;
grant select, insert, update, delete on public.auth_rate_limits to service_role;
