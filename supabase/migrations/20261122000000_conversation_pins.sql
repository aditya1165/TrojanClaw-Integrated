-- Pinned chats stay at the top of the chat list.
alter table public.conversations add column if not exists pinned_at timestamptz;
