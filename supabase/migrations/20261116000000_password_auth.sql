-- Application accounts are separate from extension credentials and Supabase Auth.
alter table public.app_users add column if not exists username text;
create unique index if not exists app_users_username_key on public.app_users (username);
alter table public.app_users add constraint app_users_username_format
    check (username is null or username ~ '^[a-z0-9_]{3,32}$');

create table public.account_credentials (
    user_id uuid primary key references public.app_users(id) on delete cascade,
    password_hash text not null,
    created_at timestamptz not null default now()
);

create table public.account_sessions (
    token_hash text primary key,
    user_id uuid not null references public.app_users(id) on delete cascade,
    created_at timestamptz not null default now(),
    expires_at timestamptz not null,
    check (length(token_hash) = 64),
    check (expires_at > created_at)
);
create index account_sessions_user_id_idx on public.account_sessions (user_id);
create index account_sessions_expires_at_idx on public.account_sessions (expires_at);

alter table public.account_credentials enable row level security;
alter table public.account_sessions enable row level security;
-- Only the trusted backend database role may access passwords or sessions.
revoke all on public.account_credentials, public.account_sessions from public, anon, authenticated;
