-- Raw transcripts are kept for a set time, not forever.
--
-- Nothing deleted conversation records before: every web chat, every channel
-- message (and its per-message document), every turn's full model input, and
-- every raw turn the memory system works from. Memories have their own fading
-- rules (retention.*) and are untouched here.
--
-- retention.transcripts_enabled (default on) and, in days (0 keeps forever):
--   retention.turn_trace_days   (30)   agent_turns + agent_turn_events: full
--                                      prompts, tool calls and results.
--   retention.conversation_days (365)  web-chat and channel sessions with
--                                      their messages, measured from the
--                                      session's last activity, and the
--                                      channel_message documents.
--   retention.raw_turn_days     (365)  subconscious_units the memory system
--                                      has finished with (encoded or skipped,
--                                      routed) and that are not pinned. Memories
--                                      formed from them stay; only the link to
--                                      the verbatim turn goes.
-- Ingested files, imported history and connector items are never touched.
-- run_transcript_retention() runs daily in the maintenance worker, in batches.
SET search_path = public, ag_catalog;

INSERT INTO config (key, value, description) VALUES
    ('retention.transcripts_enabled', 'true', 'Delete raw transcripts older than the retention.*_days limits (memories are not affected)'),
    ('retention.turn_trace_days', '30', 'Days to keep per-turn traces: full prompts, tool calls and results (0 keeps forever)'),
    ('retention.conversation_days', '365', 'Days to keep a conversation (web chat, Slack, Telegram, …) after its last activity, with its message documents (0 keeps forever)'),
    ('retention.raw_turn_days', '365', 'Days to keep raw turns the memory system has finished with (0 keeps forever)'),
    ('retention.transcript_batch_size', '500', 'Rows of each kind deleted per retention pass')
ON CONFLICT (key) DO NOTHING;

CREATE OR REPLACE FUNCTION run_transcript_retention() RETURNS JSONB
    LANGUAGE plpgsql
    AS $$
DECLARE
    trace_days INT := COALESCE(get_config_int('retention.turn_trace_days'), 30);
    conversation_days INT := COALESCE(get_config_int('retention.conversation_days'), 365);
    raw_days INT := COALESCE(get_config_int('retention.raw_turn_days'), 365);
    batch INT := GREATEST(COALESCE(get_config_int('retention.transcript_batch_size'), 500), 1);
    n_traces INT := 0;
    n_chats INT := 0;
    n_channels INT := 0;
    n_documents INT := 0;
    n_raw INT := 0;
BEGIN
    IF NOT COALESCE(get_config_bool('retention.transcripts_enabled'), TRUE) THEN
        RETURN jsonb_build_object('skipped', TRUE, 'reason', 'disabled');
    END IF;

    IF trace_days > 0 THEN
        WITH gone AS (
            DELETE FROM agent_turns WHERE id IN (
                SELECT id FROM agent_turns
                WHERE status NOT IN ('running', 'waiting_external')
                  AND COALESCE(completed_at, updated_at) < CURRENT_TIMESTAMP - make_interval(days => trace_days)
                LIMIT batch)
            RETURNING 1)
        SELECT count(*) INTO n_traces FROM gone;
    END IF;

    IF conversation_days > 0 THEN
        WITH gone AS (
            DELETE FROM chat_sessions WHERE id IN (
                SELECT id FROM chat_sessions
                WHERE last_active_at < CURRENT_TIMESTAMP - make_interval(days => conversation_days)
                LIMIT batch)
            RETURNING 1)
        SELECT count(*) INTO n_chats FROM gone;

        WITH gone AS (
            DELETE FROM channel_sessions WHERE id IN (
                SELECT id FROM channel_sessions
                WHERE COALESCE(last_active, created_at) < CURRENT_TIMESTAMP - make_interval(days => conversation_days)
                LIMIT batch)
            RETURNING 1)
        SELECT count(*) INTO n_channels FROM gone;

        WITH gone AS (
            DELETE FROM source_documents WHERE id IN (
                SELECT id FROM source_documents
                WHERE source_type = 'channel_message'
                  AND created_at < CURRENT_TIMESTAMP - make_interval(days => conversation_days)
                LIMIT batch)
            RETURNING 1)
        SELECT count(*) INTO n_documents FROM gone;
    END IF;

    IF raw_days > 0 THEN
        WITH gone AS (
            DELETE FROM subconscious_units WHERE id IN (
                SELECT id FROM subconscious_units
                WHERE turn_at < CURRENT_TIMESTAMP - make_interval(days => raw_days)
                  AND pinned_at IS NULL
                  AND extraction_status IN ('extracted', 'skipped', 'failed')
                  AND route_status IN ('raw_only', 'merged', 'episode_created', 'route_failed')
                LIMIT batch)
            RETURNING 1)
        SELECT count(*) INTO n_raw FROM gone;
    END IF;

    RETURN jsonb_build_object(
        'turn_traces', n_traces,
        'chat_sessions', n_chats,
        'channel_sessions', n_channels,
        'message_documents', n_documents,
        'raw_turns', n_raw);
END;
$$;
