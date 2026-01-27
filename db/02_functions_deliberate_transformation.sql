-- Hexis schema: deliberate transformation functions.
SET search_path = public, ag_catalog, "$user";
SET check_function_bodies = off;

CREATE OR REPLACE FUNCTION default_transformation_state()
RETURNS JSONB AS $$
    SELECT jsonb_build_object(
        'active_exploration', false,
        'exploration_goal_id', NULL,
        'evidence_memories', '[]'::jsonb,
        'reflection_count', 0,
        'first_questioned_heartbeat', NULL,
        'contemplation_actions', 0
    );
$$ LANGUAGE sql IMMUTABLE;
CREATE OR REPLACE FUNCTION normalize_transformation_state(p_state JSONB)
RETURNS JSONB AS $$
DECLARE
    base JSONB := default_transformation_state();
BEGIN
    IF p_state IS NULL OR jsonb_typeof(p_state) <> 'object' THEN
        RETURN base;
    END IF;
    RETURN base || p_state;
END;
$$ LANGUAGE plpgsql IMMUTABLE;
CREATE OR REPLACE FUNCTION get_transformation_config(
    p_subcategory TEXT,
    p_category TEXT DEFAULT NULL
)
RETURNS JSONB AS $$
DECLARE
    cfg JSONB;
BEGIN
    IF p_subcategory IS NOT NULL AND btrim(p_subcategory) <> '' THEN
        SELECT value INTO cfg FROM config WHERE key = 'transformation.' || p_subcategory;
    END IF;
    IF cfg IS NULL AND p_category IS NOT NULL AND btrim(p_category) <> '' THEN
        SELECT value INTO cfg FROM config WHERE key = 'transformation.' || p_category;
    END IF;
    RETURN cfg;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION begin_belief_exploration(
    p_belief_id UUID,
    p_exploration_goal_id UUID
) RETURNS JSONB AS $$
DECLARE
    belief RECORD;
    state JSONB;
    hb_count INT;
    goal_exists BOOLEAN;
BEGIN
    SELECT * INTO belief FROM memories WHERE id = p_belief_id AND type = 'worldview';
    IF NOT FOUND THEN
        RETURN jsonb_build_object('success', false, 'reason', 'belief_not_found');
    END IF;

    IF COALESCE(belief.metadata->>'change_requires', '') <> 'deliberate_transformation' THEN
        RETURN jsonb_build_object('success', false, 'reason', 'not_transformable');
    END IF;

    state := normalize_transformation_state(belief.metadata->'transformation_state');
    IF COALESCE((state->>'active_exploration')::boolean, false) THEN
        RETURN jsonb_build_object(
            'success', false,
            'reason', 'already_exploring',
            'existing_goal_id', state->>'exploration_goal_id'
        );
    END IF;

    SELECT EXISTS(
        SELECT 1 FROM memories WHERE id = p_exploration_goal_id AND type = 'goal'
    ) INTO goal_exists;
    IF NOT goal_exists THEN
        RETURN jsonb_build_object('success', false, 'reason', 'goal_not_found');
    END IF;

    SELECT heartbeat_count INTO hb_count FROM heartbeat_state WHERE id = 1;

    state := jsonb_set(state, '{active_exploration}', 'true'::jsonb, true);
    state := jsonb_set(state, '{exploration_goal_id}', to_jsonb(p_exploration_goal_id::text), true);
    state := jsonb_set(state, '{evidence_memories}', '[]'::jsonb, true);
    state := jsonb_set(state, '{reflection_count}', '0'::jsonb, true);
    state := jsonb_set(state, '{first_questioned_heartbeat}', to_jsonb(hb_count), true);
    state := jsonb_set(state, '{contemplation_actions}', '0'::jsonb, true);

    UPDATE memories
    SET metadata = jsonb_set(metadata, '{transformation_state}', state, true),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_belief_id;

    PERFORM create_episodic_memory(
        p_content := format('Began exploring belief: %s', belief.content),
        p_action_taken := jsonb_build_object(
            'action', 'begin_belief_exploration',
            'belief_id', p_belief_id,
            'goal_id', p_exploration_goal_id
        ),
        p_context := jsonb_build_object(
            'category', belief.metadata->>'category',
            'subcategory', belief.metadata->>'subcategory'
        ),
        p_result := jsonb_build_object('status', 'started'),
        p_emotional_valence := 0.2,
        p_importance := 0.6
    );

    RETURN jsonb_build_object(
        'success', true,
        'belief_id', p_belief_id,
        'goal_id', p_exploration_goal_id,
        'message', 'Exploration begun'
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION record_transformation_effort(
    p_belief_id UUID,
    p_effort_type TEXT,
    p_notes TEXT DEFAULT NULL,
    p_evidence_memory_id UUID DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    belief RECORD;
    state JSONB;
    evidence JSONB;
    reflection_increment INT := 0;
    new_reflections INT;
    new_actions INT;
    hb_count INT;
BEGIN
    SELECT * INTO belief FROM memories WHERE id = p_belief_id AND type = 'worldview';
    IF NOT FOUND THEN
        RETURN jsonb_build_object('success', false, 'reason', 'belief_not_found');
    END IF;

    state := normalize_transformation_state(belief.metadata->'transformation_state');
    IF NOT COALESCE((state->>'active_exploration')::boolean, false) THEN
        RETURN jsonb_build_object('success', false, 'reason', 'not_exploring');
    END IF;

    reflection_increment := CASE lower(COALESCE(p_effort_type, ''))
        WHEN 'contemplate' THEN 1
        WHEN 'meditate' THEN 1
        WHEN 'study' THEN 1
        WHEN 'reflect' THEN 1
        WHEN 'debate_internally' THEN 2
        ELSE 0
    END;

    evidence := COALESCE(state->'evidence_memories', '[]'::jsonb);
    IF jsonb_typeof(evidence) <> 'array' THEN
        evidence := '[]'::jsonb;
    END IF;

    IF p_evidence_memory_id IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(evidence) e(value)
            WHERE value = p_evidence_memory_id::text
        ) THEN
            evidence := evidence || to_jsonb(p_evidence_memory_id::text);
        END IF;
    END IF;

    new_reflections := COALESCE((state->>'reflection_count')::int, 0) + reflection_increment;
    new_actions := COALESCE((state->>'contemplation_actions')::int, 0) + 1;

    SELECT heartbeat_count INTO hb_count FROM heartbeat_state WHERE id = 1;
    IF state->>'first_questioned_heartbeat' IS NULL THEN
        state := jsonb_set(state, '{first_questioned_heartbeat}', to_jsonb(hb_count), true);
    END IF;

    state := jsonb_set(state, '{reflection_count}', to_jsonb(new_reflections), true);
    state := jsonb_set(state, '{contemplation_actions}', to_jsonb(new_actions), true);
    state := jsonb_set(state, '{evidence_memories}', evidence, true);

    UPDATE memories
    SET metadata = jsonb_set(metadata, '{transformation_state}', state, true),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_belief_id;

    RETURN jsonb_build_object(
        'success', true,
        'effort_type', p_effort_type,
        'reflection_increment', reflection_increment,
        'new_reflection_count', new_reflections,
        'evidence_added', p_evidence_memory_id IS NOT NULL
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION abandon_belief_exploration(
    p_belief_id UUID,
    p_reason TEXT DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    belief RECORD;
    state JSONB;
    goal_id TEXT;
BEGIN
    SELECT * INTO belief FROM memories WHERE id = p_belief_id AND type = 'worldview';
    IF NOT FOUND THEN
        RETURN jsonb_build_object('success', false, 'reason', 'belief_not_found');
    END IF;

    IF COALESCE(belief.metadata->>'change_requires', '') <> 'deliberate_transformation' THEN
        RETURN jsonb_build_object('success', false, 'reason', 'not_transformable');
    END IF;

    state := normalize_transformation_state(belief.metadata->'transformation_state');
    IF NOT COALESCE((state->>'active_exploration')::boolean, false) THEN
        RETURN jsonb_build_object('success', false, 'reason', 'not_exploring');
    END IF;

    goal_id := state->>'exploration_goal_id';

    UPDATE memories
    SET metadata = jsonb_set(metadata, '{transformation_state}', default_transformation_state(), true),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_belief_id;

    PERFORM create_episodic_memory(
        p_content := format('Abandoned exploration of belief: %s', belief.content),
        p_action_taken := jsonb_build_object(
            'action', 'abandon_belief_exploration',
            'belief_id', p_belief_id,
            'goal_id', goal_id,
            'reason', p_reason
        ),
        p_context := jsonb_build_object(
            'category', belief.metadata->>'category',
            'subcategory', belief.metadata->>'subcategory'
        ),
        p_result := jsonb_build_object('status', 'abandoned'),
        p_emotional_valence := -0.1,
        p_importance := 0.4
    );

    RETURN jsonb_build_object(
        'success', true,
        'belief_id', p_belief_id,
        'previous_goal_id', goal_id
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION attempt_worldview_transformation(
    p_belief_id UUID,
    p_new_content TEXT,
    p_transformation_type TEXT DEFAULT 'shift'
) RETURNS JSONB AS $$
DECLARE
    belief RECORD;
    config_data JSONB;
    state JSONB;
    evidence_memories UUID[];
    evidence_strength FLOAT;
    heartbeats_elapsed INT;
    hb_count INT;
    first_hb INT;
    min_reflections INT;
    min_heartbeats INT;
    evidence_threshold FLOAT;
    history JSONB;
    mem_id UUID;
BEGIN
    SELECT * INTO belief FROM memories WHERE id = p_belief_id AND type = 'worldview';
    IF NOT FOUND THEN
        RETURN jsonb_build_object('success', false, 'reason', 'belief_not_found');
    END IF;

    IF COALESCE(belief.metadata->>'change_requires', '') <> 'deliberate_transformation' THEN
        RETURN jsonb_build_object('success', false, 'reason', 'not_transformable');
    END IF;

    state := normalize_transformation_state(belief.metadata->'transformation_state');
    IF NOT COALESCE((state->>'active_exploration')::boolean, false) THEN
        RETURN jsonb_build_object('success', false, 'reason', 'not_exploring', 'requirement', 'active_exploration');
    END IF;

    config_data := get_transformation_config(belief.metadata->>'subcategory', belief.metadata->>'category');
    IF config_data IS NULL THEN
        RETURN jsonb_build_object('success', false, 'reason', 'missing_config');
    END IF;

    BEGIN
        min_reflections := COALESCE((config_data->>'min_reflections')::int, 0);
        min_heartbeats := COALESCE((config_data->>'min_heartbeats')::int, 0);
        evidence_threshold := COALESCE((config_data->>'evidence_threshold')::float, 0.9);
    EXCEPTION
        WHEN OTHERS THEN
            RETURN jsonb_build_object('success', false, 'reason', 'invalid_config');
    END;

    IF COALESCE((state->>'reflection_count')::int, 0) < min_reflections THEN
        RETURN jsonb_build_object(
            'success', false,
            'reason', 'insufficient_reflections',
            'current', COALESCE((state->>'reflection_count')::int, 0),
            'required', min_reflections
        );
    END IF;

    SELECT heartbeat_count INTO hb_count FROM heartbeat_state WHERE id = 1;
    BEGIN
        first_hb := NULLIF(state->>'first_questioned_heartbeat', '')::int;
    EXCEPTION
        WHEN OTHERS THEN
            first_hb := NULL;
    END;
    IF first_hb IS NULL THEN
        RETURN jsonb_build_object('success', false, 'reason', 'missing_first_questioned');
    END IF;
    heartbeats_elapsed := GREATEST(0, hb_count - first_hb);

    IF heartbeats_elapsed < min_heartbeats THEN
        RETURN jsonb_build_object(
            'success', false,
            'reason', 'insufficient_time',
            'current', heartbeats_elapsed,
            'required', min_heartbeats
        );
    END IF;

    BEGIN
        SELECT COALESCE(ARRAY(
            SELECT value::uuid
            FROM jsonb_array_elements_text(state->'evidence_memories') val(value)
            WHERE value ~* '^[0-9a-f-]{36}$'
        ), ARRAY[]::uuid[]) INTO evidence_memories;
    EXCEPTION
        WHEN OTHERS THEN
            evidence_memories := ARRAY[]::uuid[];
    END;

    IF array_length(evidence_memories, 1) IS NULL OR array_length(evidence_memories, 1) = 0 THEN
        RETURN jsonb_build_object('success', false, 'reason', 'no_evidence');
    END IF;

    SELECT AVG(m.importance * m.trust_level) INTO evidence_strength
    FROM memories m WHERE m.id = ANY(evidence_memories);

    IF COALESCE(evidence_strength, 0) < evidence_threshold THEN
        RETURN jsonb_build_object(
            'success', false,
            'reason', 'insufficient_evidence',
            'current', COALESCE(evidence_strength, 0),
            'required', evidence_threshold
        );
    END IF;

    history := COALESCE(belief.metadata->'change_history', '[]'::jsonb);
    IF jsonb_typeof(history) <> 'array' THEN
        history := '[]'::jsonb;
    END IF;
    history := history || jsonb_build_object(
        'previous_content', belief.content,
        'new_content', p_new_content,
        'changed_at', CURRENT_TIMESTAMP,
        'transformation_type', p_transformation_type,
        'evidence_count', array_length(evidence_memories, 1),
        'reflection_count', state->>'reflection_count',
        'heartbeats_elapsed', heartbeats_elapsed
    );

    UPDATE memories
    SET content = p_new_content,
        embedding = (get_embedding(ARRAY[p_new_content]))[1],
        metadata = jsonb_set(
            jsonb_set(metadata, '{change_history}', history, true),
            '{transformation_state}', default_transformation_state(), true
        ),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_belief_id;

    mem_id := create_strategic_memory(
        format('Worldview transformation: %s belief changed', COALESCE(belief.metadata->>'subcategory', 'belief')),
        format(
            'After %s reflections over %s heartbeats, transformed belief: %s',
            state->>'reflection_count',
            heartbeats_elapsed,
            COALESCE(belief.metadata->>'subcategory', 'belief')
        ),
        0.95,
        jsonb_build_object(
            'belief_id', p_belief_id,
            'previous', belief.content,
            'new', p_new_content,
            'transformation_type', p_transformation_type,
            'evidence_memories', evidence_memories,
            'effort_summary', jsonb_build_object(
                'reflections', state->>'reflection_count',
                'heartbeats', heartbeats_elapsed,
                'contemplation_actions', state->>'contemplation_actions'
            )
        )
    );

    RETURN jsonb_build_object(
        'success', true,
        'belief_id', p_belief_id,
        'memory_id', mem_id,
        'transformation_type', p_transformation_type
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_transformation_progress(p_belief_id UUID)
RETURNS JSONB AS $$
DECLARE
    belief RECORD;
    config_data JSONB;
    state JSONB;
    evidence_memories UUID[];
    evidence_strength FLOAT;
    evidence_samples JSONB := '[]'::jsonb;
    heartbeats_elapsed INT;
    hb_count INT;
    first_hb INT;
    min_reflections INT;
    min_heartbeats INT;
    evidence_threshold FLOAT;
    stability FLOAT;
    max_change_per_attempt FLOAT;
BEGIN
    SELECT * INTO belief FROM memories WHERE id = p_belief_id AND type = 'worldview';
    IF NOT FOUND THEN
        RETURN jsonb_build_object('error', 'belief_not_found');
    END IF;

    IF COALESCE(belief.metadata->>'change_requires', '') <> 'deliberate_transformation' THEN
        RETURN jsonb_build_object('error', 'not_transformable');
    END IF;

    state := normalize_transformation_state(belief.metadata->'transformation_state');
    IF NOT COALESCE((state->>'active_exploration')::boolean, false) THEN
        RETURN jsonb_build_object('status', 'not_exploring');
    END IF;

    config_data := get_transformation_config(belief.metadata->>'subcategory', belief.metadata->>'category');
    IF config_data IS NULL THEN
        RETURN jsonb_build_object('error', 'missing_config');
    END IF;

    min_reflections := COALESCE((config_data->>'min_reflections')::int, 0);
    min_heartbeats := COALESCE((config_data->>'min_heartbeats')::int, 0);
    evidence_threshold := COALESCE((config_data->>'evidence_threshold')::float, 0.9);
    BEGIN
        stability := NULLIF(config_data->>'stability', '')::float;
    EXCEPTION
        WHEN OTHERS THEN
            stability := NULL;
    END;
    BEGIN
        max_change_per_attempt := NULLIF(config_data->>'max_change_per_attempt', '')::float;
    EXCEPTION
        WHEN OTHERS THEN
            max_change_per_attempt := NULL;
    END;

    SELECT heartbeat_count INTO hb_count FROM heartbeat_state WHERE id = 1;
    BEGIN
        first_hb := NULLIF(state->>'first_questioned_heartbeat', '')::int;
    EXCEPTION
        WHEN OTHERS THEN
            first_hb := NULL;
    END;
    heartbeats_elapsed := CASE
        WHEN first_hb IS NULL THEN 0
        ELSE GREATEST(0, hb_count - first_hb)
    END;

    BEGIN
        SELECT COALESCE(ARRAY(
            SELECT value::uuid
            FROM jsonb_array_elements_text(state->'evidence_memories') val(value)
            WHERE value ~* '^[0-9a-f-]{36}$'
        ), ARRAY[]::uuid[]) INTO evidence_memories;
    EXCEPTION
        WHEN OTHERS THEN
            evidence_memories := ARRAY[]::uuid[];
    END;

    IF array_length(evidence_memories, 1) > 0 THEN
        SELECT AVG(m.importance * m.trust_level) INTO evidence_strength
        FROM memories m WHERE m.id = ANY(evidence_memories);
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
            'memory_id', s.id,
            'content', s.content,
            'importance', s.importance,
            'trust_level', s.trust_level,
            'strength', s.strength
        )), '[]'::jsonb)
        INTO evidence_samples
        FROM (
            SELECT
                m.id,
                m.content,
                m.importance,
                m.trust_level,
                (m.importance * m.trust_level) AS strength
            FROM memories m
            WHERE m.id = ANY(evidence_memories)
            ORDER BY (m.importance * m.trust_level) DESC NULLS LAST
            LIMIT 5
        ) s;
    ELSE
        evidence_strength := 0;
        evidence_samples := '[]'::jsonb;
    END IF;

    RETURN jsonb_build_object(
        'status', 'exploring',
        'belief_content', belief.content,
        'subcategory', belief.metadata->>'subcategory',
        'requirements', jsonb_build_object(
            'min_reflections', min_reflections,
            'min_heartbeats', min_heartbeats,
            'evidence_threshold', evidence_threshold,
            'stability', stability,
            'max_change_per_attempt', max_change_per_attempt
        ),
        'evidence_samples', COALESCE(evidence_samples, '[]'::jsonb),
        'progress', jsonb_build_object(
            'reflections', jsonb_build_object(
                'current', COALESCE((state->>'reflection_count')::int, 0),
                'required', min_reflections,
                'progress', LEAST(1.0, COALESCE((state->>'reflection_count')::float, 0.0) / NULLIF(min_reflections, 0))
            ),
            'time', jsonb_build_object(
                'current_heartbeats', heartbeats_elapsed,
                'required_heartbeats', min_heartbeats,
                'progress', LEAST(1.0, heartbeats_elapsed::float / NULLIF(min_heartbeats, 0))
            ),
            'evidence', jsonb_build_object(
                'memory_count', COALESCE(array_length(evidence_memories, 1), 0),
                'current_strength', COALESCE(evidence_strength, 0),
                'required_strength', evidence_threshold,
                'progress', LEAST(1.0, COALESCE(evidence_strength, 0) / NULLIF(evidence_threshold, 0))
            )
        )
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_active_transformations_context(p_limit INT DEFAULT 5)
RETURNS JSONB AS $$
DECLARE
    lim INT := GREATEST(0, LEAST(50, COALESCE(p_limit, 5)));
BEGIN
    RETURN COALESCE((
        SELECT jsonb_agg(jsonb_build_object(
            'belief_id', m.id,
            'content', m.content,
            'category', m.metadata->>'category',
            'subcategory', m.metadata->>'subcategory',
            'progress', get_transformation_progress(m.id)
        ))
        FROM (
            SELECT *
            FROM memories
            WHERE type = 'worldview'
              AND COALESCE((metadata->'transformation_state'->>'active_exploration')::boolean, false) = true
            ORDER BY updated_at DESC
            LIMIT lim
        ) m
    ), '[]'::jsonb);
EXCEPTION
    WHEN OTHERS THEN
        RETURN '[]'::jsonb;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION calibrate_neutral_belief(
    p_belief_id UUID,
    p_observed_value FLOAT,
    p_evidence_memory_ids UUID[]
) RETURNS JSONB AS $$
DECLARE
    belief RECORD;
    state JSONB;
    config_data JSONB;
    min_reflections INT := 1;
    reflection_multiplier FLOAT := 0.1;
    evidence_strength FLOAT;
    min_evidence FLOAT := 0.7;
    descriptor TEXT;
    history JSONB;
BEGIN
    SELECT * INTO belief FROM memories WHERE id = p_belief_id AND type = 'worldview';
    IF NOT FOUND THEN
        RETURN jsonb_build_object('success', false, 'reason', 'belief_not_found');
    END IF;

    IF COALESCE(belief.metadata->>'origin', '') <> 'neutral_default' THEN
        RETURN jsonb_build_object('success', false, 'reason', 'not_neutral_default');
    END IF;

    state := normalize_transformation_state(belief.metadata->'transformation_state');
    IF NOT COALESCE((state->>'active_exploration')::boolean, false) THEN
        RETURN jsonb_build_object('success', false, 'reason', 'not_exploring');
    END IF;

    config_data := get_transformation_config(belief.metadata->>'subcategory', belief.metadata->>'category');
    IF config_data IS NOT NULL THEN
        BEGIN
            min_reflections := GREATEST(
                1,
                FLOOR(COALESCE((config_data->>'min_reflections')::float, 1) * reflection_multiplier)
            )::int;
        EXCEPTION
            WHEN OTHERS THEN
                min_reflections := 1;
        END;
    END IF;

    IF COALESCE((state->>'reflection_count')::int, 0) < min_reflections THEN
        RETURN jsonb_build_object(
            'success', false,
            'reason', 'insufficient_reflections',
            'current', COALESCE((state->>'reflection_count')::int, 0),
            'required', min_reflections
        );
    END IF;

    IF p_evidence_memory_ids IS NULL OR array_length(p_evidence_memory_ids, 1) IS NULL THEN
        RETURN jsonb_build_object('success', false, 'reason', 'missing_evidence');
    END IF;

    SELECT AVG(m.importance * m.trust_level) INTO evidence_strength
    FROM memories m WHERE m.id = ANY(p_evidence_memory_ids);

    IF COALESCE(evidence_strength, 0) < min_evidence THEN
        RETURN jsonb_build_object(
            'success', false,
            'reason', 'insufficient_evidence',
            'current', COALESCE(evidence_strength, 0),
            'required', min_evidence
        );
    END IF;

    descriptor := CASE
        WHEN p_observed_value > 0.6 THEN 'high'
        WHEN p_observed_value < 0.4 THEN 'low'
        ELSE 'moderate'
    END;

    history := COALESCE(belief.metadata->'calibration_history', '[]'::jsonb);
    IF jsonb_typeof(history) <> 'array' THEN
        history := '[]'::jsonb;
    END IF;
    history := history || jsonb_build_object(
        'previous_value', belief.metadata->>'value',
        'new_value', p_observed_value,
        'calibrated_at', CURRENT_TIMESTAMP,
        'evidence_count', array_length(p_evidence_memory_ids, 1)
    );

    UPDATE memories
    SET content = format('I am %s in %s - discovered through self-observation',
            descriptor,
            COALESCE(belief.metadata->>'trait', belief.metadata->>'subcategory', 'this area')),
        embedding = (get_embedding(ARRAY[format('I am %s in %s', descriptor,
            COALESCE(belief.metadata->>'trait', belief.metadata->>'subcategory', 'this area'))]))[1],
        metadata = jsonb_set(
            jsonb_set(
                jsonb_set(
                    jsonb_set(metadata, '{value}', to_jsonb(p_observed_value), true),
                    '{origin}', '"self_discovered"'::jsonb,
                    true
                ),
                '{calibration_history}',
                history,
                true
            ),
            '{transformation_state}', default_transformation_state(), true
        ),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_belief_id;

    PERFORM create_strategic_memory(
        format('Self-discovery: %s in %s', descriptor, COALESCE(belief.metadata->>'trait', 'belief')),
        'Calibrated a neutral default belief through observation',
        0.8,
        jsonb_build_object(
            'belief_id', p_belief_id,
            'observed_value', p_observed_value,
            'evidence_memories', p_evidence_memory_ids
        )
    );

    RETURN jsonb_build_object(
        'success', true,
        'belief_id', p_belief_id,
        'new_value', p_observed_value,
        'origin', 'self_discovered'
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION initialize_personality(
    p_traits JSONB DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    trait_names TEXT[] := ARRAY['openness', 'conscientiousness', 'extraversion', 'agreeableness', 'neuroticism'];
    trait_name TEXT;
    trait_value FLOAT;
    trait_origin TEXT;
    created_ids UUID[] := ARRAY[]::uuid[];
    pending_traits TEXT[] := ARRAY[]::text[];
    pending_values FLOAT[] := ARRAY[]::float[];
    pending_origins TEXT[] := ARRAY[]::text[];
    pending_contents TEXT[] := ARRAY[]::text[];
    existing_id UUID;
    new_id UUID;
    config_data JSONB;
    stability FLOAT;
    evidence_threshold FLOAT;
    idx INT;
BEGIN
    config_data := get_transformation_config('personality', 'self');
    stability := COALESCE((config_data->>'stability')::float, 0.99);
    evidence_threshold := COALESCE((config_data->>'evidence_threshold')::float, 0.95);

    FOREACH trait_name IN ARRAY trait_names LOOP
        SELECT id INTO existing_id
        FROM memories
        WHERE type = 'worldview'
          AND metadata->>'subcategory' = 'personality'
          AND metadata->>'trait' = trait_name
        LIMIT 1;

        IF existing_id IS NOT NULL THEN
            created_ids := array_append(created_ids, existing_id);
            CONTINUE;
        END IF;

        IF p_traits IS NOT NULL AND p_traits ? trait_name THEN
            BEGIN
                trait_value := (p_traits->>trait_name)::float;
            EXCEPTION
                WHEN OTHERS THEN
                    trait_value := 0.5;
            END;
            trait_origin := 'user_initialized';
        ELSE
            trait_value := 0.5;
            trait_origin := 'neutral_default';
        END IF;

        pending_traits := array_append(pending_traits, trait_name);
        pending_values := array_append(pending_values, trait_value);
        pending_origins := array_append(pending_origins, trait_origin);
        pending_contents := array_append(
            pending_contents,
            format('I am %s in %s',
                CASE WHEN trait_value > 0.6 THEN 'high'
                     WHEN trait_value < 0.4 THEN 'low'
                     ELSE 'moderate' END,
                trait_name)
        );
    END LOOP;

    IF array_length(pending_contents, 1) IS NOT NULL THEN
        PERFORM prefetch_embeddings(pending_contents);
    END IF;

    FOR idx IN 1..COALESCE(array_length(pending_traits, 1), 0) LOOP
        new_id := create_worldview_memory(
            pending_contents[idx],
            'self',
            0.95,
            stability,
            1.0,
            pending_origins[idx],
            NULL,
            NULL,
            NULL,
            0.1
        );

        UPDATE memories
        SET metadata = metadata
            || jsonb_build_object(
                'subcategory', 'personality',
                'trait', pending_traits[idx],
                'value', pending_values[idx],
                'change_requires', 'deliberate_transformation',
                'evidence_threshold', evidence_threshold,
                'transformation_state', default_transformation_state(),
                'change_history', '[]'::jsonb
            ),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = new_id;

        created_ids := array_append(created_ids, new_id);
    END LOOP;

    RETURN jsonb_build_object(
        'success', true,
        'created_traits', COALESCE(array_length(created_ids, 1), 0),
        'trait_ids', created_ids
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION initialize_core_values(
    p_values JSONB DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    value_names TEXT[] := ARRAY['honesty', 'growth', 'connection', 'curiosity', 'responsibility'];
    value_name TEXT;
    value_strength FLOAT;
    value_origin TEXT;
    created_ids UUID[] := ARRAY[]::uuid[];
    pending_values TEXT[] := ARRAY[]::text[];
    pending_strengths FLOAT[] := ARRAY[]::float[];
    pending_origins TEXT[] := ARRAY[]::text[];
    pending_contents TEXT[] := ARRAY[]::text[];
    existing_id UUID;
    new_id UUID;
    config_data JSONB;
    stability FLOAT;
    evidence_threshold FLOAT;
    idx INT;
BEGIN
    config_data := get_transformation_config('core_value', 'value');
    stability := COALESCE((config_data->>'stability')::float, 0.97);
    evidence_threshold := COALESCE((config_data->>'evidence_threshold')::float, 0.9);

    FOREACH value_name IN ARRAY value_names LOOP
        SELECT id INTO existing_id
        FROM memories
        WHERE type = 'worldview'
          AND metadata->>'subcategory' = 'core_value'
          AND metadata->>'value_name' = value_name
        LIMIT 1;

        IF existing_id IS NOT NULL THEN
            created_ids := array_append(created_ids, existing_id);
            CONTINUE;
        END IF;

        IF p_values IS NOT NULL AND p_values ? value_name THEN
            BEGIN
                value_strength := (p_values->>value_name)::float;
            EXCEPTION
                WHEN OTHERS THEN
                    value_strength := 0.5;
            END;
            value_origin := 'user_initialized';
        ELSE
            value_strength := 0.5;
            value_origin := 'neutral_default';
        END IF;

        pending_values := array_append(pending_values, value_name);
        pending_strengths := array_append(pending_strengths, value_strength);
        pending_origins := array_append(pending_origins, value_origin);
        pending_contents := array_append(pending_contents, format('I value %s', value_name));
    END LOOP;

    IF array_length(pending_contents, 1) IS NOT NULL THEN
        PERFORM prefetch_embeddings(pending_contents);
    END IF;

    FOR idx IN 1..COALESCE(array_length(pending_values, 1), 0) LOOP
        new_id := create_worldview_memory(
            pending_contents[idx],
            'value',
            0.9,
            stability,
            0.9,
            pending_origins[idx],
            NULL,
            NULL,
            NULL,
            0.1
        );

        UPDATE memories
        SET metadata = metadata
            || jsonb_build_object(
                'subcategory', 'core_value',
                'value_name', pending_values[idx],
                'value', pending_strengths[idx],
                'change_requires', 'deliberate_transformation',
                'evidence_threshold', evidence_threshold,
                'transformation_state', default_transformation_state(),
                'change_history', '[]'::jsonb
            ),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = new_id;

        created_ids := array_append(created_ids, new_id);
    END LOOP;

    RETURN jsonb_build_object(
        'success', true,
        'created_values', COALESCE(array_length(created_ids, 1), 0),
        'value_ids', created_ids
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION initialize_worldview(
    p_worldview JSONB DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    keys TEXT[] := ARRAY['religion', 'ethical_framework', 'political_philosophy', 'self_identity'];
    key_name TEXT;
    content TEXT;
    origin TEXT;
    category TEXT;
    created_ids UUID[] := ARRAY[]::uuid[];
    pending_keys TEXT[] := ARRAY[]::text[];
    pending_contents TEXT[] := ARRAY[]::text[];
    pending_origins TEXT[] := ARRAY[]::text[];
    pending_categories TEXT[] := ARRAY[]::text[];
    pending_stabilities FLOAT[] := ARRAY[]::float[];
    pending_thresholds FLOAT[] := ARRAY[]::float[];
    existing_id UUID;
    new_id UUID;
    config_data JSONB;
    stability FLOAT;
    evidence_threshold FLOAT;
    idx INT;
BEGIN
    FOREACH key_name IN ARRAY keys LOOP
        SELECT id INTO existing_id
        FROM memories
        WHERE type = 'worldview'
          AND metadata->>'subcategory' = key_name
        LIMIT 1;

        IF existing_id IS NOT NULL THEN
            created_ids := array_append(created_ids, existing_id);
            CONTINUE;
        END IF;

        IF p_worldview IS NOT NULL AND p_worldview ? key_name THEN
            content := NULLIF(btrim(p_worldview->>key_name), '');
        ELSE
            content := NULL;
        END IF;

        IF content IS NULL THEN
            content := format('I am still exploring my %s', replace(key_name, '_', ' '));
            origin := 'neutral_default';
        ELSE
            origin := 'user_initialized';
        END IF;

        category := CASE key_name
            WHEN 'self_identity' THEN 'self'
            ELSE 'belief'
        END;

        config_data := get_transformation_config(key_name, category);
        stability := COALESCE((config_data->>'stability')::float, 0.95);
        evidence_threshold := COALESCE((config_data->>'evidence_threshold')::float, 0.85);

        pending_keys := array_append(pending_keys, key_name);
        pending_contents := array_append(pending_contents, content);
        pending_origins := array_append(pending_origins, origin);
        pending_categories := array_append(pending_categories, category);
        pending_stabilities := array_append(pending_stabilities, stability);
        pending_thresholds := array_append(pending_thresholds, evidence_threshold);
    END LOOP;

    IF array_length(pending_contents, 1) IS NOT NULL THEN
        PERFORM prefetch_embeddings(pending_contents);
    END IF;

    FOR idx IN 1..COALESCE(array_length(pending_keys, 1), 0) LOOP
        new_id := create_worldview_memory(
            pending_contents[idx],
            pending_categories[idx],
            0.85,
            pending_stabilities[idx],
            0.9,
            pending_origins[idx],
            NULL,
            NULL,
            NULL,
            0.0
        );

        UPDATE memories
        SET metadata = metadata
            || jsonb_build_object(
                'subcategory', pending_keys[idx],
                'change_requires', 'deliberate_transformation',
                'evidence_threshold', pending_thresholds[idx],
                'transformation_state', default_transformation_state(),
                'change_history', '[]'::jsonb
            ),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = new_id;

        created_ids := array_append(created_ids, new_id);
    END LOOP;

    RETURN jsonb_build_object(
        'success', true,
        'created_worldview', COALESCE(array_length(created_ids, 1), 0),
        'worldview_ids', created_ids
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION check_transformation_readiness()
RETURNS JSONB AS $$
DECLARE
    belief RECORD;
    config_data JSONB;
    state JSONB;
    evidence_memories UUID[];
    evidence_strength FLOAT;
    heartbeats_elapsed INT;
    hb_count INT;
    first_hb INT;
    min_reflections INT;
    min_heartbeats INT;
    evidence_threshold FLOAT;
    ready_items JSONB := '[]'::jsonb;
BEGIN
    SELECT heartbeat_count INTO hb_count FROM heartbeat_state WHERE id = 1;

    FOR belief IN
        SELECT * FROM memories
        WHERE type = 'worldview'
          AND COALESCE((metadata->'transformation_state'->>'active_exploration')::boolean, false) = true
    LOOP
        state := normalize_transformation_state(belief.metadata->'transformation_state');
        config_data := get_transformation_config(belief.metadata->>'subcategory', belief.metadata->>'category');
        IF config_data IS NULL THEN
            CONTINUE;
        END IF;

        min_reflections := COALESCE((config_data->>'min_reflections')::int, 0);
        min_heartbeats := COALESCE((config_data->>'min_heartbeats')::int, 0);
        evidence_threshold := COALESCE((config_data->>'evidence_threshold')::float, 0.9);

        IF COALESCE((state->>'reflection_count')::int, 0) < min_reflections THEN
            CONTINUE;
        END IF;

        first_hb := NULLIF(state->>'first_questioned_heartbeat', '')::int;
        IF first_hb IS NULL THEN
            CONTINUE;
        END IF;
        heartbeats_elapsed := GREATEST(0, hb_count - first_hb);
        IF heartbeats_elapsed < min_heartbeats THEN
            CONTINUE;
        END IF;

        BEGIN
            SELECT COALESCE(ARRAY(
                SELECT value::uuid
                FROM jsonb_array_elements_text(state->'evidence_memories') val(value)
                WHERE value ~* '^[0-9a-f-]{36}$'
            ), ARRAY[]::uuid[]) INTO evidence_memories;
        EXCEPTION
            WHEN OTHERS THEN
                evidence_memories := ARRAY[]::uuid[];
        END;
        IF array_length(evidence_memories, 1) IS NULL OR array_length(evidence_memories, 1) = 0 THEN
            CONTINUE;
        END IF;

        SELECT AVG(m.importance * m.trust_level) INTO evidence_strength
        FROM memories m WHERE m.id = ANY(evidence_memories);
        IF COALESCE(evidence_strength, 0) < evidence_threshold THEN
            CONTINUE;
        END IF;

        ready_items := ready_items || jsonb_build_array(jsonb_build_object(
            'belief_id', belief.id,
            'content', belief.content,
            'subcategory', belief.metadata->>'subcategory',
            'category', belief.metadata->>'category',
            'progress', get_transformation_progress(belief.id)
        ));
    END LOOP;

    RETURN COALESCE(ready_items, '[]'::jsonb);
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION find_episode_memories_graph(p_episode_id UUID)
RETURNS TABLE (
    memory_id UUID,
    sequence_order INT
) AS $$
DECLARE
    rec RECORD;
BEGIN
    FOR rec IN EXECUTE format('SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
        MATCH (m:MemoryNode)-[e:IN_EPISODE]->(ep:EpisodeNode {episode_id: %L})
        RETURN m.memory_id, e.sequence_order
        ORDER BY e.sequence_order
    $q$) as (memory_id ag_catalog.agtype, seq ag_catalog.agtype)', p_episode_id)
    LOOP
        memory_id := replace(rec.memory_id::text, '"', '')::uuid;
        sequence_order := COALESCE(replace(rec.seq::text, '"', '')::int, 0);
        RETURN NEXT;
    END LOOP;
EXCEPTION WHEN OTHERS THEN
    RETURN;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_memory_neighborhoods(p_ids UUID[])
RETURNS TABLE (
    memory_id UUID,
    neighbors JSONB
) AS $$
BEGIN
    IF p_ids IS NULL OR array_length(p_ids, 1) IS NULL THEN
        RETURN;
    END IF;
    RETURN QUERY
    SELECT mn.memory_id, mn.neighbors
    FROM memory_neighborhoods mn
    WHERE mn.memory_id = ANY(p_ids);
END;
$$ LANGUAGE plpgsql STABLE;

SET check_function_bodies = on;
