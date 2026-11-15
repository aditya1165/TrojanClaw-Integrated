-- Public EngageSC events are shared; linking is a preference per account.
alter table public.integration_connections drop constraint if exists integration_connections_provider_check;
alter table public.integration_connections add constraint integration_connections_provider_check
    check (provider in ('brightspace', 'piazza', 'google', 'engagesc'));

alter table public.campus_events add column if not exists ends_at timestamptz;
alter table public.campus_events add column if not exists all_day boolean not null default false;

create table public.campus_event_sources (
    source_id text primary key,
    fetched_at timestamptz not null,
    event_count integer not null check (event_count >= 0)
);
alter table public.campus_event_sources enable row level security;
revoke all on public.campus_event_sources from public, anon, authenticated;
