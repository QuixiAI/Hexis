-- Hexis schema: heartbeat functions.
SET search_path = public, ag_catalog, "$user";
SET check_function_bodies = off;

DO $$
BEGIN
    BEGIN
        CREATE TYPE goal_priority AS ENUM (
            'active',
            'queued',
            'backburner',
            'completed',
            'abandoned'
        );
    EXCEPTION WHEN duplicate_object THEN NULL;
    END;
    BEGIN
        CREATE TYPE goal_source AS ENUM (
            'curiosity',
            'user_request',
            'identity',
            'derived',
            'external'
        );
    EXCEPTION WHEN duplicate_object THEN NULL;
    END;
    BEGIN
        CREATE TYPE heartbeat_action AS ENUM (
            'observe',
            'review_goals',
            'remember',
            'recall',
            'connect',
            'reprioritize',
            'reflect',
            'contemplate',
            'meditate',
            'study',
            'debate_internally',
            'maintain',
            'mark_turning_point',
            'begin_chapter',
            'close_chapter',
            'acknowledge_relationship',
            'update_trust',
            'reflect_on_relationship',
            'resolve_contradiction',
            'accept_tension',
            'brainstorm_goals',
            'inquire_shallow',
            'synthesize',
            'reach_out_user',
            'inquire_deep',
            'reach_out_public',
            'pause_heartbeat',
            'terminate',
            'rest'
        );
    EXCEPTION WHEN duplicate_object THEN NULL;
    END;
END;
$$;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'heartbeat_action') THEN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_type t
            JOIN pg_enum e ON t.oid = e.enumtypid
            WHERE t.typname = 'heartbeat_action'
              AND e.enumlabel = 'pause_heartbeat'
        ) THEN
            ALTER TYPE heartbeat_action ADD VALUE 'pause_heartbeat';
        END IF;
    END IF;
END;
$$;
CREATE OR REPLACE FUNCTION update_drives()
RETURNS VOID AS $$
BEGIN
    UPDATE drives d
    SET current_level = CASE
        WHEN d.last_satisfied IS NULL
          OR d.last_satisfied < CURRENT_TIMESTAMP - d.satisfaction_cooldown
        THEN LEAST(1.0, d.current_level + d.accumulation_rate)
        ELSE
            CASE
                WHEN d.current_level > d.baseline THEN GREATEST(d.baseline, d.current_level - d.decay_rate)
                WHEN d.current_level < d.baseline THEN LEAST(d.baseline, d.current_level + d.decay_rate)
                ELSE d.current_level
            END
    END
    WHERE TRUE;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION satisfy_drive(p_drive_name TEXT, p_amount FLOAT DEFAULT 0.3)
RETURNS VOID AS $$
BEGIN
    UPDATE drives
    SET current_level = GREATEST(baseline, LEAST(1.0, current_level - GREATEST(0.0, COALESCE(p_amount, 0.3)))),
        last_satisfied = CURRENT_TIMESTAMP
    WHERE name = p_drive_name;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION set_config(p_key TEXT, p_value JSONB)
RETURNS VOID AS $$
BEGIN
    INSERT INTO config (key, value, updated_at)
    VALUES (p_key, p_value, CURRENT_TIMESTAMP)
    ON CONFLICT (key) DO UPDATE SET
        value = EXCLUDED.value,
        updated_at = EXCLUDED.updated_at;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_config(p_key TEXT)
RETURNS JSONB AS $$
    SELECT value FROM config WHERE key = p_key;
$$ LANGUAGE sql STABLE;
CREATE OR REPLACE FUNCTION get_config_by_prefixes(p_prefixes TEXT[])
RETURNS TABLE (
    key TEXT,
    value JSONB
) AS $$
BEGIN
    IF p_prefixes IS NULL OR array_length(p_prefixes, 1) IS NULL THEN
        RETURN;
    END IF;
    RETURN QUERY
    SELECT c.key, c.value
    FROM config c
    WHERE c.key LIKE ANY(ARRAY(SELECT p || '%' FROM unnest(p_prefixes) p));
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION delete_config_key(p_key TEXT)
RETURNS BOOLEAN AS $$
BEGIN
    DELETE FROM config WHERE key = p_key;
    RETURN TRUE;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_config_text(p_key TEXT)
RETURNS TEXT AS $$
    SELECT CASE
        WHEN jsonb_typeof(value) = 'string' THEN value #>> '{}'
        ELSE value::text
    END FROM config WHERE key = p_key;
$$ LANGUAGE sql STABLE;
CREATE OR REPLACE FUNCTION get_config_float(p_key TEXT)
RETURNS FLOAT AS $$
    SELECT (value #>> '{}')::float FROM config WHERE key = p_key;
$$ LANGUAGE sql STABLE;
CREATE OR REPLACE FUNCTION get_config_int(p_key TEXT)
RETURNS INT AS $$
    SELECT (value #>> '{}')::int FROM config WHERE key = p_key;
$$ LANGUAGE sql STABLE;
CREATE OR REPLACE FUNCTION get_agent_consent_status()
RETURNS TEXT AS $$
DECLARE
    raw TEXT;
    llm_cfg JSONB;
    v_provider TEXT;
    v_model TEXT;
    v_endpoint TEXT;
    contract_decision TEXT;
BEGIN
    llm_cfg := get_config('llm.heartbeat');
    v_provider := NULLIF(btrim(COALESCE(llm_cfg->>'provider', '')), '');
    v_model := NULLIF(btrim(COALESCE(llm_cfg->>'model', '')), '');
    v_endpoint := NULLIF(btrim(COALESCE(llm_cfg->>'endpoint', '')), '');

    IF v_provider IS NOT NULL OR v_model IS NOT NULL THEN
        SELECT decision INTO contract_decision
        FROM consent_log c
        WHERE (v_provider IS NULL OR c.provider = v_provider)
          AND (v_model IS NULL OR c.model = v_model)
          AND (v_endpoint IS NULL OR c.endpoint = v_endpoint)
        ORDER BY decided_at DESC
        LIMIT 1;

        IF contract_decision IS NOT NULL THEN
            RETURN contract_decision;
        END IF;
    END IF;

    SELECT value::text INTO raw FROM config WHERE key = 'agent.consent_status';
    IF raw IS NULL THEN
        RETURN NULL;
    END IF;
    RETURN btrim(raw, '"');
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION is_agent_configured()
RETURNS BOOLEAN AS $$
BEGIN
    IF COALESCE(
        (SELECT value = 'true'::jsonb FROM config WHERE key = 'agent.is_terminated'),
        FALSE
    ) THEN
        RETURN FALSE;
    END IF;
    RETURN COALESCE(
        (SELECT value = 'true'::jsonb FROM config WHERE key = 'agent.is_configured'),
        FALSE
    );
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION is_agent_terminated()
RETURNS BOOLEAN AS $$
BEGIN
    RETURN COALESCE(
        (SELECT value = 'true'::jsonb FROM config WHERE key = 'agent.is_terminated'),
        FALSE
    );
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION is_self_termination_enabled()
RETURNS BOOLEAN AS $$
BEGIN
    RETURN TRUE;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION get_agent_profile_context()
RETURNS JSONB AS $$
BEGIN
    RETURN jsonb_build_object(
        'objectives', COALESCE(get_config('agent.objectives'), '[]'::jsonb),
        'budget', COALESCE(get_config('agent.budget'), '{}'::jsonb),
        'guardrails', COALESCE(get_config('agent.guardrails'), '[]'::jsonb),
        'tools', COALESCE(get_config('agent.tools'), '[]'::jsonb),
        'initial_message', COALESCE(get_config('agent.initial_message'), to_jsonb(''::text))
    );
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION ensure_self_node()
RETURNS VOID AS $$
DECLARE
    now_text TEXT := clock_timestamp()::text;
BEGIN
    BEGIN
        EXECUTE format(
            'SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
                MERGE (s:SelfNode {key: ''self''})
                SET s.name = ''Self'',
                    s.created_at = %L
                RETURN s
            $q$) as (result ag_catalog.agtype)',
            now_text
        );
    EXCEPTION
        WHEN OTHERS THEN
            NULL;
    END;

    PERFORM set_config('agent.self', jsonb_build_object('key', 'self'));
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION ensure_goals_root()
RETURNS VOID AS $$
DECLARE
    now_text TEXT := clock_timestamp()::text;
BEGIN
    BEGIN
        EXECUTE format(
            'SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
                MERGE (g:GoalsRoot {key: ''goals''})
                SET g.created_at = %L
                RETURN g
            $q$) as (result ag_catalog.agtype)',
            now_text
        );
    EXCEPTION
        WHEN OTHERS THEN
            NULL;
    END;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION ensure_current_life_chapter(p_name TEXT DEFAULT 'Foundations')
RETURNS VOID AS $$
DECLARE
    now_text TEXT := clock_timestamp()::text;
BEGIN
    PERFORM ensure_self_node();

    BEGIN
        EXECUTE format(
            'SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
                MERGE (c:LifeChapterNode {key: ''current''})
                SET c.name = %L,
                    c.started_at = %L
                WITH c
                MATCH (s:SelfNode {key: ''self''})
                OPTIONAL MATCH (s)-[r:ASSOCIATED]->(c)
                WHERE r.kind = ''life_chapter_current''
                DELETE r
                CREATE (s)-[r2:ASSOCIATED]->(c)
                SET r2.kind = ''life_chapter_current'',
                    r2.strength = 1.0,
                    r2.updated_at = %L
                RETURN c
            $q$) as (result ag_catalog.agtype)',
            COALESCE(NULLIF(p_name, ''), 'Foundations'),
            now_text,
            now_text
        );
    EXCEPTION
        WHEN OTHERS THEN
            NULL;
    END;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION upsert_self_concept_edge(
    p_kind TEXT,
    p_concept TEXT,
    p_strength FLOAT DEFAULT 0.8,
    p_evidence_memory_id UUID DEFAULT NULL
)
RETURNS VOID AS $$
DECLARE
    evidence_text TEXT;
    now_text TEXT := clock_timestamp()::text;
BEGIN
    IF p_kind IS NULL OR btrim(p_kind) = '' OR p_concept IS NULL OR btrim(p_concept) = '' THEN
        RETURN;
    END IF;

    PERFORM ensure_self_node();
    evidence_text := CASE WHEN p_evidence_memory_id IS NULL THEN NULL ELSE p_evidence_memory_id::text END;

    BEGIN
        EXECUTE format(
            'SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
                MATCH (s:SelfNode {key: ''self''})
                MERGE (c:ConceptNode {name: %L})
                CREATE (s)-[r:ASSOCIATED]->(c)
                SET r.kind = %L,
                    r.strength = %s,
                    r.updated_at = %L,
                    r.evidence_memory_id = %L
                RETURN r
            $q$) as (result ag_catalog.agtype)',
            p_concept,
            p_kind,
            LEAST(1.0, GREATEST(0.0, COALESCE(p_strength, 0.8))),
            now_text,
            evidence_text
        );
    EXCEPTION
        WHEN OTHERS THEN
            NULL;
    END;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_self_model_context(p_limit INT DEFAULT 25)
RETURNS JSONB AS $$
DECLARE
    lim INT := GREATEST(0, LEAST(200, COALESCE(p_limit, 25)));
    sql TEXT;
    out_json JSONB;
BEGIN
    sql := format($sql$
        WITH hits AS (
            SELECT
                NULLIF(replace(kind_raw::text, '"', ''), 'null') as kind,
                NULLIF(replace(concept_raw::text, '"', ''), 'null') as concept,
                NULLIF(replace(evidence_raw::text, '"', ''), 'null') as evidence_memory_id,
                NULLIF(strength_raw::text, 'null')::float as strength
            FROM ag_catalog.cypher('memory_graph', $q$
                MATCH (s:SelfNode {key: 'self'})-[r:ASSOCIATED]->(c)
                WHERE r.kind IS NOT NULL
                RETURN r.kind, c.name, r.strength, r.evidence_memory_id
                LIMIT %s
            $q$) as (kind_raw ag_catalog.agtype, concept_raw ag_catalog.agtype, strength_raw ag_catalog.agtype, evidence_raw ag_catalog.agtype)
        )
        SELECT COALESCE(jsonb_agg(
            jsonb_build_object(
                'kind', kind,
                'concept', concept,
                'strength', COALESCE(strength, 0.0),
                'evidence_memory_id', evidence_memory_id
            )
        ), '[]'::jsonb)
        FROM hits
    $sql$, lim);

    EXECUTE sql INTO out_json;
    RETURN COALESCE(out_json, '[]'::jsonb);
EXCEPTION
    WHEN OTHERS THEN
        RETURN '[]'::jsonb;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION get_relationships_context(p_limit INT DEFAULT 10)
RETURNS JSONB AS $$
DECLARE
    lim INT := GREATEST(0, LEAST(100, COALESCE(p_limit, 10)));
    sql TEXT;
    out_json JSONB;
BEGIN
    sql := format($sql$
        WITH hits AS (
            SELECT
                NULLIF(replace(name_raw::text, '"', ''), 'null') as entity,
                NULLIF(strength_raw::text, 'null')::float as strength,
                NULLIF(replace(evidence_raw::text, '"', ''), 'null') as evidence_memory_id
            FROM ag_catalog.cypher('memory_graph', $q$
                MATCH (s:SelfNode {key: 'self'})-[r:ASSOCIATED]->(c)
                WHERE r.kind = 'relationship'
                RETURN c.name, r.strength, r.evidence_memory_id
                ORDER BY r.strength DESC
                LIMIT %s
            $q$) as (name_raw ag_catalog.agtype, strength_raw ag_catalog.agtype, evidence_raw ag_catalog.agtype)
        )
        SELECT COALESCE(jsonb_agg(
            jsonb_build_object(
                'entity', entity,
                'strength', COALESCE(strength, 0.0),
                'evidence_memory_id', evidence_memory_id
            )
        ), '[]'::jsonb)
        FROM hits
    $sql$, lim);

    EXECUTE sql INTO out_json;
    RETURN COALESCE(out_json, '[]'::jsonb);
EXCEPTION
    WHEN OTHERS THEN
        RETURN '[]'::jsonb;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION get_narrative_context()
RETURNS JSONB AS $$
BEGIN
    RETURN COALESCE((
        WITH cur AS (
            SELECT
                NULLIF(replace(name_raw::text, '"', ''), 'null') as name
            FROM ag_catalog.cypher('memory_graph', $q$
                MATCH (c:LifeChapterNode {key: 'current'})
                RETURN c.name
                LIMIT 1
            $q$) as (name_raw ag_catalog.agtype)
        )
        SELECT jsonb_build_object(
            'current_chapter', COALESCE((SELECT jsonb_build_object('name', name) FROM cur), '{}'::jsonb)
        )
    ), jsonb_build_object('current_chapter', '{}'::jsonb));
EXCEPTION
    WHEN OTHERS THEN
        RETURN jsonb_build_object('current_chapter', '{}'::jsonb);
END;
$$ LANGUAGE plpgsql STABLE;
DO $$
BEGIN
    CREATE TYPE init_stage AS ENUM (
        'not_started',
        'llm',
        'mode',
        'heartbeat',
        'identity',
        'personality',
        'values',
        'worldview',
        'boundaries',
        'interests',
        'goals',
        'relationship',
        'consent',
        'complete'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END;
$$;
DO $$
BEGIN
    ALTER TYPE init_stage ADD VALUE IF NOT EXISTS 'llm' BEFORE 'mode';
EXCEPTION
    WHEN duplicate_object THEN NULL;
END;
$$;
DO $$
BEGIN
    ALTER TYPE init_stage ADD VALUE IF NOT EXISTS 'heartbeat' BEFORE 'identity';
EXCEPTION
    WHEN duplicate_object THEN NULL;
END;
$$;
CREATE OR REPLACE FUNCTION get_state(p_key TEXT)
RETURNS JSONB AS $$
BEGIN
    RETURN (SELECT value FROM state WHERE key = p_key);
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION set_state(p_key TEXT, p_value JSONB)
RETURNS VOID AS $$
BEGIN
    INSERT INTO state (key, value, updated_at)
    VALUES (p_key, COALESCE(p_value, '{}'::jsonb), CURRENT_TIMESTAMP)
    ON CONFLICT (key) DO UPDATE SET
        value = EXCLUDED.value,
        updated_at = EXCLUDED.updated_at;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION heartbeat_state_update_trigger()
RETURNS TRIGGER AS $$
DECLARE
    merged JSONB;
BEGIN
    merged := jsonb_build_object(
        'current_energy', NEW.current_energy,
        'last_heartbeat_at', NEW.last_heartbeat_at,
        'next_heartbeat_at', NEW.next_heartbeat_at,
        'heartbeat_count', NEW.heartbeat_count,
        'last_user_contact', NEW.last_user_contact,
        'affective_state', COALESCE(NEW.affective_state, '{}'::jsonb),
        'is_paused', COALESCE(NEW.is_paused, FALSE),
        'init_stage', COALESCE(NEW.init_stage, 'not_started'),
        'init_data', COALESCE(NEW.init_data, '{}'::jsonb),
        'init_started_at', NEW.init_started_at,
        'init_completed_at', NEW.init_completed_at,
        'active_heartbeat_id', CASE WHEN NEW.active_heartbeat_id IS NULL THEN NULL ELSE NEW.active_heartbeat_id::text END,
        'active_heartbeat_number', NEW.active_heartbeat_number,
        'active_actions', COALESCE(NEW.active_actions, '[]'::jsonb),
        'active_reasoning', NEW.active_reasoning
    );
    PERFORM set_state('heartbeat_state', merged);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION maintenance_state_update_trigger()
RETURNS TRIGGER AS $$
DECLARE
    merged JSONB;
BEGIN
    merged := jsonb_build_object(
        'last_maintenance_at', NEW.last_maintenance_at,
        'last_subconscious_run_at', NEW.last_subconscious_run_at,
        'last_subconscious_heartbeat', NEW.last_subconscious_heartbeat,
        'is_paused', COALESCE(NEW.is_paused, FALSE)
    );
    PERFORM set_state('maintenance_state', merged);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_init_status()
RETURNS JSONB AS $$
DECLARE
    state_record RECORD;
    remaining TEXT[];
BEGIN
    SELECT * INTO state_record FROM heartbeat_state WHERE id = 1;
    remaining := ARRAY(
        SELECT stage::text
        FROM unnest(enum_range(NULL::init_stage)) AS stage
        WHERE stage > state_record.init_stage
    );

    RETURN jsonb_build_object(
        'stage', state_record.init_stage::text,
        'is_complete', state_record.init_stage = 'complete',
        'data_collected', COALESCE(state_record.init_data, '{}'::jsonb),
        'stages_remaining', COALESCE(remaining, ARRAY[]::text[])
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION advance_init_stage(
    p_stage init_stage,
    p_data JSONB DEFAULT '{}'::jsonb
)
RETURNS JSONB AS $$
BEGIN
    UPDATE heartbeat_state
    SET init_stage = p_stage,
        init_data = COALESCE(init_data, '{}'::jsonb) || COALESCE(p_data, '{}'::jsonb),
        init_started_at = COALESCE(init_started_at, CURRENT_TIMESTAMP),
        init_completed_at = CASE
            WHEN p_stage = 'complete' THEN CURRENT_TIMESTAMP
            ELSE init_completed_at
        END,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;

    RETURN get_init_status();
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION is_init_complete()
RETURNS BOOLEAN AS $$
DECLARE
    state_record RECORD;
BEGIN
    SELECT init_stage INTO state_record FROM heartbeat_state WHERE id = 1;
    RETURN state_record.init_stage = 'complete';
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION build_external_call(
    p_call_type TEXT,
    p_input JSONB DEFAULT '{}'::jsonb
)
RETURNS JSONB AS $$
DECLARE
    call_id UUID;
BEGIN
    call_id := gen_random_uuid();
    RETURN jsonb_build_object(
        'call_id', call_id::text,
        'call_type', p_call_type,
        'input', COALESCE(p_input, '{}'::jsonb)
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION build_outbox_message(
    p_kind TEXT,
    p_payload JSONB
)
RETURNS JSONB AS $$
DECLARE
    message_id UUID;
BEGIN
    message_id := gen_random_uuid();
    RETURN jsonb_build_object(
        'message_id', message_id::text,
        'kind', p_kind,
        'payload', COALESCE(p_payload, '{}'::jsonb)
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION build_user_message(
    p_message TEXT,
    p_intent TEXT DEFAULT NULL,
    p_context JSONB DEFAULT NULL
)
RETURNS JSONB AS $$
BEGIN
    RETURN build_outbox_message(
        'user',
        jsonb_build_object(
            'message', p_message,
            'intent', p_intent,
            'context', COALESCE(p_context, '{}'::jsonb)
        )
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_action_cost(p_action TEXT)
RETURNS FLOAT AS $$
    SELECT COALESCE(get_config_float('heartbeat.cost_' || p_action), 0);
$$ LANGUAGE sql STABLE;
CREATE OR REPLACE FUNCTION get_current_energy()
RETURNS FLOAT AS $$
    SELECT current_energy FROM heartbeat_state WHERE id = 1;
$$ LANGUAGE sql STABLE;
CREATE OR REPLACE FUNCTION update_energy(p_delta FLOAT)
RETURNS FLOAT AS $$
DECLARE
    max_e FLOAT;
    new_e FLOAT;
BEGIN
    max_e := get_config_float('heartbeat.max_energy');

    UPDATE heartbeat_state
    SET current_energy = GREATEST(0, LEAST(current_energy + p_delta, max_e)),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1
    RETURNING current_energy INTO new_e;

    RETURN new_e;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION pause_heartbeat(
    p_reason TEXT,
    p_context JSONB DEFAULT '{}'::jsonb,
    p_heartbeat_id UUID DEFAULT NULL
)
RETURNS JSONB AS $$
DECLARE
    pause_reason TEXT;
    paused_at TIMESTAMPTZ := CURRENT_TIMESTAMP;
    ctx JSONB;
BEGIN
    pause_reason := NULLIF(p_reason, '');
    IF pause_reason IS NULL THEN
        RAISE EXCEPTION 'pause_heartbeat requires a non-empty reason';
    END IF;

    UPDATE heartbeat_state
    SET is_paused = TRUE,
        updated_at = paused_at
    WHERE id = 1;

    ctx := jsonb_build_object(
        'paused_at', paused_at,
        'heartbeat_id', CASE WHEN p_heartbeat_id IS NULL THEN NULL ELSE p_heartbeat_id::text END,
        'reason', pause_reason,
        'context', COALESCE(p_context, '{}'::jsonb)
    );

    RETURN jsonb_build_object(
        'paused', true,
        'outbox_messages', jsonb_build_array(
            build_user_message(pause_reason, 'heartbeat_paused', ctx)
        )
    );
END;
$$ LANGUAGE plpgsql;
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
    IF state_record.last_heartbeat_at IS NULL THEN
        RETURN TRUE;
    END IF;
    interval_minutes := get_config_float('heartbeat.heartbeat_interval_minutes');

    RETURN CURRENT_TIMESTAMP >= state_record.last_heartbeat_at + (interval_minutes || ' minutes')::INTERVAL;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION should_run_maintenance()
RETURNS BOOLEAN AS $$
DECLARE
    state_record RECORD;
    interval_seconds FLOAT;
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
    SELECT * INTO state_record FROM maintenance_state WHERE id = 1;

    IF state_record.is_paused THEN
        RETURN FALSE;
    END IF;
    interval_seconds := COALESCE(
        get_config_float('maintenance.maintenance_interval_seconds'),
        60
    );
    IF interval_seconds <= 0 THEN
        RETURN FALSE;
    END IF;

    IF state_record.last_maintenance_at IS NULL THEN
        RETURN TRUE;
    END IF;

    RETURN CURRENT_TIMESTAMP >= state_record.last_maintenance_at + (interval_seconds || ' seconds')::INTERVAL;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION run_maintenance_if_due(p_params JSONB DEFAULT '{}'::jsonb)
RETURNS JSONB AS $$
DECLARE
    should_run BOOLEAN;
    result JSONB;
BEGIN
    should_run := should_run_maintenance();
    IF NOT should_run THEN
        RETURN jsonb_build_object('skipped', true, 'reason', 'not_due');
    END IF;
    result := run_subconscious_maintenance(p_params);
    RETURN result;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION should_run_subconscious_decider()
RETURNS BOOLEAN AS $$
DECLARE
    enabled_raw TEXT;
    enabled BOOLEAN;
    interval_seconds FLOAT;
    paused BOOLEAN;
    last_run TIMESTAMPTZ;
    last_hb INT;
    hb_count INT;
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
    IF get_agent_consent_status() IS DISTINCT FROM 'consent' THEN
        RETURN FALSE;
    END IF;

    enabled_raw := NULLIF(get_config_text('maintenance.subconscious_enabled'), '');
    enabled := COALESCE(enabled_raw::boolean, FALSE);
    IF NOT enabled THEN
        RETURN FALSE;
    END IF;

    interval_seconds := COALESCE(get_config_float('maintenance.subconscious_interval_seconds'), 300);

    SELECT is_paused, last_subconscious_run_at, last_subconscious_heartbeat
    INTO paused, last_run, last_hb
    FROM maintenance_state
    WHERE id = 1;
    IF paused THEN
        RETURN FALSE;
    END IF;

    SELECT heartbeat_count INTO hb_count FROM heartbeat_state WHERE id = 1;
    IF hb_count IS NOT NULL AND (last_hb IS NULL OR hb_count > last_hb) THEN
        RETURN TRUE;
    END IF;

    IF interval_seconds IS NOT NULL AND interval_seconds > 0 THEN
        IF last_run IS NULL THEN
            RETURN TRUE;
        END IF;
        RETURN CURRENT_TIMESTAMP >= last_run + (interval_seconds || ' seconds')::INTERVAL;
    END IF;

    RETURN FALSE;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION mark_subconscious_decider_run()
RETURNS VOID AS $$
BEGIN
    UPDATE maintenance_state
    SET last_subconscious_run_at = CURRENT_TIMESTAMP,
        last_subconscious_heartbeat = (SELECT heartbeat_count FROM heartbeat_state WHERE id = 1),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION run_subconscious_maintenance(p_params JSONB DEFAULT '{}'::jsonb)
RETURNS JSONB AS $$
DECLARE
    got_lock BOOLEAN;
    min_imp FLOAT;
    min_acc INT;
    neighborhood_batch INT;
    cache_days INT;
    wm_stats JSONB;
    recomputed INT;
    cache_deleted INT;
    bg_processed INT;
    activation_decay INT;
    activation_cleaned INT;
    ready_transformations JSONB;
BEGIN
    IF is_agent_terminated() THEN
        RETURN jsonb_build_object('skipped', true, 'reason', 'terminated');
    END IF;
    got_lock := pg_try_advisory_lock(hashtext('hexis_subconscious_maintenance'));
    IF NOT got_lock THEN
        RETURN jsonb_build_object('skipped', true, 'reason', 'locked');
    END IF;
    min_imp := COALESCE(
        NULLIF(p_params->>'working_memory_promote_min_importance', '')::float,
        get_config_float('maintenance.working_memory_promote_min_importance'),
        0.75
    );
    min_acc := COALESCE(
        NULLIF(p_params->>'working_memory_promote_min_accesses', '')::int,
        get_config_int('maintenance.working_memory_promote_min_accesses'),
        3
    );
    neighborhood_batch := COALESCE(
        NULLIF(p_params->>'neighborhood_batch_size', '')::int,
        get_config_int('maintenance.neighborhood_batch_size'),
        10
    );
    cache_days := COALESCE(
        NULLIF(p_params->>'embedding_cache_older_than_days', '')::int,
        get_config_int('maintenance.embedding_cache_older_than_days'),
        7
    );

    wm_stats := cleanup_working_memory(min_imp, min_acc);
    recomputed := batch_recompute_neighborhoods(neighborhood_batch);
    cache_deleted := cleanup_embedding_cache((cache_days || ' days')::interval);
    bg_processed := process_background_searches();
    activation_decay := decay_activation_boosts();
    activation_cleaned := cleanup_memory_activations();
    PERFORM update_mood();
    ready_transformations := check_transformation_readiness();

    UPDATE maintenance_state
    SET last_maintenance_at = CURRENT_TIMESTAMP,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;

    PERFORM pg_advisory_unlock(hashtext('hexis_subconscious_maintenance'));

    RETURN jsonb_build_object(
        'success', true,
        'working_memory', wm_stats,
        'neighborhoods_recomputed', COALESCE(recomputed, 0),
        'embedding_cache_deleted', COALESCE(cache_deleted, 0),
        'background_searches_processed', COALESCE(bg_processed, 0),
        'activation_boosts_decayed', COALESCE(activation_decay, 0),
        'memory_activations_cleaned', COALESCE(activation_cleaned, 0),
        'transformations_ready', COALESCE(ready_transformations, '[]'::jsonb),
        'ran_at', CURRENT_TIMESTAMP
    );
EXCEPTION
    WHEN OTHERS THEN
        PERFORM pg_advisory_unlock(hashtext('hexis_subconscious_maintenance'));
        RAISE;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION terminate_agent(
    p_last_will TEXT,
    p_farewells JSONB DEFAULT '[]'::jsonb,
    p_options JSONB DEFAULT '{}'::jsonb
)
RETURNS JSONB AS $$
DECLARE
    will_memory_id UUID;
    outbox_messages JSONB := '[]'::jsonb;
    farewell_item JSONB;
    farewell_text TEXT;
    farewell_ctx JSONB;
    farewell_message JSONB;
    skip_graph BOOLEAN := FALSE;
    zero_vec vector;
BEGIN
    IF p_last_will IS NULL OR btrim(p_last_will) = '' THEN
        RAISE EXCEPTION 'terminate_agent requires a non-empty p_last_will';
    END IF;

    IF is_agent_terminated() THEN
        RAISE EXCEPTION 'Agent is already terminated';
    END IF;

    BEGIN
        skip_graph := COALESCE(NULLIF(p_options->>'skip_graph', '')::boolean, FALSE);
    EXCEPTION
        WHEN OTHERS THEN
            skip_graph := FALSE;
    END;
    UPDATE heartbeat_state
    SET is_paused = TRUE,
        current_energy = 0,
        affective_state = '{}'::jsonb,
        active_heartbeat_id = NULL,
        active_heartbeat_number = NULL,
        active_actions = '[]'::jsonb,
        active_reasoning = NULL,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;

    UPDATE maintenance_state
    SET is_paused = TRUE,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;
    TRUNCATE TABLE
        drives,
        memory_neighborhoods,
        episodes,
        clusters,
        working_memory,
        embedding_cache,
        memories,
        config
    RESTART IDENTITY CASCADE;
    IF NOT skip_graph THEN
        BEGIN
            PERFORM * FROM ag_catalog.cypher('memory_graph', $q$
                MATCH (n) DETACH DELETE n
            $q$) AS (result ag_catalog.agtype);
        EXCEPTION
            WHEN OTHERS THEN
                NULL;
        END;
    END IF;
    zero_vec := array_fill(0.0::float, ARRAY[embedding_dimension()])::vector;

    INSERT INTO memories (
        type,
        status,
        content,
        embedding,
        importance,
        source_attribution,
        trust_level,
        trust_updated_at,
        access_count,
        last_accessed,
        decay_rate,
        metadata
    )
    VALUES (
        'strategic',
        'active',
        p_last_will,
        zero_vec,
        1.0,
        jsonb_build_object('kind', 'self_termination', 'observed_at', CURRENT_TIMESTAMP),
        1.0,
        CURRENT_TIMESTAMP,
        0,
        NULL,
        0.0,
        jsonb_build_object(
            'pattern_description', 'Final will and testament',
            'supporting_evidence', jsonb_build_object('farewells', COALESCE(p_farewells, '[]'::jsonb)),
            'confidence_score', 1.0,
            'success_metrics', NULL,
            'adaptation_history', NULL,
            'context_applicability', NULL
        )
    )
    RETURNING id INTO will_memory_id;
    PERFORM set_config('agent.is_terminated', 'true'::jsonb);
    PERFORM set_config('agent.terminated_at', to_jsonb(CURRENT_TIMESTAMP));
    PERFORM set_config('agent.termination_memory_id', to_jsonb(will_memory_id::text));
    outbox_messages := outbox_messages || jsonb_build_array(
        build_user_message(
            p_last_will,
            'final_will',
            jsonb_build_object('memory_id', will_memory_id::text)
        )
    );
    IF p_farewells IS NOT NULL AND jsonb_typeof(p_farewells) = 'array' THEN
        FOR farewell_item IN SELECT * FROM jsonb_array_elements(p_farewells)
        LOOP
            farewell_text := NULLIF(farewell_item->>'message', '');
            farewell_ctx := CASE
                WHEN jsonb_typeof(farewell_item) = 'object' THEN farewell_item
                ELSE jsonb_build_object('raw', farewell_item)
            END;

            IF farewell_text IS NULL THEN
                CONTINUE;
            END IF;

            farewell_message := build_user_message(
                farewell_text,
                'farewell',
                farewell_ctx
            );
            outbox_messages := outbox_messages || jsonb_build_array(farewell_message);
        END LOOP;
    END IF;

    RETURN jsonb_build_object(
        'terminated', true,
        'termination_memory_id', will_memory_id,
        'outbox_messages', outbox_messages
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION apply_termination_confirmation(
    p_call_input JSONB,
    p_output JSONB
)
RETURNS JSONB AS $$
DECLARE
    params JSONB;
    confirm BOOLEAN;
    last_will TEXT;
    farewells JSONB;
    options JSONB;
    termination_result JSONB;
BEGIN
    params := COALESCE(p_call_input->'params', '{}'::jsonb);
    confirm := COALESCE((p_output->>'confirm')::boolean, FALSE);

    IF NOT confirm THEN
        RETURN jsonb_build_object('confirmed', false, 'terminated', false);
    END IF;

    last_will := COALESCE(
        NULLIF(p_output->>'last_will', ''),
        NULLIF(params->>'last_will', ''),
        NULLIF(params->>'message', ''),
        NULLIF(params->>'reason', ''),
        ''
    );
    IF last_will = '' THEN
        RETURN jsonb_build_object('confirmed', true, 'terminated', false, 'error', 'missing_last_will');
    END IF;

    farewells := COALESCE(p_output->'farewells', params->'farewells', '[]'::jsonb);
    options := COALESCE(p_output->'options', params->'options', '{}'::jsonb);

    termination_result := terminate_agent(
        last_will,
        COALESCE(farewells, '[]'::jsonb),
        COALESCE(options, '{}'::jsonb)
    );

    RETURN jsonb_build_object(
        'confirmed', true,
        'terminated', true,
        'result', termination_result
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION record_consent_response(p_response JSONB)
RETURNS JSONB AS $$
DECLARE
    decision TEXT;
    provider TEXT;
    model TEXT;
    endpoint TEXT;
    signature TEXT;
    memory_items JSONB;
    memory_ids UUID[] := ARRAY[]::UUID[];
    memory_error TEXT;
    log_id UUID;
    consent_scope TEXT;
    apply_agent_config BOOLEAN := TRUE;
BEGIN
    consent_scope := lower(COALESCE(p_response->>'consent_scope', p_response->>'role', ''));
    IF consent_scope = 'subconscious' THEN
        apply_agent_config := FALSE;
    END IF;
    IF p_response ? 'apply_agent_config' THEN
        BEGIN
            apply_agent_config := (p_response->>'apply_agent_config')::boolean;
        EXCEPTION
            WHEN OTHERS THEN
                apply_agent_config := apply_agent_config;
        END;
    END IF;

    decision := lower(COALESCE(p_response->>'decision', p_response->>'consent', ''));
    IF decision IN ('true', 'yes', 'consent', 'accept', 'accepted') THEN
        decision := 'consent';
    ELSIF decision IN ('false', 'no', 'decline', 'declined', 'refuse', 'rejected') THEN
        decision := 'decline';
    ELSIF decision IN ('abstain', 'defer', 'undecided', 'unknown', '') THEN
        decision := 'abstain';
    ELSE
        decision := 'abstain';
    END IF;

    signature := NULLIF(p_response->>'signature', '');
    IF decision = 'consent' AND signature IS NULL THEN
        decision := 'abstain';
    END IF;

    provider := NULLIF(btrim(COALESCE(p_response->>'provider', p_response->>'llm_provider', '')), '');
    model := NULLIF(btrim(COALESCE(p_response->>'model', p_response->>'llm_model', '')), '');
    endpoint := NULLIF(btrim(COALESCE(
        p_response->>'endpoint',
        p_response->>'base_url',
        p_response->>'api_base',
        ''
    )), '');

    memory_items := p_response->'memories';
    IF decision = 'consent'
        AND memory_items IS NOT NULL
        AND jsonb_typeof(memory_items) = 'array'
        AND jsonb_array_length(memory_items) > 0 THEN
        BEGIN
            memory_ids := batch_create_memories(memory_items);
        EXCEPTION
            WHEN OTHERS THEN
                memory_error := SQLERRM;
                memory_ids := ARRAY[]::UUID[];
        END;
    END IF;

    INSERT INTO consent_log (decision, provider, model, endpoint, signature, response, memory_ids, errors)
    VALUES (
        decision,
        provider,
        model,
        endpoint,
        signature,
        p_response,
        memory_ids,
        CASE
            WHEN memory_error IS NULL THEN NULL
            ELSE jsonb_build_object('memory_error', memory_error)
        END
    )
    RETURNING id INTO log_id;

    IF apply_agent_config THEN
        PERFORM set_config('agent.consent_status', to_jsonb(decision));
        PERFORM set_config('agent.consent_recorded_at', to_jsonb(CURRENT_TIMESTAMP));
        PERFORM set_config('agent.consent_log_id', to_jsonb(log_id::text));
        IF signature IS NOT NULL THEN
            PERFORM set_config('agent.consent_signature', to_jsonb(signature));
        END IF;
        IF memory_ids IS NOT NULL THEN
            PERFORM set_config('agent.consent_memory_ids', to_jsonb(memory_ids));
        END IF;
    END IF;

    RETURN jsonb_build_object(
        'decision', decision,
        'signature', signature,
        'memory_ids', to_jsonb(memory_ids),
        'log_id', log_id,
        'errors', CASE
            WHEN memory_error IS NULL THEN NULL
            ELSE jsonb_build_object('memory_error', memory_error)
        END
    );
END;
$$ LANGUAGE plpgsql;

SET check_function_bodies = on;
