-- Piazza is now read live through the mcp-piazza server; the raw-post table
-- written by the removed piazza_ingest.py script is no longer used.
drop table if exists public.piazza_data;
