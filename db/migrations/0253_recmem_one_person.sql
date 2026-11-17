-- 0253: Episodes stay within one person's conversations, and a turn that
-- produced a fact still belongs to its conversation's episode.
--
-- RecMem's recurrence path grouped any similar turns into a new episode,
-- across people and including turns already in an episode: one person's
-- confidential DM and two of another's conversations asking about it became
-- one episode, and a first meeting was retold in a second episode. The merge
-- path likewise appended a turn to the nearest episode whoever it was with.
-- Both now consider only turns and episodes with the same speaker (the label
-- each turn starts with), and recurrence skips turns already consolidated.
--
-- The scene sweep skips turns already consolidated into an episode, but it
-- counted any 'source' link, and the encoder links a dated fact to its turn
-- with role 'source'. That turn was then left out of its conversation's
-- episode. Only links to episodic memories count now.
SET search_path = public, ag_catalog;

-- The speaker a raw turn starts with ("Mia Tanaka (Slack user U…): …").
-- Episodes stay within one person's conversations: a turn of Ken's is never
-- merged into Mia's episode, and recurrence is one person coming back to
-- something.
CREATE OR REPLACE FUNCTION _recmem_speaker(p_content TEXT) RETURNS TEXT
    LANGUAGE sql IMMUTABLE
    AS $f$ SELECT btrim(split_part(COALESCE(p_content, ''), ':', 1)) $f$;

CREATE OR REPLACE FUNCTION recmem_route_unit(
    p_unit_id UUID
) RETURNS JSONB AS $$
DECLARE
    unit_row subconscious_units%ROWTYPE;
    theta_sim FLOAT := COALESCE(get_config_float('memory.recmem_theta_sim'), 0.7);
    theta_merge FLOAT := COALESCE(get_config_float('memory.recmem_theta_sim_merge'), 0.78);
    theta_count INT := COALESCE(get_config_int('memory.recmem_theta_count'), 5);
    top_k INT := COALESCE(get_config_int('memory.recmem_top_k'), 20);
    queue_max INT := COALESCE(get_config_int('memory.recmem_queue_max'), 5000);
    nearest_memory_id UUID;
    nearest_similarity FLOAT;
    source_ids UUID[];
    recurrence_count INT;
    max_neighbor_similarity FLOAT;
    task_id UUID;
    overlaps_open_create BOOLEAN;
BEGIN
    SELECT * INTO unit_row
    FROM subconscious_units
    WHERE id = p_unit_id
      AND status = 'active'
      AND embedding_status = 'embedded';

    IF NOT FOUND THEN
        RETURN jsonb_build_object('unit_id', p_unit_id, 'status', 'skipped', 'reason', 'not_embedded_or_inactive');
    END IF;

    SELECT m.id, 1 - (m.embedding <=> unit_row.embedding)
    INTO nearest_memory_id, nearest_similarity
    FROM memories m
    WHERE m.status = 'active'
      AND (m.valid_until IS NULL OR m.valid_until > CURRENT_TIMESTAMP)
      AND m.type = 'episodic'
      AND m.embedding IS NOT NULL
      -- Only an episode of a conversation with the same person: a turn of
      -- Ken's must never be merged into Mia's private episode.
      AND EXISTS (
          SELECT 1 FROM memory_source_units msu
          JOIN subconscious_units su ON su.id = msu.subconscious_unit_id
          WHERE msu.memory_id = m.id
            AND _recmem_speaker(su.content) = _recmem_speaker(unit_row.content)
      )
    ORDER BY m.embedding <=> unit_row.embedding
    LIMIT 1;

    IF nearest_memory_id IS NOT NULL
       AND nearest_similarity >= theta_merge
       AND COALESCE(unit_row.route_result->>'merge_rejected_target_memory_id', '') <> nearest_memory_id::text THEN
        IF _recmem_pending_queue_depth() >= queue_max THEN
            UPDATE subconscious_units
            SET route_status = 'raw_only',
                route_result = route_result || jsonb_build_object(
                    'decision', 'raw_only',
                    'reason', 'queue_full',
                    'nearest_memory_id', nearest_memory_id,
                    'similarity', nearest_similarity
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = p_unit_id;
            RETURN jsonb_build_object('unit_id', p_unit_id, 'status', 'raw_only', 'reason', 'queue_full');
        END IF;

        SELECT id INTO task_id
        FROM recmem_consolidation_tasks
        WHERE task_type = 'episode_merge'
          AND status = 'pending'
          AND target_memory_id = nearest_memory_id
        ORDER BY created_at
        LIMIT 1
        FOR UPDATE;

        IF task_id IS NOT NULL THEN
            UPDATE recmem_consolidation_tasks
            SET source_unit_ids = (
                    SELECT array_agg(DISTINCT source_id)
                    FROM unnest(array_append(source_unit_ids, p_unit_id)) AS source_id
                ),
                task_payload = task_payload || jsonb_build_object('coalesced_at', CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = task_id;
        ELSE
            INSERT INTO recmem_consolidation_tasks (
                task_type,
                trigger_unit_id,
                target_memory_id,
                source_unit_ids,
                max_similarity,
                task_payload
            )
            VALUES (
                'episode_merge',
                p_unit_id,
                nearest_memory_id,
                ARRAY[p_unit_id],
                nearest_similarity,
                jsonb_build_object(
                    'unit_content', unit_row.content,
                    'target_memory_id', nearest_memory_id,
                    'similarity', nearest_similarity
                )
            )
            RETURNING id INTO task_id;
        END IF;

        UPDATE subconscious_units
        SET route_status = 'merge_queued',
            route_result = route_result || jsonb_build_object(
                'decision', 'merge_queued',
                'task_id', task_id,
                'target_memory_id', nearest_memory_id,
                'similarity', nearest_similarity
            ),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = p_unit_id;

        RETURN jsonb_build_object('unit_id', p_unit_id, 'status', 'merge_queued', 'task_id', task_id);
    END IF;

    WITH neighbors AS (
        SELECT s.id, 1 - (s.embedding <=> unit_row.embedding) AS similarity
        FROM subconscious_units s
        WHERE s.status = 'active'
          AND s.embedding_status = 'embedded'
          AND s.embedding IS NOT NULL
          -- Recurrence is about one person coming back to something, and it
          -- must not retell turns that are already part of an episode.
          AND _recmem_speaker(s.content) = _recmem_speaker(unit_row.content)
          AND (s.id = unit_row.id OR NOT EXISTS (
              SELECT 1 FROM memory_source_units msu
              WHERE msu.subconscious_unit_id = s.id
                AND msu.role IN ('source', 'direct_promotion', 'merge_addition')))
        ORDER BY s.embedding <=> unit_row.embedding
        LIMIT GREATEST(top_k, theta_count)
    ),
    recurrent AS (
        SELECT id, similarity
        FROM neighbors
        WHERE similarity >= theta_sim
    )
    SELECT array_agg(id ORDER BY id), COUNT(*)::int, MAX(similarity)
    INTO source_ids, recurrence_count, max_neighbor_similarity
    FROM recurrent;

    source_ids := COALESCE(source_ids, ARRAY[p_unit_id]);
    IF NOT p_unit_id = ANY(source_ids) THEN
        source_ids := source_ids || p_unit_id;
        recurrence_count := COALESCE(recurrence_count, 0) + 1;
    END IF;

    IF COALESCE(recurrence_count, 0) < theta_count THEN
        UPDATE subconscious_units
        SET route_status = 'raw_only',
            route_result = route_result || jsonb_build_object(
                'decision', 'raw_only',
                'recurrence_count', COALESCE(recurrence_count, 0),
                'max_similarity', max_neighbor_similarity
            ),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = p_unit_id;
        RETURN jsonb_build_object('unit_id', p_unit_id, 'status', 'raw_only', 'recurrence_count', COALESCE(recurrence_count, 0));
    END IF;

    SELECT EXISTS (
        SELECT 1
        FROM recmem_consolidation_tasks t
        WHERE t.task_type = 'episode_create'
          AND t.status IN ('pending','in_progress')
          AND t.source_unit_ids && source_ids
    ) INTO overlaps_open_create;

    IF overlaps_open_create THEN
        UPDATE subconscious_units
        SET route_status = 'raw_only',
            route_result = route_result || jsonb_build_object(
                'decision', 'raw_only',
                'reason', 'open_create_overlap',
                'recurrence_count', recurrence_count
            ),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = p_unit_id;
        RETURN jsonb_build_object('unit_id', p_unit_id, 'status', 'raw_only', 'reason', 'open_create_overlap');
    END IF;

    IF _recmem_pending_queue_depth() >= queue_max THEN
        UPDATE subconscious_units
        SET route_status = 'raw_only',
            route_result = route_result || jsonb_build_object(
                'decision', 'raw_only',
                'reason', 'queue_full_create_paused',
                'recurrence_count', recurrence_count
            ),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = p_unit_id;
        RETURN jsonb_build_object('unit_id', p_unit_id, 'status', 'raw_only', 'reason', 'queue_full_create_paused');
    END IF;

    INSERT INTO recmem_consolidation_tasks (
        task_type,
        trigger_unit_id,
        source_unit_ids,
        recurrence_count,
        max_similarity,
        task_payload
    )
    VALUES (
        'episode_create',
        p_unit_id,
        source_ids,
        recurrence_count,
        max_neighbor_similarity,
        jsonb_build_object('source_unit_ids', source_ids, 'recurrence_count', recurrence_count)
    )
    RETURNING id INTO task_id;

    UPDATE subconscious_units
    SET route_status = 'create_queued',
        route_result = route_result || jsonb_build_object(
            'decision', 'create_queued',
            'task_id', task_id,
            'recurrence_count', recurrence_count,
            'max_similarity', max_neighbor_similarity
        ),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = ANY(source_ids)
      AND route_status IN ('routing','raw_only','unrouted');

    RETURN jsonb_build_object('unit_id', p_unit_id, 'status', 'create_queued', 'task_id', task_id, 'recurrence_count', recurrence_count);
END;
$$ LANGUAGE plpgsql;

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
