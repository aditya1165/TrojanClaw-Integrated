-- Anonymous USC attempts are bound to a secret HttpOnly browser cookie, not an IP.
create table public.remote_usc_attempts (
    id uuid primary key,
    owner_hash text not null unique,
    encrypted_payload bytea not null,
    expires_at timestamptz not null default now() + interval '10 minutes',
    busy boolean not null default false,
    created_at timestamptz not null default now()
);
create index remote_usc_attempts_expiry on public.remote_usc_attempts (expires_at);
alter table public.remote_usc_attempts enable row level security;
alter table public.remote_usc_attempts force row level security;
revoke all on public.remote_usc_attempts from public, anon, authenticated;
grant select, insert, update, delete on public.remote_usc_attempts to service_role;
create policy remote_usc_owner on public.remote_usc_attempts for all
    using (owner_hash = current_setting('app.usc_attempt_owner', true))
    with check (owner_hash = current_setting('app.usc_attempt_owner', true));
do $$ begin
    if exists (select 1 from pg_roles where rolname = 'trojanclaw_backend') then
        grant select, insert, update, delete on public.remote_usc_attempts to trojanclaw_backend;
    end if;
end $$;

-- A username collision must never link a USC identity to a password account.
-- Also prevent concurrent first logins creating two accounts for one identity.
create unique index app_users_usc_identity_unique
    on public.app_users ((profile->'usc'->>'brightspace_user_id'))
    where profile->'usc'->>'brightspace_user_id' is not null;
