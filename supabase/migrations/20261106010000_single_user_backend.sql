create table if not exists public.app_users (
    id uuid primary key,
    display_name text not null,
    profile jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

insert into public.app_users (id, display_name)
values ('00000000-0000-0000-0000-000000000001', 'TrojanClaw User')
on conflict (id) do nothing;

alter table public.documents
    add column if not exists user_id uuid
    references public.app_users(id)
    default '00000000-0000-0000-0000-000000000001';

update public.documents
set user_id = '00000000-0000-0000-0000-000000000001'
where user_id is null;

alter table public.documents alter column user_id set not null;
create index if not exists idx_documents_user_id on public.documents(user_id);

create table if not exists public.integration_connections (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.app_users(id) on delete cascade,
    provider text not null check (provider in ('piazza', 'brightspace')),
    status text not null default 'not_configured',
    external_account_id text,
    metadata jsonb not null default '{}'::jsonb,
    last_synced_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (user_id, provider)
);

insert into public.integration_connections (user_id, provider)
values
    ('00000000-0000-0000-0000-000000000001', 'piazza'),
    ('00000000-0000-0000-0000-000000000001', 'brightspace')
on conflict (user_id, provider) do nothing;

alter table public.app_users enable row level security;
alter table public.integration_connections enable row level security;
revoke all on table public.app_users from anon, authenticated;
revoke all on table public.integration_connections from anon, authenticated;
