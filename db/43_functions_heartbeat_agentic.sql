-- DB-owned agentic-heartbeat helpers: active-hours gating + finalization.
-- Moves inline Python SQL/clock logic (services/worker_service._check_active_hours,
-- services/heartbeat_agentic.finalize_heartbeat) into the DB. Named
-- finalize_agentic_heartbeat to avoid colliding with the legacy JSON-path
-- finalize_heartbeat (db/13).
SET search_path = public, ag_catalog, "$user";

-- Is "now" within the configured heartbeat.active_hours window (e.g. '08:00-22:00',
-- with wraparound like '22:00-06:00') in heartbeat.timezone? Fails open (TRUE) on
-- missing/malformed config; falls back to UTC on an unknown timezone.
CREATE OR REPLACE FUNCTION is_within_active_hours()
RETURNS BOOLEAN
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    active_hours TEXT := get_config_text('heartbeat.active_hours');
    tz TEXT := COALESCE(NULLIF(get_config_text('heartbeat.timezone'), ''), 'UTC');
    parts TEXT[];
    start_min INT;
    end_min INT;
    cur_min INT;
    local_ts TIMESTAMP;
BEGIN
    IF NULLIF(btrim(COALESCE(active_hours, '')), '') IS NULL THEN
        RETURN TRUE;  -- no restriction configured
    END IF;
    BEGIN
        parts := string_to_array(active_hours, '-');
        IF array_length(parts, 1) <> 2 THEN
            RETURN TRUE;
        END IF;
        start_min := split_part(btrim(parts[1]), ':', 1)::int * 60 + split_part(btrim(parts[1]), ':', 2)::int;
        end_min := split_part(btrim(parts[2]), ':', 1)::int * 60 + split_part(btrim(parts[2]), ':', 2)::int;
        IF start_min < 0 OR start_min > 1439 OR end_min < 0 OR end_min > 1439 THEN
            RETURN TRUE;
        END IF;

        BEGIN
            local_ts := now() AT TIME ZONE tz;
        EXCEPTION WHEN OTHERS THEN
            local_ts := now() AT TIME ZONE 'UTC';  -- unknown tz -> UTC, like the Python
        END;
        cur_min := extract(hour FROM local_ts)::int * 60 + extract(minute FROM local_ts)::int;

        -- 23:59 is the last representable minute, so 00:00-23:59 is the
        -- conventional full-day window rather than a one-minute daily gap.
        IF start_min = 0 AND end_min = 1439 THEN
            RETURN TRUE;
        END IF;

        IF start_min <= end_min THEN
            RETURN cur_min >= start_min AND cur_min < end_min;
        ELSE
            RETURN cur_min >= start_min OR cur_min < end_min;  -- wraparound window
        END IF;
    EXCEPTION WHEN OTHERS THEN
        RETURN TRUE;  -- malformed active_hours -> don't block
    END;
END;
$$;

-- Finalize an agentic heartbeat: record it as an episodic memory, bump
-- heartbeat_state, and auto-checkpoint interrupted in-progress backlog items.
-- Release the active-heartbeat claim. With p_heartbeat_id the release is
-- guarded (only that run's claim is cleared); with NULL it clears
-- unconditionally (operator/error recovery). Returns whether a claim was
-- cleared.
CREATE OR REPLACE FUNCTION release_active_heartbeat(
    p_heartbeat_id TEXT DEFAULT NULL
) RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    cleared INT;
BEGIN
    UPDATE heartbeat_state
    SET active_heartbeat_id = NULL,
        active_heartbeat_number = NULL,
        active_actions = '[]'::jsonb,
        active_reasoning = NULL,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1
      AND (p_heartbeat_id IS NULL OR active_heartbeat_id::text = p_heartbeat_id);
    GET DIAGNOSTICS cleared = ROW_COUNT;
    RETURN cleared > 0;
END;
$$;

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
