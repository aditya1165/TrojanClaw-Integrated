-- Private bucket for files attached in chat. Objects live at
-- <account id>/<upload id>/<file name>. No storage policies are added, so
-- browser keys cannot read or write it; only the backend (secret key) can.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
    'chat-uploads', 'chat-uploads', false, 5242880,
    array[
        'application/pdf', 'image/png', 'image/jpeg', 'image/gif', 'image/webp',
        'text/plain', 'text/markdown', 'text/csv', 'application/json',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    ]
)
on conflict (id) do nothing;
