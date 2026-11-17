-- Chat comes first: background model calls wait while a chat turn is live.
--
-- On a single GPU box, reading a Slack import can keep dozens of model calls
-- in flight; a chat turn then queues behind them and times out. The API
-- records a lease for the duration of each turn, and the workers check it
-- before every background model call (core.chat_priority). Leases expire on
-- their own, so a crashed API never blocks background work for good.
SET search_path = public, ag_catalog;

CREATE TABLE IF NOT EXISTS chat_turn_leases (
    session_id TEXT PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE OR REPLACE FUNCTION chat_turn_begin(p_session_id TEXT, p_ttl_seconds INT DEFAULT 360) RETURNS VOID
    LANGUAGE sql AS $$
    INSERT INTO chat_turn_leases (session_id, started_at, expires_at)
    VALUES (p_session_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP + make_interval(secs => GREATEST(p_ttl_seconds, 1)))
    ON CONFLICT (session_id) DO UPDATE
        SET started_at = EXCLUDED.started_at, expires_at = EXCLUDED.expires_at;
$$;

CREATE OR REPLACE FUNCTION chat_turn_end(p_session_id TEXT) RETURNS VOID
    LANGUAGE sql AS $$
    DELETE FROM chat_turn_leases WHERE session_id = p_session_id;
$$;

CREATE OR REPLACE FUNCTION chat_turn_active() RETURNS BOOLEAN
    LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM chat_turn_leases WHERE expires_at <= CURRENT_TIMESTAMP;
    RETURN EXISTS (SELECT 1 FROM chat_turn_leases);
END;
$$;
