-- Calendar drafts belong to the chat that created them. Older drafts keep a
-- null conversation and no longer show under any chat. Deleting a chat
-- deletes its drafts; events already added to Google Calendar stay there.
alter table public.calendar_proposals
    add column if not exists conversation_id uuid references public.conversations(id) on delete cascade;
create index if not exists calendar_proposals_conversation_idx
    on public.calendar_proposals (conversation_id, created_at desc);
