-- Retain the existing owner policies, encryption and rows; enable one more provider.
ALTER TABLE public.user_connector_sessions
    DROP CONSTRAINT user_connector_sessions_provider_check;
ALTER TABLE public.user_connector_sessions
    ADD CONSTRAINT user_connector_sessions_provider_check
    CHECK (provider IN ('brightspace', 'handshake', 'piazza'));
