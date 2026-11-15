-- Multi-user: public USC campus pages are one shared knowledge base.
--
-- Every per-student table already carries user_id with ON DELETE CASCADE to
-- app_users, and accounts come from the password + session tables
-- (20261116000000_password_auth). The only single-user leftover is
-- public.documents, whose user_id defaulted to the demo user even though rows
-- were already unique per (source_url, chunk_index) across the whole table.
-- Campus pages are public, so drop the column: every signed-in student searches
-- the same rows. Per-student course text lives in public.content_chunks.

alter table public.documents drop column if exists user_id;  -- also drops its FK and index

comment on table public.documents is
    'Shared public USC web pages (chunked + embedded) for campus search. Not per user; course material per student is in content_chunks.';
