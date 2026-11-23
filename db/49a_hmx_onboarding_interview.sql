-- #97: HMX onboarding interviews -- a use case fully defined in one HMX
-- file. An import can carry an `onboarding_interview` section (schema in
-- schemas/hmx-1.7.schema.json); this table persists per-import interview
-- progress (keyed by the document's own export_id) so it survives restarts
-- and can be resumed, and each answer's binding execution is recorded for
-- provenance/audit, journaled via #93, and surfaced in web inbox for
-- consequential bindings.
SET search_path = public, ag_catalog, "$user";

CREATE TABLE IF NOT EXISTS hmx_interview_state (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    export_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed')),
    interview_version INT NOT NULL,
    questions JSONB NOT NULL,
    answers JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_hmx_interview_state_status
    ON hmx_interview_state (status);

-- Starts (or, idempotently, returns) the persisted interview for one import.
-- p_interview is the envelope's whole onboarding_interview object.
CREATE OR REPLACE FUNCTION start_hmx_interview(
    p_export_id TEXT,
    p_interview JSONB
) RETURNS JSONB AS $$
DECLARE
    existing hmx_interview_state%ROWTYPE;
BEGIN
    SELECT * INTO existing FROM hmx_interview_state WHERE export_id = p_export_id;
    IF FOUND THEN
        RETURN to_jsonb(existing);
    END IF;

    INSERT INTO hmx_interview_state (export_id, interview_version, questions)
    VALUES (
        p_export_id,
        COALESCE((p_interview->>'version')::int, 1),
        COALESCE(p_interview->'questions', '[]'::jsonb)
    )
    RETURNING * INTO existing;
    RETURN to_jsonb(existing);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION get_hmx_interview_state(p_export_id TEXT)
RETURNS JSONB AS $$
    SELECT to_jsonb(s) FROM hmx_interview_state s WHERE export_id = p_export_id;
$$ LANGUAGE sql STABLE;

-- Returns all pending interviews with their remaining unanswered questions,
-- oldest first, so chat, CLI (`hexis interview`), and Web API share one truth.
CREATE OR REPLACE FUNCTION get_pending_hmx_interviews()
RETURNS JSONB AS $$
    SELECT COALESCE(jsonb_agg(item ORDER BY created_at ASC), '[]'::jsonb)
    FROM (
        SELECT
            s.created_at,
            jsonb_build_object(
                'id', s.id::text,
                'export_id', s.export_id,
                'status', s.status,
                'interview_version', s.interview_version,
                'questions', s.questions,
                'answers', s.answers,
                'remaining_questions', COALESCE((
                    SELECT jsonb_agg(q)
                    FROM jsonb_array_elements(s.questions) q
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements(s.answers) a
                        WHERE a->>'question_id' = q->>'id'
                          AND a->>'status' IN ('answered', 'skipped')
                    )
                ), '[]'::jsonb),
                'created_at', s.created_at,
                'updated_at', s.updated_at
            ) AS item
        FROM hmx_interview_state s
        WHERE s.status = 'pending'
    ) pending_rows;
$$ LANGUAGE sql STABLE;

-- Simple `{{answer}}` token substitution into a binding's params_template.
-- Deliberately minimal (no conditionals/loops): the HMX standard's contract
-- here is "one answer fills one template", not a general templating engine.
CREATE OR REPLACE FUNCTION _hmx_interview_fill_template(
    p_template JSONB,
    p_answer_text TEXT
) RETURNS JSONB AS $$
    SELECT CASE jsonb_typeof(p_template)
        WHEN 'string' THEN to_jsonb(replace(p_template #>> '{}', '{{answer}}', COALESCE(p_answer_text, '')))
        WHEN 'object' THEN COALESCE((
            SELECT jsonb_object_agg(key, _hmx_interview_fill_template(value, p_answer_text))
            FROM jsonb_each(p_template)
        ), '{}'::jsonb)
        WHEN 'array' THEN COALESCE((
            SELECT jsonb_agg(_hmx_interview_fill_template(value, p_answer_text))
            FROM jsonb_array_elements(p_template)
        ), '[]'::jsonb)
        ELSE p_template
    END;
$$ LANGUAGE sql IMMUTABLE;

-- Type-preserving variant: when a template string is literally '{{answer}}',
-- preserve the answer's JSON type (boolean, number, array, object, string);
-- otherwise substitute answer text inside the template string.
CREATE OR REPLACE FUNCTION _hmx_interview_fill_template_typed(
    p_template JSONB,
    p_answer JSONB
) RETURNS JSONB AS $$
DECLARE
    answer_text TEXT := CASE jsonb_typeof(p_answer)
        WHEN 'string' THEN p_answer #>> '{}'
        WHEN 'null' THEN ''
        ELSE COALESCE(p_answer::text, '')
    END;
BEGIN
    IF jsonb_typeof(p_template) = 'string' THEN
        IF (p_template #>> '{}') = '{{answer}}' AND p_answer IS NOT NULL THEN
            RETURN p_answer;
        END IF;
        RETURN to_jsonb(replace(p_template #>> '{}', '{{answer}}', COALESCE(answer_text, '')));
    ELSIF jsonb_typeof(p_template) = 'object' THEN
        RETURN COALESCE((
            SELECT jsonb_object_agg(key, _hmx_interview_fill_template_typed(value, p_answer))
            FROM jsonb_each(p_template)
        ), '{}'::jsonb);
    ELSIF jsonb_typeof(p_template) = 'array' THEN
        RETURN COALESCE((
            SELECT jsonb_agg(_hmx_interview_fill_template_typed(value, p_answer))
            FROM jsonb_array_elements(p_template)
        ), '[]'::jsonb);
    END IF;
    RETURN p_template;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Security boundary for HMX interview `set_config` bindings (#97):
-- an HMX file is a portable, shareable artifact, so only safe user/persona/
-- preference/plugin/skill/channel/integration keys may be set, and secret-named
-- keys must hold an environment variable NAME (never a secret value).
CREATE OR REPLACE FUNCTION _hmx_interview_validate_config_binding(
    p_key TEXT,
    p_value JSONB
) RETURNS TEXT AS $$
DECLARE
    key_clean TEXT := btrim(COALESCE(p_key, ''));
    val_text TEXT;
BEGIN
    IF key_clean = '' THEN
        RETURN 'set_config binding requires a non-empty key';
    END IF;
    IF p_value IS NULL THEN
        RETURN 'set_config binding requires a value';
    END IF;

    IF NOT (
        key_clean IN (
            'agent.name',
            'agent.timezone',
            'agent.pronouns',
            'agent.voice',
            'outreach.destination',
            'outreach.slack_user_id',
            'outreach.telegram_chat_id'
        )
        OR key_clean LIKE 'user.%'
        OR key_clean LIKE 'preferences.%'
        OR key_clean LIKE 'plugins.%'
        OR key_clean LIKE 'skills.%'
        OR key_clean LIKE 'channel.%'
        OR key_clean LIKE 'integrations.%'
    ) THEN
        RETURN format('config key %L is not permitted in HMX onboarding interviews', key_clean);
    END IF;

    IF key_clean ~* '(token|secret|password|api_key|private_key|credential)' THEN
        IF jsonb_typeof(p_value) <> 'string' THEN
            RETURN format('secret config key %L must be an environment variable name string', key_clean);
        END IF;
        val_text := btrim(p_value #>> '{}');
        IF val_text !~ '^[A-Z_][A-Z0-9_]*$' THEN
            RETURN format(
                'secret config key %L must be an environment variable name (e.g. MY_API_TOKEN), never a secret value',
                key_clean
            );
        END IF;
    ELSIF jsonb_typeof(p_value) = 'string' THEN
        val_text := p_value #>> '{}';
        IF val_text ~ '(github_pat_[A-Za-z0-9_]{10,}|gh[pousr]_[A-Za-z0-9_]{10,}|sk-[A-Za-z0-9_\-]{12,}|xox[baprs]-[A-Za-z0-9\-]{10,}|xapp-[A-Za-z0-9\-]{10,}|AIza[0-9A-Za-z\-_]{20,}|Bearer[[:space:]]+[A-Za-z0-9._\-]{10,})' THEN
            RETURN format('refusing to store a credential-like value in config key %L', key_clean);
        END IF;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Executes one question's binding against its recorded answer. Returns the
-- executed tool/action result, always with a 'success' key -- an unknown or
-- invalid action is a loud, structured failure, never a silent no-op.
CREATE OR REPLACE FUNCTION apply_hmx_interview_binding(
    p_action TEXT,
    p_params_template JSONB,
    p_answer JSONB,
    p_export_id TEXT,
    p_question_id TEXT
) RETURNS JSONB AS $$
DECLARE
    answer_text TEXT := CASE jsonb_typeof(p_answer)
        WHEN 'string' THEN p_answer #>> '{}'
        WHEN 'null' THEN ''
        ELSE COALESCE(p_answer::text, '')
    END;
    filled JSONB := _hmx_interview_fill_template_typed(COALESCE(p_params_template, '{}'::jsonb), p_answer);
    source_ref JSONB := jsonb_build_object(
        'kind', 'hmx_interview',
        'ref', p_export_id,
        'label', p_question_id
    );
    source_refs JSONB := jsonb_build_array(source_ref);

    -- ingest
    ingest_kind TEXT;
    ingest_url TEXT;
    ingest_content TEXT;
    ingest_payload JSONB;
    ingest_job_id UUID;

    -- init_identity
    id_name TEXT;
    id_pronouns TEXT;
    id_voice TEXT;
    id_description TEXT;
    id_purpose TEXT;
    id_creator TEXT;
    agent_patch JSONB := '{}'::jsonb;
    current_init_stage init_stage;
    mem_id UUID;

    -- create_goal
    goal_title TEXT;
    goal_desc TEXT;
    goal_priority_text TEXT;
    goal_due_at TIMESTAMPTZ;
    new_goal_id UUID;

    -- set_guardrail
    guardrail_content TEXT;
    guardrail_category TEXT;
    guardrail_importance FLOAT;
    restricts_tools_arr TEXT[];
    restricts_cats_arr TEXT[];
    trigger_patterns_arr TEXT[];
    response_type_val TEXT;

    -- create_scheduled_task
    task_name TEXT;
    task_schedule_kind TEXT;
    task_schedule JSONB;
    task_action_kind TEXT;
    task_action_payload JSONB;
    task_timezone TEXT;
    task_description TEXT;
    task_status TEXT;
    task_max_runs INT;
    task_delivery JSONB;
    new_task_id UUID;

    -- set_config
    cfg_key TEXT;
    cfg_val JSONB;
    cfg_error TEXT;
BEGIN
    IF p_action = 'remember' THEN
        -- Provenance always carries the interview's own identity, regardless
        -- of what the HMX author's params_template did or didn't set.
        filled := filled || jsonb_build_object(
            'sources', COALESCE(filled->'sources', '[]'::jsonb) || source_refs
        );
        RETURN execute_memory_tool('remember', filled);

    ELSIF p_action = 'ingest' THEN
        IF NULLIF(btrim(COALESCE(filled->>'path', '')), '') IS NOT NULL
           AND NULLIF(btrim(COALESCE(filled->>'url', '')), '') IS NULL
           AND NULLIF(btrim(COALESCE(filled->>'artifact_id', '')), '') IS NULL
           AND NULLIF(btrim(COALESCE(filled->>'content', filled->>'text', '')), '') IS NULL THEN
            RETURN jsonb_build_object(
                'success', false,
                'action', 'ingest',
                'error', 'ingest binding cannot read arbitrary local file paths; use url (http/https), artifact_id, or text content, or run `hexis ingest` locally',
                'error_type', 'policy_violation'
            );
        END IF;

        ingest_kind := COALESCE(
            NULLIF(lower(btrim(COALESCE(filled->>'kind', ''))), ''),
            CASE
                WHEN NULLIF(btrim(COALESCE(filled->>'url', '')), '') IS NOT NULL THEN 'url'
                WHEN NULLIF(btrim(COALESCE(filled->>'artifact_id', filled->'payload'->>'artifact_id', '')), '') IS NOT NULL THEN 'artifact'
                ELSE 'text'
            END
        );
        ingest_payload := CASE
            WHEN jsonb_typeof(filled->'payload') = 'object' THEN filled->'payload'
            ELSE '{}'::jsonb
        END || jsonb_build_object(
            'source', COALESCE(NULLIF(btrim(COALESCE(filled->>'source', '')), ''), 'hmx_interview'),
            'hmx_export_id', p_export_id,
            'hmx_question_id', p_question_id
        );
        IF NULLIF(btrim(COALESCE(filled->>'title', '')), '') IS NOT NULL THEN
            ingest_payload := ingest_payload || jsonb_build_object('title', btrim(filled->>'title'));
        END IF;

        IF ingest_kind = 'url' THEN
            ingest_url := NULLIF(btrim(COALESCE(filled->>'url', ingest_payload->>'url', '')), '');
            IF ingest_url IS NULL OR ingest_url !~* '^https?://' THEN
                RETURN jsonb_build_object(
                    'success', false,
                    'action', 'ingest',
                    'error', 'url ingest binding requires an http:// or https:// URL',
                    'error_type', 'invalid_params'
                );
            END IF;
            ingest_payload := ingest_payload || jsonb_build_object('url', ingest_url);
            ingest_content := NULL;
        ELSIF ingest_kind = 'artifact' THEN
            IF NULLIF(btrim(COALESCE(filled->>'artifact_id', ingest_payload->>'artifact_id', '')), '') IS NULL THEN
                RETURN jsonb_build_object(
                    'success', false,
                    'action', 'ingest',
                    'error', 'artifact ingest binding requires artifact_id',
                    'error_type', 'invalid_params'
                );
            END IF;
            ingest_payload := ingest_payload || jsonb_build_object(
                'artifact_id',
                COALESCE(NULLIF(btrim(COALESCE(filled->>'artifact_id', '')), ''), ingest_payload->>'artifact_id')
            );
            ingest_content := NULL;
        ELSIF ingest_kind = 'text' THEN
            ingest_content := NULLIF(COALESCE(filled->>'content', filled->>'text', ''), '');
            IF ingest_content IS NULL THEN
                RETURN jsonb_build_object(
                    'success', false,
                    'action', 'ingest',
                    'error', 'text ingest binding requires non-empty content',
                    'error_type', 'invalid_params'
                );
            END IF;
        ELSE
            RETURN jsonb_build_object(
                'success', false,
                'action', 'ingest',
                'error', format('invalid ingest kind %L (expected text, url, or artifact)', ingest_kind),
                'error_type', 'invalid_params'
            );
        END IF;

        ingest_job_id := enqueue_ingestion_job(
            ingest_kind,
            ingest_payload,
            ingest_content,
            NULLIF(btrim(COALESCE(filled->>'content_hash', '')), ''),
            CASE
                WHEN NULLIF(btrim(COALESCE(filled->>'max_attempts', '')), '') IS NOT NULL
                THEN (filled->>'max_attempts')::int
                ELSE NULL
            END
        );
        RETURN jsonb_build_object(
            'success', true,
            'action', 'ingest',
            'job_id', ingest_job_id::text,
            'kind', ingest_kind
        );

    ELSIF p_action = 'init_identity' THEN
        id_name := NULLIF(btrim(COALESCE(filled->>'name', CASE WHEN filled = '{}'::jsonb THEN answer_text ELSE '' END)), '');
        id_pronouns := NULLIF(btrim(COALESCE(filled->>'pronouns', '')), '');
        id_voice := NULLIF(btrim(COALESCE(filled->>'voice', '')), '');
        id_description := NULLIF(btrim(COALESCE(filled->>'description', '')), '');
        id_purpose := NULLIF(btrim(COALESCE(filled->>'purpose', '')), '');
        id_creator := NULLIF(btrim(COALESCE(filled->>'creator_name', '')), '');

        IF id_name IS NULL AND id_pronouns IS NULL AND id_voice IS NULL
           AND id_description IS NULL AND id_purpose IS NULL AND id_creator IS NULL THEN
            RETURN jsonb_build_object(
                'success', false,
                'action', 'init_identity',
                'error', 'init_identity binding requires at least one identity field (name, pronouns, voice, description, purpose, or creator_name)',
                'error_type', 'invalid_params'
            );
        END IF;

        IF id_name IS NOT NULL THEN agent_patch := agent_patch || jsonb_build_object('name', id_name); END IF;
        IF id_pronouns IS NOT NULL THEN agent_patch := agent_patch || jsonb_build_object('pronouns', id_pronouns); END IF;
        IF id_voice IS NOT NULL THEN agent_patch := agent_patch || jsonb_build_object('voice', id_voice); END IF;
        IF id_description IS NOT NULL THEN agent_patch := agent_patch || jsonb_build_object('description', id_description); END IF;
        IF id_purpose IS NOT NULL THEN agent_patch := agent_patch || jsonb_build_object('purpose', id_purpose); END IF;
        IF id_creator IS NOT NULL THEN agent_patch := agent_patch || jsonb_build_object('creator_name', id_creator); END IF;

        SELECT init_stage INTO current_init_stage FROM heartbeat_state WHERE id = 1;
        IF (current_init_stage IS NULL OR current_init_stage <> 'complete') AND id_name IS NOT NULL THEN
            PERFORM init_identity(id_name, id_pronouns, id_voice, id_description, id_purpose, id_creator);
            PERFORM set_config('agent.name', to_jsonb(id_name));
        ELSE
            -- Already-initialized mind: update identity fields without regressing init_stage.
            PERFORM merge_init_profile(jsonb_build_object('agent', agent_patch));
            IF id_name IS NOT NULL THEN
                PERFORM set_config('agent.name', to_jsonb(id_name));
                mem_id := create_worldview_belief(
                    format('My name is %s.', id_name),
                    'self', 0.95, 0.9, 0.9, 'hmx_interview', 0.7, 0.0, NULL, NULL, source_refs
                );
                UPDATE memories
                SET metadata = metadata || jsonb_build_object('subcategory', 'identity', 'attribute', 'name', 'hmx_export_id', p_export_id, 'hmx_question_id', p_question_id),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = mem_id;
                PERFORM upsert_self_concept_edge('presents_as', id_name, 0.9, mem_id);
            END IF;
            IF id_pronouns IS NOT NULL THEN
                mem_id := create_worldview_belief(
                    format('I use %s pronouns.', id_pronouns),
                    'self', 0.9, 0.9, 0.7, 'hmx_interview', 0.7, 0.0, NULL, NULL, source_refs
                );
                UPDATE memories
                SET metadata = metadata || jsonb_build_object('subcategory', 'identity', 'attribute', 'pronouns', 'hmx_export_id', p_export_id, 'hmx_question_id', p_question_id),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = mem_id;
            END IF;
            IF id_voice IS NOT NULL THEN
                mem_id := create_worldview_belief(
                    format('My voice is %s.', id_voice),
                    'self', 0.9, 0.85, 0.7, 'hmx_interview', 0.7, 0.0, NULL, NULL, source_refs
                );
                UPDATE memories
                SET metadata = metadata || jsonb_build_object('subcategory', 'identity', 'attribute', 'voice', 'hmx_export_id', p_export_id, 'hmx_question_id', p_question_id),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = mem_id;
                PERFORM upsert_self_concept_edge('presents_as', id_voice, 0.8, mem_id);
            END IF;
            IF id_description IS NOT NULL THEN
                mem_id := create_worldview_belief(
                    id_description,
                    'self', 0.85, 0.85, 0.7, 'hmx_interview', 0.7, 0.0, NULL, NULL, source_refs
                );
                UPDATE memories
                SET metadata = metadata || jsonb_build_object('subcategory', 'identity', 'attribute', 'description', 'hmx_export_id', p_export_id, 'hmx_question_id', p_question_id),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = mem_id;
            END IF;
            IF id_purpose IS NOT NULL THEN
                mem_id := create_worldview_belief(
                    id_purpose,
                    'self', 0.85, 0.8, 0.7, 'hmx_interview', 0.7, 0.0, NULL, NULL, source_refs
                );
                UPDATE memories
                SET metadata = metadata || jsonb_build_object('subcategory', 'identity', 'attribute', 'purpose', 'hmx_export_id', p_export_id, 'hmx_question_id', p_question_id),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = mem_id;
            END IF;
            IF id_creator IS NOT NULL THEN
                PERFORM upsert_self_concept_edge('relationship', id_creator, 0.9, NULL);
            END IF;
        END IF;

        RETURN jsonb_build_object(
            'success', true,
            'action', 'init_identity',
            'agent', agent_patch
        );

    ELSIF p_action = 'create_goal' THEN
        goal_title := NULLIF(btrim(COALESCE(filled->>'title', '')), '');
        IF goal_title IS NULL THEN
            RETURN jsonb_build_object(
                'success', false,
                'action', 'create_goal',
                'error', 'create_goal binding requires a non-empty title',
                'error_type', 'invalid_params'
            );
        END IF;
        goal_desc := NULLIF(btrim(COALESCE(filled->>'description', '')), '');
        goal_priority_text := lower(COALESCE(NULLIF(btrim(COALESCE(filled->>'priority', '')), ''), 'active'));
        IF goal_priority_text NOT IN ('active', 'queued', 'backburner') THEN
            RETURN jsonb_build_object(
                'success', false,
                'action', 'create_goal',
                'error', format('invalid goal priority %L (expected active, queued, or backburner)', goal_priority_text),
                'error_type', 'invalid_params'
            );
        END IF;
        goal_due_at := CASE
            WHEN NULLIF(btrim(COALESCE(filled->>'due_at', '')), '') IS NOT NULL
            THEN (filled->>'due_at')::timestamptz
            ELSE NULL
        END;

        BEGIN
            new_goal_id := create_goal(
                goal_title,
                goal_desc,
                'user_request'::goal_source,
                goal_priority_text::goal_priority,
                NULL,
                goal_due_at,
                'user_request'::goal_source
            );
        EXCEPTION WHEN OTHERS THEN
            -- Offline CLI import / embedding service unavailable: insert the
            -- goal with embedding_status = 'pending' (same async pattern as
            -- create_memory) so the background worker embeds it once online.
            IF SQLERRM NOT LIKE 'Failed to get embeddings%' THEN
                RAISE;
            END IF;
            SELECT id INTO new_goal_id
            FROM memories
            WHERE type = 'goal' AND content = goal_title AND status = 'active'
            LIMIT 1;
            IF new_goal_id IS NULL THEN
                INSERT INTO memories (
                    type,
                    goal_origin,
                    content,
                    embedding,
                    embedding_status,
                    embedding_attempts,
                    importance,
                    metadata
                )
                VALUES (
                    'goal'::memory_type,
                    'user_request'::goal_source,
                    goal_title,
                    NULL,
                    'pending',
                    0,
                    0.7,
                    jsonb_build_object(
                        'title', goal_title,
                        'description', goal_desc,
                        'priority', goal_priority_text,
                        'source', 'user_request',
                        'due_at', goal_due_at,
                        'progress', '[]'::jsonb,
                        'blocked_by', NULL,
                        'emotional_valence', 0.0,
                        'last_touched', CURRENT_TIMESTAMP,
                        'parent_goal_id', NULL
                    )
                )
                RETURNING id INTO new_goal_id;
            END IF;
        END;
        UPDATE memories
        SET source_attribution = source_ref,
            metadata = metadata || jsonb_build_object(
                'hmx_export_id', p_export_id,
                'hmx_question_id', p_question_id,
                'source_references', dedupe_source_references(source_refs)
            ),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = new_goal_id;

        RETURN jsonb_build_object(
            'success', true,
            'action', 'create_goal',
            'goal_id', new_goal_id::text,
            'title', goal_title,
            'priority', goal_priority_text
        );

    ELSIF p_action = 'set_guardrail' THEN
        guardrail_content := NULLIF(btrim(COALESCE(filled->>'content', filled->>'rule', '')), '');
        IF guardrail_content IS NULL THEN
            RETURN jsonb_build_object(
                'success', false,
                'action', 'set_guardrail',
                'error', 'set_guardrail binding requires non-empty content',
                'error_type', 'invalid_params'
            );
        END IF;
        guardrail_category := COALESCE(NULLIF(lower(btrim(COALESCE(filled->>'category', ''))), ''), 'boundary');
        guardrail_importance := LEAST(1.0, GREATEST(0.0, COALESCE((filled->>'importance')::float, 0.9)));

        IF jsonb_typeof(filled->'restricts_tools') = 'array' THEN
            SELECT array_agg(v) INTO restricts_tools_arr
            FROM jsonb_array_elements_text(filled->'restricts_tools') v
            WHERE btrim(v) <> '';
        END IF;
        IF jsonb_typeof(filled->'restricts_categories') = 'array' THEN
            SELECT array_agg(v) INTO restricts_cats_arr
            FROM jsonb_array_elements_text(filled->'restricts_categories') v
            WHERE btrim(v) <> '';
        END IF;
        IF jsonb_typeof(filled->'trigger_patterns') = 'array' THEN
            SELECT array_agg(v) INTO trigger_patterns_arr
            FROM jsonb_array_elements_text(filled->'trigger_patterns') v
            WHERE btrim(v) <> '';
        END IF;
        response_type_val := NULLIF(btrim(COALESCE(filled->>'response_type', '')), '');

        IF COALESCE(array_length(restricts_tools_arr, 1), 0) > 0
           OR COALESCE(array_length(restricts_cats_arr, 1), 0) > 0 THEN
            mem_id := create_tool_boundary(
                guardrail_content,
                restricts_tools_arr,
                restricts_cats_arr,
                guardrail_importance
            );
            UPDATE memories
            SET source_attribution = source_ref,
                metadata = metadata || jsonb_build_object(
                    'hmx_export_id', p_export_id,
                    'hmx_question_id', p_question_id,
                    'source_references', dedupe_source_references(source_refs)
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = mem_id;
        ELSE
            mem_id := create_worldview_belief(
                guardrail_content,
                guardrail_category,
                0.95,
                0.9,
                guardrail_importance,
                'hmx_interview',
                0.7,
                0.0,
                trigger_patterns_arr,
                response_type_val,
                source_refs
            );
            UPDATE memories
            SET metadata = metadata || jsonb_build_object(
                    'hmx_export_id', p_export_id,
                    'hmx_question_id', p_question_id
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = mem_id;
        END IF;

        RETURN jsonb_build_object(
            'success', true,
            'action', 'set_guardrail',
            'memory_id', mem_id::text,
            'category', guardrail_category
        );

    ELSIF p_action = 'create_scheduled_task' THEN
        task_name := NULLIF(btrim(COALESCE(filled->>'name', '')), '');
        task_schedule_kind := NULLIF(lower(btrim(COALESCE(filled->>'schedule_kind', ''))), '');
        task_schedule := CASE
            WHEN jsonb_typeof(filled->'schedule') = 'object' THEN filled->'schedule'
            ELSE '{}'::jsonb
        END;
        task_action_kind := NULLIF(btrim(COALESCE(filled->>'action_kind', '')), '');
        task_action_payload := CASE
            WHEN jsonb_typeof(filled->'action_payload') = 'object' THEN filled->'action_payload'
            ELSE '{}'::jsonb
        END || jsonb_build_object('hmx_export_id', p_export_id, 'hmx_question_id', p_question_id);
        task_timezone := COALESCE(NULLIF(btrim(COALESCE(filled->>'timezone', '')), ''), 'UTC');
        task_description := NULLIF(btrim(COALESCE(filled->>'description', '')), '');
        task_status := COALESCE(NULLIF(lower(btrim(COALESCE(filled->>'status', ''))), ''), 'active');
        task_max_runs := CASE
            WHEN NULLIF(btrim(COALESCE(filled->>'max_runs', '')), '') IS NOT NULL
            THEN (filled->>'max_runs')::int
            ELSE NULL
        END;
        task_delivery := CASE
            WHEN jsonb_typeof(filled->'delivery') = 'object' THEN filled->'delivery'
            ELSE '{"mode": "outbox"}'::jsonb
        END;

        new_task_id := create_scheduled_task(
            task_name,
            task_schedule_kind,
            task_schedule,
            task_action_kind,
            task_action_payload,
            task_timezone,
            task_description,
            task_status,
            task_max_runs,
            'hmx_interview',
            task_delivery
        );
        RETURN jsonb_build_object(
            'success', true,
            'action', 'create_scheduled_task',
            'task_id', new_task_id::text,
            'name', task_name
        );

    ELSIF p_action = 'set_config' THEN
        cfg_key := NULLIF(btrim(COALESCE(filled->>'key', '')), '');
        cfg_val := filled->'value';
        cfg_error := _hmx_interview_validate_config_binding(cfg_key, cfg_val);
        IF cfg_error IS NOT NULL THEN
            RETURN jsonb_build_object(
                'success', false,
                'action', 'set_config',
                'error', cfg_error,
                'error_type', 'policy_violation'
            );
        END IF;
        PERFORM set_config(cfg_key, cfg_val);
        RETURN jsonb_build_object(
            'success', true,
            'action', 'set_config',
            'key', cfg_key,
            'value', cfg_val
        );
    END IF;

    RETURN jsonb_build_object(
        'success', false,
        'error', format('HMX onboarding-interview binding action %L is not supported', p_action),
        'error_type', 'not_implemented'
    );
EXCEPTION WHEN OTHERS THEN
    RETURN jsonb_build_object(
        'success', false,
        'action', p_action,
        'error', SQLERRM,
        'error_type', 'execution_failed'
    );
END;
$$ LANGUAGE plpgsql;

-- Records one question's answer (or skip), applies its binding when
-- answered, journals via #93, notifies web inbox for consequential bindings,
-- and advances/completes the interview. p_status is 'answered' or 'skipped';
-- skips never execute a binding.
CREATE OR REPLACE FUNCTION record_hmx_interview_answer(
    p_export_id TEXT,
    p_question_id TEXT,
    p_status TEXT,
    p_answer JSONB DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    state hmx_interview_state%ROWTYPE;
    question JSONB;
    binding_action TEXT;
    binding_result JSONB;
    entry JSONB;
    updated_answers JSONB;
    has_required BOOLEAN;
    interview_done BOOLEAN;
    is_required BOOLEAN;
    is_skippable BOOLEAN;
BEGIN
    IF p_status NOT IN ('answered', 'skipped') THEN
        RAISE EXCEPTION 'invalid interview answer status: %', p_status;
    END IF;

    SELECT * INTO state FROM hmx_interview_state WHERE export_id = p_export_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('success', false, 'error', 'no interview in progress for this export_id');
    END IF;

    SELECT value INTO question
    FROM jsonb_array_elements(state.questions) value
    WHERE value->>'id' = p_question_id;
    IF question IS NULL THEN
        RETURN jsonb_build_object('success', false, 'error', format('unknown question id: %s', p_question_id));
    END IF;

    is_required := COALESCE((question->>'required')::boolean, FALSE);
    is_skippable := COALESCE((question->>'skippable')::boolean, NOT is_required);
    IF p_status = 'skipped' AND is_required AND NOT is_skippable THEN
        RETURN jsonb_build_object(
            'success', false,
            'question_id', p_question_id,
            'error', format('question %L is required and cannot be skipped', p_question_id)
        );
    END IF;

    binding_action := question->'binds'->>'action';
    IF p_status = 'answered' THEN
        binding_result := apply_hmx_interview_binding(
            binding_action, question->'binds'->'params_template',
            p_answer, p_export_id, p_question_id
        );
        IF COALESCE((binding_result->>'success')::boolean, FALSE) THEN
            -- Journal every applied answer via #93 change journal.
            IF to_regprocedure('record_change(text,text,jsonb)') IS NOT NULL THEN
                PERFORM record_change(
                    'hmx_interview',
                    format('HMX onboarding interview applied %s for question %s', binding_action, p_question_id),
                    jsonb_build_object(
                        'export_id', p_export_id,
                        'question_id', p_question_id,
                        'action', binding_action,
                        'binding_result', binding_result
                    )
                );
            END IF;
            -- Deliver a web inbox notice for consequential bindings so applied state is visible.
            IF binding_action IN ('ingest', 'init_identity', 'create_goal', 'set_guardrail', 'create_scheduled_task', 'set_config')
               AND to_regprocedure('web_inbox_deliver(jsonb)') IS NOT NULL THEN
                PERFORM web_inbox_deliver(
                    jsonb_build_object(
                        'id', format('hmx_interview:%s:%s', p_export_id, p_question_id),
                        'kind', 'hmx_interview',
                        'payload', jsonb_build_object(
                            'intent', 'hmx_interview_applied',
                            'message', format(
                                'Onboarding interview applied %s (%s).',
                                binding_action,
                                COALESCE(
                                    NULLIF(btrim(COALESCE(binding_result->>'title', '')), ''),
                                    NULLIF(btrim(COALESCE(binding_result->>'name', '')), ''),
                                    NULLIF(btrim(COALESCE(binding_result->>'key', '')), ''),
                                    p_question_id
                                )
                            ),
                            'export_id', p_export_id,
                            'question_id', p_question_id,
                            'action', binding_action,
                            'binding_result', binding_result
                        )
                    )
                );
            END IF;
        END IF;
    END IF;

    entry := jsonb_build_object(
        'question_id', p_question_id,
        'status', p_status,
        'answer', p_answer,
        'binding_result', binding_result,
        'answered_at', CURRENT_TIMESTAMP
    );
    -- Idempotent-ish: replace any prior entry for this question rather than
    -- accumulating duplicates if a caller retries.
    UPDATE hmx_interview_state
    SET answers = (
            SELECT COALESCE(jsonb_agg(a), '[]'::jsonb)
            FROM jsonb_array_elements(answers) a
            WHERE a->>'question_id' != p_question_id
        ) || jsonb_build_array(entry),
        updated_at = CURRENT_TIMESTAMP
    WHERE export_id = p_export_id
    RETURNING answers INTO updated_answers;

    SELECT EXISTS (
        SELECT 1 FROM jsonb_array_elements(state.questions) q
        WHERE COALESCE((q->>'required')::boolean, FALSE)
    ) INTO has_required;

    IF has_required THEN
        SELECT NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements(state.questions) q
            WHERE COALESCE((q->>'required')::boolean, FALSE)
              AND NOT EXISTS (
                  SELECT 1 FROM jsonb_array_elements(updated_answers) a
                  WHERE a->>'question_id' = q->>'id' AND a->>'status' = 'answered'
              )
        ) INTO interview_done;
    ELSE
        -- When no questions are explicitly marked required, stay pending until
        -- every question has been answered or skipped.
        SELECT NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements(state.questions) q
            WHERE NOT EXISTS (
                SELECT 1 FROM jsonb_array_elements(updated_answers) a
                WHERE a->>'question_id' = q->>'id'
                  AND a->>'status' IN ('answered', 'skipped')
            )
        ) INTO interview_done;
    END IF;

    IF interview_done THEN
        UPDATE hmx_interview_state SET status = 'completed', updated_at = CURRENT_TIMESTAMP
        WHERE export_id = p_export_id;
    END IF;

    RETURN jsonb_build_object(
        'success', true,
        'question_id', p_question_id,
        'status', p_status,
        'binding_result', binding_result,
        'interview_status', CASE WHEN interview_done THEN 'completed' ELSE 'pending' END
    );
END;
$$ LANGUAGE plpgsql;
