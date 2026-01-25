-- Hexis schema: core memory functions.
SET search_path = public, ag_catalog, "$user";
SET check_function_bodies = off;

CREATE OR REPLACE FUNCTION update_memory_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION update_memory_importance()
RETURNS TRIGGER AS $$
BEGIN
    NEW.importance = NEW.importance * (1.0 + (LN(NEW.access_count + 1) * 0.1));
    NEW.last_accessed = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION mark_neighborhoods_stale()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE memory_neighborhoods 
    SET is_stale = TRUE 
    WHERE memory_id = NEW.id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION assign_to_episode()
RETURNS TRIGGER AS $$
DECLARE
    current_episode_id UUID;
    last_memory_time TIMESTAMPTZ;
    new_seq INT;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('episode_manager'));
    SELECT e.id INTO current_episode_id
    FROM episodes e
    WHERE e.ended_at IS NULL
    ORDER BY e.started_at DESC
    LIMIT 1;
    IF current_episode_id IS NOT NULL THEN
        SELECT MAX(m.created_at), COALESCE(MAX(fem.sequence_order), 0)
        INTO last_memory_time, new_seq
        FROM find_episode_memories_graph(current_episode_id) fem
        JOIN memories m ON fem.memory_id = m.id;

        new_seq := COALESCE(new_seq, 0) + 1;
    END IF;
    IF current_episode_id IS NULL OR
       (last_memory_time IS NOT NULL AND NEW.created_at - last_memory_time > INTERVAL '30 minutes')
    THEN
        IF current_episode_id IS NOT NULL THEN
            UPDATE episodes
            SET ended_at = last_memory_time
            WHERE id = current_episode_id;
        END IF;
        INSERT INTO episodes (started_at, metadata)
        VALUES (NEW.created_at, jsonb_build_object('episode_type', 'autonomous'))
        RETURNING id INTO current_episode_id;

        new_seq := 1;
    END IF;
    PERFORM link_memory_to_episode_graph(NEW.id, current_episode_id, new_seq);
    INSERT INTO memory_neighborhoods (memory_id, is_stale)
    VALUES (NEW.id, TRUE)
    ON CONFLICT DO NOTHING;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION fast_recall(
    p_query_text TEXT,
    p_limit INT DEFAULT 10
) RETURNS TABLE (
    memory_id UUID,
    content TEXT,
    memory_type memory_type,
    score FLOAT,
    source TEXT
) AS $$
	DECLARE
	    query_embedding vector;
	    zero_vec vector;
	    affective_state JSONB;
	    current_valence FLOAT;
	    current_arousal FLOAT;
	    current_primary TEXT;
        min_trust FLOAT;
	BEGIN
	    query_embedding := get_embedding(ensure_embedding_prefix(p_query_text, 'search_query'));
	    zero_vec := array_fill(0.0::float, ARRAY[embedding_dimension()])::vector;
        affective_state := get_current_affective_state();
	    BEGIN
	        current_valence := NULLIF(affective_state->>'valence', '')::float;
	    EXCEPTION
	        WHEN OTHERS THEN
	            current_valence := NULL;
	    END;
	    BEGIN
	        current_arousal := NULLIF(affective_state->>'arousal', '')::float;
	    EXCEPTION
	        WHEN OTHERS THEN
	            current_arousal := NULL;
	    END;
	    BEGIN
	        current_primary := NULLIF(affective_state->>'primary_emotion', '');
	    EXCEPTION
	        WHEN OTHERS THEN
	            current_primary := NULL;
	    END;
	    current_valence := COALESCE(current_valence, 0.0);
	    current_arousal := COALESCE(current_arousal, 0.5);
	    current_primary := COALESCE(current_primary, 'neutral');
        min_trust := COALESCE(get_config_float('memory.recall_min_trust_level'), 0.0);
	    
	    RETURN QUERY
	    WITH 
	    seeds AS (
	        SELECT 
	            m.id, 
	            m.content, 
	            m.type,
            m.importance,
            m.decay_rate,
            m.created_at,
            m.last_accessed,
            1 - (m.embedding <=> query_embedding) as sim
        FROM memories m
	        WHERE m.status = 'active'
	          AND m.embedding IS NOT NULL
	          AND m.embedding <> zero_vec
	        ORDER BY m.embedding <=> query_embedding
	        LIMIT GREATEST(p_limit, 5)
	    ),
    associations AS (
        SELECT 
            (key)::UUID as mem_id,
            MAX((value::float) * s.sim) as assoc_score
        FROM seeds s
        JOIN memory_neighborhoods mn ON s.id = mn.memory_id,
        jsonb_each_text(mn.neighbors)
        WHERE NOT mn.is_stale
        GROUP BY key
    ),
    temporal AS (
        SELECT DISTINCT
            fem.memory_id as mem_id,
            0.15 as temp_score
        FROM episodes e
        CROSS JOIN LATERAL find_episode_memories_graph(e.id) fem
        WHERE e.ended_at IS NULL
          OR e.ended_at > CURRENT_TIMESTAMP - INTERVAL '1 hour'
        LIMIT 20
    ),
    candidates AS (
        SELECT id as mem_id, sim as vector_score, NULL::float as assoc_score, NULL::float as temp_score
        FROM seeds
        UNION
        SELECT mem_id, NULL, assoc_score, NULL FROM associations
        UNION
        SELECT mem_id, NULL, NULL, temp_score FROM temporal
    ),
    scored AS (
        SELECT 
            c.mem_id,
            MAX(c.vector_score) as vector_score,
            MAX(c.assoc_score) as assoc_score,
            MAX(c.temp_score) as temp_score
        FROM candidates c
        GROUP BY c.mem_id
    )
	    SELECT
	        m.id,
	        m.content,
	        m.type,
	        GREATEST(
	            COALESCE(sc.vector_score, 0) * 0.5 +
	            COALESCE(sc.assoc_score, 0) * 0.2 +
	            COALESCE(sc.temp_score, 0) * 0.15 +
	            calculate_relevance(m.importance, m.decay_rate, m.created_at, m.last_accessed) * 0.05 +
                COALESCE(m.trust_level, 0.5) * 0.1 +
	            (CASE
	                WHEN m.metadata ? 'emotional_context' THEN
	                    (
	                        COALESCE(
	                            CASE
	                                WHEN (m.metadata->'emotional_context'->>'valence') IS NULL THEN NULL
	                                ELSE 1.0 - (ABS((m.metadata->'emotional_context'->>'valence')::float - current_valence) / 2.0)
	                            END,
	                            0.5
	                        ) * 0.6
	                        +
	                        COALESCE(
	                            CASE
	                                WHEN (m.metadata->'emotional_context'->>'arousal') IS NULL THEN NULL
	                                ELSE 1.0 - ABS((m.metadata->'emotional_context'->>'arousal')::float - current_arousal)
	                            END,
	                            0.5
	                        ) * 0.3
	                        +
	                        (CASE
	                            WHEN (m.metadata->'emotional_context'->>'primary_emotion') IS NULL THEN 0.5
	                            WHEN (m.metadata->'emotional_context'->>'primary_emotion') = current_primary THEN 1.0
	                            ELSE 0.7
	                        END) * 0.1
	                    )
	                ELSE
	                    CASE
	                        WHEN (m.metadata->>'emotional_valence') IS NULL THEN 0.5
	                        ELSE 1.0 - (ABS((m.metadata->>'emotional_valence')::float - current_valence) / 2.0)
	                    END
	            END) * 0.05,
	            0.001
	        ) as final_score,
	        CASE
	            WHEN sc.vector_score IS NOT NULL THEN 'vector'
	            WHEN sc.assoc_score IS NOT NULL THEN 'association'
	            WHEN sc.temp_score IS NOT NULL THEN 'temporal'
	            ELSE 'fallback'
	        END as source
	    FROM scored sc
	    JOIN memories m ON sc.mem_id = m.id
	    WHERE m.status = 'active'
          AND m.trust_level >= min_trust
	    ORDER BY final_score DESC
	    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql;

SET check_function_bodies = on;
