-- 0255: Only one heartbeat runs at a time, a stale claim stops blocking, chat
-- turns always end, and chat comes first.
--
-- Heartbeats:
-- - The timer checked should_run_heartbeat() and the dashboard checked the
--   active claim, then each called start_heartbeat() separately, which neither
--   locked nor checked: started together, both ran. start_heartbeat now takes
--   the heartbeat row and returns NULL while one is in progress.
-- - A worker restarted or killed mid-heartbeat cleared nothing, and every
--   later heartbeat was refused as "already running" until someone cleared the
--   row by hand. A claim now counts until the longest a heartbeat may run
--   (heartbeat.claim_timeout_s, or the RLM heartbeat timeout plus ten minutes).
-- - Recent activity listed a heartbeat once per memory saved during it; it
--   now lists the heartbeat's own record, which finalize_agentic_heartbeat
--   (dashboard runs) now stamps with the heartbeat number like
--   complete_heartbeat does.
--
-- Chat:
-- - chat.turn_timeout_s: an outer bound so a hung model call ends in a
--   visible error, never a spinner.
-- - chat.max_tool_rounds / chat.answer_reserve_s: after that many rounds of
--   tool calls, or with that little of the time limit left, the model is
--   asked to answer with what it has found and is offered no more tools.
-- - chat_turn_leases: the API holds a lease for each turn and background
--   model calls (extraction, RecMem, summarization, reconsolidation, reading)
--   wait for it, capped by chat.priority_max_wait_s.
-- - The HNSW index on memories.embedding was partial on embedding_status and
--   never used by similarity queries, which filter on status/type only; the
--   database computed every cosine distance on every recall.
SET search_path = public, ag_catalog;

INSERT INTO config_defaults (key, value, description) VALUES
    ('chat.turn_timeout_s', '360'::jsonb, 'Outer time limit for one chat turn, in seconds; 0 disables'),
    ('chat.priority_max_wait_s', '180'::jsonb, 'Longest a background model call waits for chat to finish before going ahead'),
    ('chat.max_tool_rounds', '5'::jsonb, 'Rounds of tool calls in one chat turn before the agent must answer'),
    ('chat.answer_reserve_s', '30'::jsonb, 'Seconds of the chat loop time limit kept for writing the answer'),
    ('heartbeat.claim_timeout_s', '1800'::jsonb, 'Seconds after a heartbeat starts before its claim stops blocking new heartbeats')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, description = EXCLUDED.description;
INSERT INTO config (key, value, description) VALUES
    ('chat.turn_timeout_s', '360'::jsonb, 'Outer time limit for one chat turn, in seconds; 0 disables'),
    ('chat.priority_max_wait_s', '180'::jsonb, 'Longest a background model call waits for chat to finish before going ahead'),
    ('chat.max_tool_rounds', '5'::jsonb, 'Rounds of tool calls in one chat turn before the agent must answer'),
    ('chat.answer_reserve_s', '30'::jsonb, 'Seconds of the chat loop time limit kept for writing the answer'),
    ('heartbeat.claim_timeout_s', '1800'::jsonb, 'Seconds after a heartbeat starts before its claim stops blocking new heartbeats')
ON CONFLICT (key) DO NOTHING;

-- A heartbeat claim counts until the longest a heartbeat may run, plus ten
-- minutes. A worker killed mid-heartbeat clears nothing; without this, every
-- later heartbeat was refused as "already running" until someone cleared the
-- row by hand.
CREATE OR REPLACE FUNCTION heartbeat_in_progress() RETURNS BOOLEAN
    LANGUAGE sql STABLE AS $$
    SELECT COALESCE((
        SELECT active_heartbeat_id IS NOT NULL
           AND (last_heartbeat_at IS NULL
                OR last_heartbeat_at > CURRENT_TIMESTAMP - make_interval(secs => GREATEST(
                    COALESCE(get_config_int('heartbeat.claim_timeout_s'), 1800),
                    COALESCE(get_config_int('rlm.heartbeat.timeout_seconds'), 300) + 600)))
        FROM heartbeat_state WHERE id = 1
    ), FALSE);
$$;

CREATE OR REPLACE FUNCTION should_run_heartbeat()
RETURNS BOOLEAN AS $$
DECLARE
    state_record RECORD;
    interval_minutes FLOAT;
BEGIN
    IF is_agent_terminated() THEN
        RETURN FALSE;
    END IF;
    IF NOT is_agent_configured() THEN
        RETURN FALSE;
    END IF;
    IF NOT is_init_complete() THEN
        RETURN FALSE;
    END IF;

    SELECT * INTO state_record FROM heartbeat_state WHERE id = 1;
    IF state_record.is_paused THEN
        RETURN FALSE;
    END IF;
    IF heartbeat_in_progress() THEN
        RETURN FALSE;
    END IF;
    IF state_record.last_heartbeat_at IS NULL THEN
        RETURN TRUE;
    END IF;
    IF state_record.next_heartbeat_at IS NOT NULL THEN
        RETURN CURRENT_TIMESTAMP >= state_record.next_heartbeat_at;
    END IF;
    interval_minutes := get_config_float('heartbeat.heartbeat_interval_minutes');

    RETURN CURRENT_TIMESTAMP >= state_record.last_heartbeat_at + (interval_minutes || ' minutes')::INTERVAL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION start_heartbeat()
RETURNS JSONB AS $$
DECLARE
    heartbeat_id UUID;
    state_record RECORD;
    new_energy FLOAT;
    regeneration JSONB;
    economy JSONB;
    context JSONB;
    decision_max_tokens INT;
    hb_number INT;
    external_calls JSONB := '[]'::jsonb;
BEGIN
    IF NOT is_agent_configured() THEN
        RETURN NULL;
    END IF;
    IF NOT is_init_complete() THEN
        RETURN NULL;
    END IF;

    -- One heartbeat at a time: the timer, the dashboard and the CLI all start
    -- here, so take the row first and refuse while another is running.
    PERFORM 1 FROM heartbeat_state WHERE id = 1 FOR UPDATE;
    IF heartbeat_in_progress() THEN
        RETURN NULL;
    END IF;

    PERFORM ensure_emotion_bootstrap();
    PERFORM ensure_self_node();
    PERFORM ensure_current_life_chapter();
    SELECT * INTO state_record FROM heartbeat_state WHERE id = 1;
    hb_number := state_record.heartbeat_count + 1;
    heartbeat_id := gen_random_uuid();
    PERFORM update_drives();
    regeneration := regenerate_heartbeat_energy(CURRENT_TIMESTAMP);
    new_energy := COALESCE(
        NULLIF(regeneration->>'after_energy', '')::float,
        state_record.current_energy
    );
    UPDATE heartbeat_state SET
        current_energy = new_energy,
        heartbeat_count = hb_number,
        last_heartbeat_at = CURRENT_TIMESTAMP,
        next_heartbeat_at = NULL,
        active_heartbeat_id = heartbeat_id,
        active_heartbeat_number = hb_number,
        active_actions = '[]'::jsonb,
        active_reasoning = NULL,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;
    economy := begin_heartbeat_outcome(heartbeat_id, hb_number, regeneration);
    IF COALESCE(get_config_bool('heartbeat.use_rlm'), FALSE) THEN
        context := gather_turn_snapshot();
    ELSE
        context := gather_turn_context();
    END IF;
    context := context || jsonb_build_object(
        'belief_updates_recent',
        recent_belief_updates_json(20, state_record.last_heartbeat_at),
        'heartbeat_economy', economy
    );
    decision_max_tokens := get_config_int('heartbeat.max_decision_tokens');
    external_calls := jsonb_build_array(
        build_external_call(
            'think',
            jsonb_build_object(
                'kind', CASE
                    WHEN COALESCE(get_config_bool('heartbeat.use_rlm'), FALSE) THEN 'heartbeat_decision_rlm'
                    ELSE 'heartbeat_decision'
                END,
                'context', context,
                'heartbeat_id', heartbeat_id,
                'max_tokens', decision_max_tokens
            )
        )
    );

    RETURN jsonb_build_object(
        'heartbeat_id', heartbeat_id,
        'heartbeat_number', hb_number,
        'external_calls', external_calls,
        'outbox_messages', '[]'::jsonb
    );
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION finalize_agentic_heartbeat(
    p_heartbeat_id TEXT,
    p_summary TEXT,
    p_energy_spent INT DEFAULT 0,
    p_tool_call_count INT DEFAULT 0,
    p_stopped_reason TEXT DEFAULT 'completed',
    p_has_tasks BOOLEAN DEFAULT FALSE
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    memory_id UUID;
    item RECORD;
    hb_number INTEGER;
BEGIN
    SELECT active_heartbeat_number INTO hb_number
    FROM heartbeat_state
    WHERE id = 1 AND active_heartbeat_id::text = p_heartbeat_id;

    -- 1. Record the heartbeat as an episodic memory. Best-effort: a memory
    --    failure (e.g. embedding service down) must not block finalization.
    --    (The pre-move Python call used the wrong arg names/types — p_action and
    --    a text p_result — so it silently never recorded anything; corrected here.)
    BEGIN
        memory_id := create_episodic_memory(
            p_content := left(COALESCE(p_summary, ''), 2000),
            p_action_taken := to_jsonb('heartbeat'::text),
            p_context := jsonb_build_object(
                'heartbeat_id', p_heartbeat_id,
                'heartbeat_number', hb_number,
                'energy_spent', p_energy_spent,
                'tool_calls', p_tool_call_count,
                'stopped_reason', p_stopped_reason,
                'has_backlog_tasks', p_has_tasks
            ),
            p_result := to_jsonb(CASE WHEN p_stopped_reason = 'completed' THEN 'completed' ELSE p_stopped_reason END),
            p_importance := 0.5::double precision,
            p_trust_level := 1.0::double precision
        );
    EXCEPTION WHEN OTHERS THEN
        memory_id := NULL;
    END;

    -- 2. Mark heartbeat completion and release this run's active claim.
    UPDATE heartbeat_state
    SET last_heartbeat_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;
    PERFORM release_active_heartbeat(p_heartbeat_id);

    -- 3. Auto-checkpoint in-progress backlog items interrupted by timeout/energy,
    --    so the next heartbeat can resume (only those without a checkpoint yet).
    IF p_has_tasks AND p_stopped_reason IN ('timeout', 'energy_exhausted') THEN
        FOR item IN
            SELECT id, checkpoint
            FROM public.backlog
            WHERE status = 'in_progress'
            ORDER BY updated_at DESC
            LIMIT 5
        LOOP
            IF item.checkpoint IS NULL THEN
                UPDATE public.backlog
                SET checkpoint = jsonb_build_object(
                        'step', 'interrupted',
                        'progress', format('Heartbeat ended (%s). %s tool calls made.',
                                           p_stopped_reason, p_tool_call_count),
                        'next_action', 'Continue from where left off'
                    ),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = item.id;
            END IF;
        END LOOP;
    END IF;

    RETURN jsonb_build_object('memory_id', memory_id::text);
END;
$$;

DROP VIEW IF EXISTS recent_heartbeats;
CREATE VIEW recent_heartbeats AS
SELECT
    m.id as memory_id,
    NULLIF(m.metadata#>>'{context,heartbeat_id}', '')::uuid as heartbeat_id,
    (m.metadata#>>'{context,heartbeat_number}')::int as heartbeat_number,
    COALESCE((m.metadata->>'event_time')::timestamptz, m.created_at) as started_at,
    COALESCE((m.metadata->>'event_time')::timestamptz, m.created_at) as ended_at,
    NULL::float as energy_start,
    NULL::float as energy_end,
    jsonb_array_length(COALESCE(m.metadata#>'{context,actions_taken}', '[]'::jsonb)) as action_count,
    m.content as narrative,
    NULLIF(m.metadata->>'emotional_valence', '')::float as emotional_valence
FROM memories m
WHERE m.type = 'episodic'
  AND m.metadata#>>'{context,heartbeat_id}' IS NOT NULL
  -- The heartbeat's own record, not every memory saved during it (those carry
  -- the heartbeat_id too): complete_heartbeat and finalize_agentic_heartbeat
  -- write the number; earlier finalizer records carry stopped_reason.
  AND ((m.metadata -> 'context') ? 'heartbeat_number' OR (m.metadata -> 'context') ? 'stopped_reason')
ORDER BY COALESCE((m.metadata->>'event_time')::timestamptz, m.created_at) DESC
LIMIT 20;

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

CREATE INDEX IF NOT EXISTS idx_memories_embedding_all
    ON memories USING hnsw (embedding vector_cosine_ops);
DROP INDEX IF EXISTS idx_memories_embedding;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_memories_embedding_all')
       AND NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_memories_embedding') THEN
        ALTER INDEX idx_memories_embedding_all RENAME TO idx_memories_embedding;
    END IF;
END $$;
