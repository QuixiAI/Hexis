-- Scene consolidation (#73, RecMem Rev 5 Phase 1).
-- A conversation becomes one scene-grained consolidation pass when its
-- session goes quiet, instead of dozens of per-turn promotions. Reuses the
-- existing episode_create task/worker/LLM machinery — this file only decides
-- WHEN to consolidate (session idle) and WHAT to cover (the session's
-- unconsumed units). Units without a session_id stay on the recurrence path.
SET search_path = public, ag_catalog, "$user";

INSERT INTO config_defaults (key, value, description) VALUES
    ('memory.scene_idle_seconds', '1800'::jsonb,
     'A session with no new turns for this long is a closed scene, ready to consolidate'),
    ('memory.scene_check_interval_seconds', '300'::jsonb,
     'How often the worker looks for idle sessions to consolidate into scenes')
ON CONFLICT (key) DO NOTHING;

-- Sessions whose units have gone quiet and still hold unconsumed material.
-- "Unconsumed" = active, embedded, and not already claimed by or folded into
-- an episode. Sessions with units still awaiting embedding are skipped — the
-- embed pass runs every worker tick, so they become eligible moments later.
CREATE OR REPLACE FUNCTION find_idle_scene_sessions(
    p_limit INT DEFAULT 8
) RETURNS TABLE (
    session_id UUID,
    unit_ids UUID[],
    first_turn_at TIMESTAMPTZ,
    last_turn_at TIMESTAMPTZ
) AS $$
DECLARE
    idle_seconds FLOAT := GREATEST(COALESCE(get_config_float('memory.scene_idle_seconds'), 1800), 60);
BEGIN
    RETURN QUERY
    SELECT
        s.session_id,
        array_agg(s.id ORDER BY s.turn_at, s.created_at) AS unit_ids,
        min(s.turn_at) AS first_turn_at,
        max(s.turn_at) AS last_turn_at
    FROM subconscious_units s
    -- 'routing' units are mid-claim by the per-turn router; the scene sweep
    -- waits for them to settle (claims stale out in ~60s) rather than racing.
    WHERE s.session_id IS NOT NULL
      AND s.status = 'active'
      AND s.route_status IN ('unrouted', 'raw_only')
      -- Only EPISODIC consumption counts: extraction/corroboration links mean
      -- a fact was distilled from the turn, not that the experience itself
      -- was consolidated — the scene still needs to exist.
      AND NOT EXISTS (
          SELECT 1 FROM memory_source_units msu
          JOIN memories em ON em.id = msu.memory_id AND em.type = 'episodic'
          WHERE msu.subconscious_unit_id = s.id
            AND msu.role IN ('source', 'direct_promotion', 'merge_addition')
      )
    GROUP BY s.session_id
    HAVING max(s.turn_at) < CURRENT_TIMESTAMP - (idle_seconds || ' seconds')::interval
       -- Every unit of the scene must be embedded before the LLM sees it.
       AND bool_and(s.embedding_status = 'embedded')
    ORDER BY max(s.turn_at)
    LIMIT GREATEST(COALESCE(p_limit, 8), 1);
END;
$$ LANGUAGE plpgsql STABLE;

-- One episode_create task per idle session, covering all its unconsumed
-- units time-ordered. The existing worker, prompt, and apply handler run
-- unchanged; task_payload marks the boundary reason and session for the
-- scene metadata stamp in apply_recmem_episode_create.
CREATE OR REPLACE FUNCTION enqueue_scene_consolidations(
    p_limit INT DEFAULT 8
) RETURNS JSONB AS $$
DECLARE
    queue_max INT := COALESCE(get_config_int('memory.recmem_queue_max'), 5000);
    scene RECORD;
    enqueued INT := 0;
    skipped_queue_full INT := 0;
BEGIN
    FOR scene IN SELECT * FROM find_idle_scene_sessions(p_limit)
    LOOP
        IF _recmem_pending_queue_depth() >= queue_max THEN
            skipped_queue_full := skipped_queue_full + 1;
            CONTINUE;
        END IF;

        INSERT INTO recmem_consolidation_tasks (
            task_type, trigger_unit_id, source_unit_ids, task_payload
        )
        VALUES (
            'episode_create',
            scene.unit_ids[1],
            scene.unit_ids,
            jsonb_build_object(
                'reason', 'session_boundary',
                'session_id', scene.session_id,
                'first_turn_at', scene.first_turn_at,
                'last_turn_at', scene.last_turn_at
            )
        );

        UPDATE subconscious_units
        SET route_status = 'create_queued',
            route_result = route_result || jsonb_build_object(
                'scene_boundary', true,
                'at', CURRENT_TIMESTAMP
            ),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ANY(scene.unit_ids);

        enqueued := enqueued + 1;
    END LOOP;

    RETURN jsonb_build_object(
        'enqueued', enqueued,
        'skipped_queue_full', skipped_queue_full
    );
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION should_run_scene_consolidation()
RETURNS BOOLEAN AS $$
DECLARE
    state_doc JSONB := COALESCE(get_state('scene_state'), '{}'::jsonb);
    last_run TIMESTAMPTZ := NULLIF(state_doc->>'last_run_at', '')::timestamptz;
    interval_seconds FLOAT := COALESCE(get_config_float('memory.scene_check_interval_seconds'), 300);
BEGIN
    IF last_run IS NULL THEN
        RETURN TRUE;
    END IF;

    RETURN CURRENT_TIMESTAMP >= last_run + (interval_seconds || ' seconds')::interval;
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION mark_scene_consolidation_run(
    p_result JSONB DEFAULT '{}'::jsonb
) RETURNS JSONB AS $$
DECLARE
    merged JSONB;
BEGIN
    merged := COALESCE(get_state('scene_state'), '{}'::jsonb)
        || jsonb_build_object(
            'last_run_at', CURRENT_TIMESTAMP,
            'last_run_result', COALESCE(p_result, '{}'::jsonb)
        );

    PERFORM set_state('scene_state', merged);
    RETURN merged;
END;
$$ LANGUAGE plpgsql;
