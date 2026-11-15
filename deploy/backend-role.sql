-- Run after migrations as the database administrator. Set the login password
-- interactively with psql's \password trojanclaw_api (never commit it).
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'trojanclaw_backend') then
        create role trojanclaw_backend nologin;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'trojanclaw_api') then
        create role trojanclaw_api login inherit nosuperuser nocreatedb nocreaterole noreplication nobypassrls;
    end if;
    execute format('grant connect on database %I to trojanclaw_backend', current_database());
end;
$$;
alter role trojanclaw_backend nosuperuser nocreatedb nocreaterole noreplication nobypassrls;
alter role trojanclaw_api nosuperuser nocreatedb nocreaterole noreplication nobypassrls;
grant trojanclaw_backend to trojanclaw_api;
grant usage on schema public, extensions to trojanclaw_backend;
grant select on public.documents to trojanclaw_backend;
grant select, insert, update, delete on
    public.app_users, public.account_credentials, public.account_sessions,
    public.auth_rate_limits, public.integration_connections,
    public.campus_events, public.campus_event_sources,
    public.user_connector_sessions, public.remote_usc_attempts to trojanclaw_backend;

-- Current Google-first onboarding, calendar approvals, saved chats and course reads.
grant select, insert, update, delete on
    public.google_calendar_connections, public.google_calendar_oauth_states,
    public.calendar_proposals, public.conversations, public.conversation_messages
    to trojanclaw_backend;
grant select on public.courses, public.deliverables, public.deliverable_due_changes,
    public.course_events, public.announcements, public.course_documents,
    public.piazza_posts, public.content_chunks, public.upcoming_deadlines,
    public.activity_feed to trojanclaw_backend;

do $$
declare relation_name text;
begin
    foreach relation_name in array array[
        'google_calendar_connections', 'google_calendar_oauth_states', 'calendar_proposals',
        'conversations', 'conversation_messages'
    ] loop
        execute format('create policy backend_server_access on public.%I for all to trojanclaw_backend using (true) with check (true)', relation_name);
    end loop;
    foreach relation_name in array array[
        'courses', 'deliverables', 'deliverable_due_changes', 'course_events',
        'announcements', 'course_documents', 'piazza_posts', 'content_chunks'
    ] loop
        execute format('create policy backend_server_read on public.%I for select to trojanclaw_backend using (true)', relation_name);
    end loop;
end $$;

-- Auth lookups precede user authentication, and public events are shared. Only
-- the backend role can use these policies; browser roles receive no access.
create policy backend_server_access on public.app_users for all to trojanclaw_backend using (true) with check (true);
create policy backend_server_access on public.account_credentials for all to trojanclaw_backend using (true) with check (true);
create policy backend_server_access on public.account_sessions for all to trojanclaw_backend using (true) with check (true);
create policy backend_server_access on public.auth_rate_limits for all to trojanclaw_backend using (true) with check (true);
create policy backend_server_access on public.integration_connections for all to trojanclaw_backend using (true) with check (true);
create policy backend_server_access on public.campus_events for all to trojanclaw_backend using (true) with check (true);
create policy backend_server_access on public.campus_event_sources for all to trojanclaw_backend using (true) with check (true);
create policy backend_server_read on public.documents for select to trojanclaw_backend using (true);

-- Do NOT add an unrestricted backend policy on user_connector_sessions. Its
-- FORCE RLS policy requires app.connector_user_id set locally per transaction.
-- remote_usc_attempts likewise retains its cookie-bound FORCE RLS policy.
