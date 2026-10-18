-- 0257: The heartbeat yields to chat, acts only on what it has seen, knows
-- its step budget, gets the person's shell permission, and works on the
-- person's goals without spending energy.
--
-- - Goals created on the Goals page or with the CLI are the person's
--   (goal_origin = user_request); goals already recorded with source
--   user_request are taken as theirs. While one is active the heartbeat may
--   take rlm.heartbeat.user_goal_max_iterations steps and
--   user_goal_timeout_seconds, and tool calls serving it (work_on_goal) cost
--   no energy until heartbeat.free_work_daily_token_cap.
-- - A heartbeat action missing its text (synthesize or reach_out_public
--   without params.content, reach_out_user without params.message) fails
--   alone instead of rolling back every action in the heartbeat.
-- - The heartbeat prompt lists the params the executor reads and the
--   work_on_goal policy.
-- Idempotent.
SET search_path = public, ag_catalog;

INSERT INTO config_defaults (key, value, description) VALUES
    ('rlm.heartbeat.user_goal_max_iterations', '40'::jsonb, 'Reasoning steps per heartbeat while a goal the user set is active'),
    ('rlm.heartbeat.user_goal_timeout_seconds', '1800'::jsonb, 'Seconds per heartbeat while a goal the user set is active'),
    ('heartbeat.free_work_daily_token_cap', '5000000'::jsonb, 'Heartbeat model tokens per day after which work on the user''s goals spends energy again (0 = no cap)')
ON CONFLICT (key) DO NOTHING;

-- Existing goals: the Goals page recorded the source the person picked; only
-- "User request" goals are taken as theirs.
UPDATE memories
SET goal_origin = 'user_request'::goal_source, updated_at = CURRENT_TIMESTAMP
WHERE type = 'goal'
  AND metadata->>'source' = 'user_request'
  AND goal_origin IS DISTINCT FROM 'user_request'::goal_source;

CREATE OR REPLACE FUNCTION has_active_user_goal() RETURNS BOOLEAN
    LANGUAGE sql STABLE AS $$
    SELECT EXISTS (
        SELECT 1 FROM memories
        WHERE type = 'goal' AND status = 'active'
          AND metadata->>'priority' = 'active'
          AND goal_origin = 'user_request'::goal_source);
$$;

CREATE OR REPLACE FUNCTION free_goal_work_status() RETURNS JSONB
    LANGUAGE plpgsql STABLE AS $$
DECLARE
    cap_tokens BIGINT := COALESCE(get_config_int('heartbeat.free_work_daily_token_cap'), 0);
    used_tokens BIGINT;
BEGIN
    SELECT COALESCE(sum(total_tokens), 0)::bigint INTO used_tokens FROM api_usage
    WHERE source IN ('heartbeat', 'rlm') AND created_at >= date_trunc('day', CURRENT_TIMESTAMP);
    RETURN jsonb_build_object(
        'free', cap_tokens = 0 OR used_tokens < cap_tokens,
        'tokens_today', used_tokens,
        'daily_cap', cap_tokens);
END;
$$;


-- Heartbeat actions missing their text fail alone.
CREATE OR REPLACE FUNCTION execute_heartbeat_action(
    p_heartbeat_id UUID,
    p_action TEXT,
    p_params JSONB DEFAULT '{}'
)
RETURNS JSONB AS $$
DECLARE
    action_kind heartbeat_action;
    action_cost FLOAT;
    current_e FLOAT;
    result JSONB;
    queued_call JSONB;
    external_calls JSONB := '[]'::jsonb;
    outbox_messages JSONB := '[]'::jsonb;
    allowed_actions JSONB;
    is_allowed BOOLEAN;
    remembered_id UUID;
    boundary_hits JSONB;
    boundary_content TEXT;
    rel_entity TEXT;
    rel_strength FLOAT;
    rel_evidence UUID;
    belief_id UUID;
    evidence_id UUID;
    action_notes TEXT;
    action_topic TEXT;
    chapter_name TEXT;
    chapter_summary TEXT;
    chapter_next TEXT;
    tp_memory_id UUID;
    v_review_id UUID;
    v_review_ids UUID[];
    contra_a UUID;
    contra_b UUID;
    resolution_text TEXT;
    identity_updated BOOLEAN;
    pause_reason TEXT;
    outbound_purpose JSONB;
    v_last_user_contact TIMESTAMPTZ;
    v_cooldown_hours FLOAT;
BEGIN
    BEGIN
        action_kind := p_action::heartbeat_action;
    EXCEPTION
        WHEN invalid_text_representation THEN
            RETURN jsonb_build_object('success', false, 'error', 'Unknown action: ' || COALESCE(p_action, '<null>'));
    END;

    allowed_actions := get_config('heartbeat.allowed_actions');
    IF jsonb_typeof(allowed_actions) = 'array' THEN
        SELECT EXISTS (
            SELECT 1 FROM jsonb_array_elements_text(allowed_actions) a WHERE a = p_action
        ) INTO is_allowed;
        IF NOT is_allowed THEN
            RETURN jsonb_build_object(
                'success', false,
                'error', 'Action not allowed',
                'action', p_action
            );
        END IF;
    END IF;

    -- An action missing its text fails alone instead of reaching a NOT NULL
    -- insert and rolling back every action in the heartbeat.
    IF p_action IN ('synthesize', 'reach_out_public')
       AND NULLIF(btrim(COALESCE(p_params->>'content', '')), '') IS NULL THEN
        RETURN jsonb_build_object('success', false, 'error', p_action || ' requires params.content', 'action', p_action);
    END IF;
    IF p_action = 'reach_out_user'
       AND NULLIF(btrim(COALESCE(p_params->>'message', '')), '') IS NULL THEN
        RETURN jsonb_build_object('success', false, 'error', 'reach_out_user requires params.message', 'action', p_action);
    END IF;

    IF p_action IN ('reach_out_user', 'reach_out_public') THEN
        IF p_action = 'reach_out_public'
           AND (
               NULLIF(btrim(COALESCE(p_params->>'platform', '')), '') IS NULL
               OR NULLIF(btrim(COALESCE(p_params->>'target_id', '')), '') IS NULL
           ) THEN
            RETURN jsonb_build_object(
                'success', false,
                'error', 'reach_out_public requires an exact platform and public target_id; it cannot fall back to the user''s last-active private channel.',
                'next_step', 'Choose a configured public channel and provide its exact room/channel target_id, or use the provider''s declared outbound tool.'
            );
        END IF;
        EXECUTE 'SELECT verify_outbound_purpose($1, $2, $3, NULL, $4::jsonb)'
        INTO outbound_purpose
        USING
            p_params->>'purpose_kind',
            p_params->>'purpose_reference',
            p_action = 'reach_out_user',
            jsonb_build_object(
                'tool_context', 'heartbeat',
                'heartbeat_id', p_heartbeat_id
            );
        IF NOT COALESCE((outbound_purpose->>'verified')::boolean, FALSE) THEN
            RETURN jsonb_build_object(
                'success', false,
                'error', 'Outbound communication requires a backed purpose_kind and purpose_reference.',
                'purpose', outbound_purpose,
                'next_step', CASE p_action
                    WHEN 'reach_out_user' THEN 'Use connection with a recorded reference, or cite a goal, responsibility, reply, or user request.'
                    ELSE 'Cite an existing goal, responsibility, reply thread, or explicit user request.'
                END
            );
        END IF;

        -- Cooldown on unsolicited contact (#112): heartbeat.user_contact_cooldown_hours
        -- was seeded and documented but never enforced. A reply to something the
        -- user just sent, or a purpose already flagged urgent (an assigned goal,
        -- an urgent responsibility), is not "unsolicited" and skips the cooldown.
        IF p_action = 'reach_out_user'
           AND COALESCE(outbound_purpose->>'kind', '') <> 'reply'
           AND NOT COALESCE((outbound_purpose->>'urgent_backed')::boolean, FALSE) THEN
            SELECT last_user_contact INTO v_last_user_contact FROM heartbeat_state WHERE id = 1;
            v_cooldown_hours := GREATEST(COALESCE(get_config_float('heartbeat.user_contact_cooldown_hours'), 4.0), 0.0);
            IF v_last_user_contact IS NOT NULL
               AND v_last_user_contact > CURRENT_TIMESTAMP - (v_cooldown_hours * INTERVAL '1 hour') THEN
                RETURN jsonb_build_object(
                    'success', false,
                    'error', 'User contact cooldown active',
                    'last_user_contact', v_last_user_contact,
                    'cooldown_hours', v_cooldown_hours,
                    'next_step', format(
                        'Wait until %s hours have passed since the last user contact (%s), or use a reply/urgent purpose instead.',
                        v_cooldown_hours, v_last_user_contact
                    )
                );
            END IF;
        END IF;
    END IF;

    action_cost := get_action_cost(p_action);
    IF COALESCE((outbound_purpose->>'assigned_goal')::boolean, FALSE) THEN
        action_cost := action_cost * LEAST(
            GREATEST(
                COALESCE(get_config_float('outbound.assigned_goal_energy_multiplier'), 0.25),
                0
            ),
            1
        );
    END IF;
    current_e := get_current_energy();

    IF current_e < action_cost THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'Insufficient energy',
            'required', action_cost,
            'available', current_e
        );
    END IF;
    IF p_action IN ('reach_out_public', 'synthesize') THEN
        boundary_content := COALESCE(p_params->>'content', '');
        SELECT COALESCE(jsonb_agg(row_to_json(r)), '[]'::jsonb)
        INTO boundary_hits
        FROM check_boundaries(boundary_content) r;

        IF boundary_hits IS NOT NULL AND jsonb_array_length(boundary_hits) > 0 THEN
            IF EXISTS (
                SELECT 1
                FROM jsonb_array_elements(boundary_hits) e
                WHERE e->>'response_type' = 'refuse'
            ) THEN
                RETURN jsonb_build_object(
                    'success', false,
                    'error', 'Boundary triggered',
                    'boundaries', boundary_hits
                );
            END IF;
        END IF;
    END IF;

    PERFORM update_energy(-action_cost);

    CASE p_action
        WHEN 'observe' THEN
            result := jsonb_build_object('environment', get_environment_snapshot());

        WHEN 'review_goals' THEN
            result := jsonb_build_object('goals', get_goals_snapshot());

        WHEN 'remember' THEN
            remembered_id := create_episodic_memory(
                p_content := COALESCE(p_params->>'content', ''),
                p_context := COALESCE(p_params, '{}'::jsonb) || jsonb_build_object('heartbeat_id', p_heartbeat_id),
                p_emotional_valence := COALESCE((p_params->>'emotional_valence')::float, 0),
                p_importance := COALESCE((p_params->>'importance')::float, 0.4)
            );
            result := jsonb_build_object('memory_id', remembered_id);

        WHEN 'recall' THEN
            SELECT jsonb_agg(row_to_json(r)) INTO result
            FROM fast_recall(p_params->>'query', COALESCE((p_params->>'limit')::int, 5)) r;
            result := jsonb_build_object('memories', COALESCE(result, '[]'::jsonb));
            PERFORM satisfy_drive('curiosity', 0.2);

        WHEN 'connect' THEN
            PERFORM create_memory_relationship(
                (p_params->>'from_id')::UUID,
                (p_params->>'to_id')::UUID,
                (p_params->>'relationship_type')::graph_edge_type,
                COALESCE(p_params->'properties', '{}'::jsonb)
            );
            result := jsonb_build_object('connected', true);
            PERFORM satisfy_drive('coherence', 0.1);

        WHEN 'reprioritize' THEN
            PERFORM change_goal_priority(
                (p_params->>'goal_id')::UUID,
                (p_params->>'new_priority')::goal_priority,
                p_params->>'reason'
            );
            IF (p_params->>'new_priority') = 'completed' THEN
                PERFORM satisfy_drive('competence', 0.4);
            END IF;
            result := jsonb_build_object('reprioritized', true);

        WHEN 'reflect' THEN
            queued_call := build_external_call(
                'think',
                jsonb_build_object(
                    'kind', 'reflect',
                    'recent_memories', get_recent_context(20),
                    'identity', get_identity_context(),
                    'worldview', get_worldview_context(),
                    'contradictions', (
                        SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb)
                        FROM (SELECT * FROM find_contradictions(NULL) LIMIT 5) t
                    ),
                    'goals', get_goals_snapshot(),
                    'heartbeat_id', p_heartbeat_id,
                    'instructions', 'Analyze patterns. Note contradictions. Suggest identity updates. Discover relationships between memories.'
                )
            );
            external_calls := external_calls || jsonb_build_array(queued_call);
            result := jsonb_build_object('queued', true, 'external_call', queued_call);
            PERFORM satisfy_drive('coherence', 0.2);

        WHEN 'contemplate', 'meditate', 'study', 'debate_internally' THEN
            BEGIN
                belief_id := NULLIF(p_params->>'belief_id', '')::uuid;
            EXCEPTION
                WHEN OTHERS THEN
                    belief_id := NULL;
            END;
            BEGIN
                evidence_id := NULLIF(p_params->>'evidence_memory_id', '')::uuid;
            EXCEPTION
                WHEN OTHERS THEN
                    evidence_id := NULL;
            END;

            action_notes := COALESCE(p_params->>'notes', '');
            action_topic := COALESCE(
                NULLIF(p_params->>'topic', ''),
                NULLIF(p_params->>'belief', ''),
                NULLIF(p_params->>'subject', ''),
                'belief'
            );

            IF belief_id IS NOT NULL THEN
                PERFORM record_transformation_effort(
                    belief_id,
                    p_action,
                    action_notes,
                    evidence_id
                );
            END IF;

            PERFORM create_episodic_memory(
                p_content := format('%s: %s', initcap(replace(p_action, '_', ' ')), action_topic),
                p_action_taken := jsonb_build_object(
                    'action', p_action,
                    'belief_id', belief_id,
                    'notes', action_notes
                ),
                p_context := COALESCE(p_params, '{}'::jsonb) || jsonb_build_object('heartbeat_id', p_heartbeat_id),
                p_result := jsonb_build_object('belief_id', belief_id),
                p_emotional_valence := COALESCE((p_params->>'emotional_valence')::float, 0.1),
                p_importance := COALESCE((p_params->>'importance')::float, 0.4)
            );

            result := jsonb_build_object('logged', true, 'belief_id', belief_id);
            PERFORM satisfy_drive('coherence', 0.1);

        WHEN 'maintain' THEN
            identity_updated := NULL;
            IF (p_params ? 'identity_belief_id') AND (p_params ? 'new_content') THEN
                identity_updated := update_identity_belief(
                    (p_params->>'identity_belief_id')::uuid,
                    p_params->>'new_content',
                    NULLIF(p_params->>'evidence_memory_id', '')::uuid,
                    COALESCE(NULLIF(p_params->>'force', '')::boolean, FALSE)
                );
            ELSIF p_params ? 'worldview_id' THEN
                UPDATE memories
                SET metadata = jsonb_set(
                        metadata,
                        '{confidence}',
                        to_jsonb(COALESCE((p_params->>'new_confidence')::float, (metadata->>'confidence')::float))
                    ),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = (p_params->>'worldview_id')::UUID AND type = 'worldview';
            END IF;
            result := jsonb_build_object('maintained', true, 'identity_updated', identity_updated);
            PERFORM satisfy_drive('coherence', 0.1);

        WHEN 'mark_turning_point' THEN
            tp_memory_id := NULLIF(p_params->>'memory_id', '')::uuid;
            chapter_summary := COALESCE(p_params->>'summary', p_params->>'reason', '');

            IF tp_memory_id IS NOT NULL THEN
                UPDATE memories
                SET importance = GREATEST(importance, 0.9),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = tp_memory_id;
            END IF;

            PERFORM create_strategic_memory(
                p_content := COALESCE(NULLIF(chapter_summary, ''), 'Turning point noted'),
                p_pattern_description := 'Narrative turning point',
                p_confidence_score := 0.85,
                p_supporting_evidence := jsonb_build_object(
                    'memory_id', tp_memory_id,
                    'summary', chapter_summary,
                    'heartbeat_id', p_heartbeat_id
                ),
                p_importance := 0.6
            );
            result := jsonb_build_object('marked', true, 'memory_id', tp_memory_id);

        WHEN 'begin_chapter' THEN
            chapter_name := COALESCE(
                NULLIF(p_params->>'name', ''),
                NULLIF(p_params->>'chapter_name', ''),
                NULLIF(p_params->>'title', ''),
                'Foundations'
            );
            PERFORM ensure_current_life_chapter(chapter_name);
            result := jsonb_build_object('started', true, 'chapter', chapter_name);

        WHEN 'close_chapter' THEN
            chapter_summary := COALESCE(p_params->>'summary', '');
            chapter_next := NULLIF(p_params->>'next_chapter', '');
            PERFORM create_strategic_memory(
                p_content := COALESCE(NULLIF(chapter_summary, ''), 'Chapter closed'),
                p_pattern_description := 'Chapter closure',
                p_confidence_score := 0.8,
                p_supporting_evidence := jsonb_build_object(
                    'summary', chapter_summary,
                    'previous_chapter', get_narrative_context(),
                    'heartbeat_id', p_heartbeat_id
                ),
                p_importance := 0.6
            );
            IF chapter_next IS NOT NULL THEN
                PERFORM ensure_current_life_chapter(chapter_next);
            END IF;
            result := jsonb_build_object('closed', true, 'next_chapter', chapter_next);

        WHEN 'acknowledge_relationship' THEN
            rel_entity := COALESCE(NULLIF(p_params->>'entity', ''), NULLIF(p_params->>'name', ''));
            rel_strength := COALESCE(NULLIF(p_params->>'strength', '')::float, 0.6);
            rel_evidence := NULLIF(p_params->>'evidence_memory_id', '')::uuid;
            IF rel_entity IS NOT NULL THEN
                PERFORM upsert_self_concept_edge('relationship', rel_entity, rel_strength, rel_evidence);
            END IF;
            result := jsonb_build_object('acknowledged', true, 'entity', rel_entity);

        WHEN 'update_trust' THEN
            rel_entity := COALESCE(NULLIF(p_params->>'entity', ''), NULLIF(p_params->>'name', ''));
            rel_strength := COALESCE(
                NULLIF(p_params->>'strength', '')::float,
                NULLIF(p_params->>'delta', '')::float,
                0.6
            );
            rel_evidence := NULLIF(p_params->>'evidence_memory_id', '')::uuid;
            IF rel_entity IS NOT NULL THEN
                PERFORM upsert_self_concept_edge('relationship', rel_entity, rel_strength, rel_evidence);
            END IF;
            result := jsonb_build_object('updated', true, 'entity', rel_entity, 'strength', rel_strength);

        WHEN 'reflect_on_relationship' THEN
            rel_entity := COALESCE(NULLIF(p_params->>'entity', ''), NULLIF(p_params->>'name', ''));
            queued_call := build_external_call(
                'think',
                jsonb_build_object(
                    'kind', 'reflect',
                    'heartbeat_id', p_heartbeat_id,
                    'context', gather_turn_context(),
                    'params', jsonb_build_object('relationship', rel_entity)
                )
            );
            external_calls := external_calls || jsonb_build_array(queued_call);
            result := jsonb_build_object('queued', true, 'external_call', queued_call, 'entity', rel_entity);

        WHEN 'resolve_contradiction' THEN
            contra_a := NULLIF(p_params->>'memory_a', '')::uuid;
            contra_b := NULLIF(p_params->>'memory_b', '')::uuid;
            resolution_text := COALESCE(p_params->>'resolution', '');

            PERFORM create_strategic_memory(
                p_content := COALESCE(NULLIF(resolution_text, ''), 'Contradiction resolved'),
                p_pattern_description := 'Contradiction resolved',
                p_confidence_score := 0.8,
                p_supporting_evidence := jsonb_build_object(
                    'memory_a', contra_a,
                    'memory_b', contra_b,
                    'resolution', resolution_text,
                    'heartbeat_id', p_heartbeat_id
                ),
                p_importance := 0.6
            );

            BEGIN
                IF contra_a IS NOT NULL AND contra_b IS NOT NULL THEN
                    EXECUTE format(
                        'SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$ 
                            MATCH (a:MemoryNode {memory_id: %L})-[r:CONTRADICTS]-(b:MemoryNode {memory_id: %L})
                            DELETE r
                            RETURN a
                        $q$) as (result ag_catalog.agtype)',
                        contra_a,
                        contra_b
                    );
                    -- Mirror the resolved-contradiction deletion to memory_edges
                    -- (undirected, so clear both stored directions).
                    PERFORM delete_memory_edge('memory', contra_a::text, 'CONTRADICTS', 'memory', contra_b::text);
                    PERFORM delete_memory_edge('memory', contra_b::text, 'CONTRADICTS', 'memory', contra_a::text);
                END IF;
            EXCEPTION WHEN OTHERS THEN NULL;
            END;

            result := jsonb_build_object('resolved', true, 'memory_a', contra_a, 'memory_b', contra_b);

        WHEN 'accept_tension' THEN
            contra_a := NULLIF(p_params->>'memory_a', '')::uuid;
            contra_b := NULLIF(p_params->>'memory_b', '')::uuid;
            resolution_text := COALESCE(p_params->>'note', p_params->>'resolution', '');
            PERFORM create_strategic_memory(
                p_content := COALESCE(NULLIF(resolution_text, ''), 'Contradiction acknowledged'),
                p_pattern_description := 'Contradiction accepted',
                p_confidence_score := 0.7,
                p_supporting_evidence := jsonb_build_object(
                    'memory_a', contra_a,
                    'memory_b', contra_b,
                    'note', resolution_text,
                    'heartbeat_id', p_heartbeat_id
                ),
                p_importance := 0.5
            );
            result := jsonb_build_object('accepted', true, 'memory_a', contra_a, 'memory_b', contra_b);

        WHEN 'brainstorm_goals' THEN
            queued_call := build_external_call(
                'think',
                jsonb_build_object(
                    'kind', 'brainstorm_goals',
                    'heartbeat_id', p_heartbeat_id,
                    'context', gather_turn_context(),
                    'params', COALESCE(p_params, '{}'::jsonb)
                )
            );
            external_calls := external_calls || jsonb_build_array(queued_call);
            result := jsonb_build_object('queued', true, 'external_call', queued_call);

        WHEN 'inquire_shallow', 'inquire_deep' THEN
            queued_call := build_external_call(
                'think',
                jsonb_build_object(
                    'kind', 'inquire',
                    'depth', p_action,
                    'heartbeat_id', p_heartbeat_id,
                    'query', COALESCE(p_params->>'query', p_params->>'question'),
                    'context', gather_turn_context(),
                    'params', COALESCE(p_params, '{}'::jsonb)
                )
            );
            external_calls := external_calls || jsonb_build_array(queued_call);
            result := jsonb_build_object('queued', true, 'external_call', queued_call);
            PERFORM satisfy_drive('curiosity', 0.2);

        WHEN 'synthesize' THEN
            DECLARE synth_id UUID;
            BEGIN
                synth_id := create_semantic_memory(
                    p_params->>'content',
                    COALESCE((p_params->>'confidence')::float, 0.8),
                    ARRAY['synthesis', COALESCE(p_params->>'topic', 'general')],
                    NULL,
                    jsonb_build_object('heartbeat_id', p_heartbeat_id, 'sources', p_params->'sources', 'boundaries', boundary_hits),
                    0.7
                );
                result := jsonb_build_object('synthesis_memory_id', synth_id, 'boundaries', boundary_hits);
            END;

        WHEN 'reach_out_user' THEN
            queued_call := build_outbox_message(
                'user',
                jsonb_build_object(
                    'message', p_params->>'message',
                    'intent', p_params->>'intent',
                    'heartbeat_id', p_heartbeat_id,
                    'purpose_kind', p_params->>'purpose_kind',
                    'purpose_reference', p_params->>'purpose_reference',
                    'urgency', COALESCE(p_params->>'urgency', 'normal')
                )
            );
            outbox_messages := outbox_messages || jsonb_build_array(queued_call);
            result := jsonb_build_object('queued', true, 'outbox_message', queued_call);
            PERFORM satisfy_drive('connection', 0.3);

        WHEN 'reach_out_public' THEN
            queued_call := build_outbox_message(
                'public',
                jsonb_build_object(
                    'platform', p_params->>'platform',
                    'content', p_params->>'content',
                    'heartbeat_id', p_heartbeat_id,
                    'boundaries', boundary_hits,
                    'purpose_kind', p_params->>'purpose_kind',
                    'purpose_reference', p_params->>'purpose_reference',
                    'urgency', COALESCE(p_params->>'urgency', 'normal'),
                    'target_id', p_params->>'target_id',
                    'target_channel', p_params->>'platform',
                    'delivery_mode', 'direct'
                )
            );
            outbox_messages := outbox_messages || jsonb_build_array(queued_call);
            result := jsonb_build_object('queued', true, 'outbox_message', queued_call, 'boundaries', boundary_hits);
            PERFORM satisfy_drive('connection', 0.3);

        WHEN 'pause_heartbeat' THEN
            pause_reason := COALESCE(
                NULLIF(p_params->>'reason', ''),
                NULLIF(p_params->>'details', ''),
                NULLIF(p_params->>'message', '')
            );
            IF pause_reason IS NULL THEN
                RETURN jsonb_build_object('success', false, 'error', 'pause_heartbeat requires a reason');
            END IF;
            result := pause_heartbeat(pause_reason, p_params, p_heartbeat_id);
            outbox_messages := outbox_messages || COALESCE(result->'outbox_messages', '[]'::jsonb);

        WHEN 'terminate' THEN
            IF COALESCE(p_params->'confirmed', 'false'::jsonb) = 'true'::jsonb THEN
                result := terminate_agent(
                    COALESCE(NULLIF(p_params->>'last_will', ''), NULLIF(p_params->>'message', ''), NULLIF(p_params->>'reason', ''), ''),
                    COALESCE(p_params->'farewells', '[]'::jsonb),
                    COALESCE(p_params->'options', '{}'::jsonb)
                );
                outbox_messages := outbox_messages || COALESCE(result->'outbox_messages', '[]'::jsonb);
            ELSE
                queued_call := build_external_call(
                    'think',
                    jsonb_build_object(
                        'kind', 'termination_confirm',
                        'heartbeat_id', p_heartbeat_id,
                        'context', gather_turn_context(),
                        'params', COALESCE(p_params, '{}'::jsonb)
                    )
                );
                external_calls := external_calls || jsonb_build_array(queued_call);
                result := jsonb_build_object('confirmation_required', true, 'external_call', queued_call);
            END IF;

        WHEN 'rest' THEN
            result := jsonb_build_object('rested', true, 'energy_preserved', current_e - action_cost);
            PERFORM satisfy_drive('rest', 0.4);

        -- Memory retention: the conscious mind's verdict on a memory at the threshold
        -- of fading (surfaced via context.memories_at_threshold).
        WHEN 'keep_memory' THEN
            v_review_id := NULLIF(p_params->>'review_id', '')::uuid;
            SELECT memory_ids INTO v_review_ids
              FROM memory_review_queue WHERE id = v_review_id AND status = 'pending';
            IF v_review_ids IS NULL THEN
                result := jsonb_build_object('kept', false, 'reason', 'not_found');
            ELSIF NOT spend_retention_budget() THEN
                -- Out of points: a reminder that memory is finite. Cannot hold this one.
                result := jsonb_build_object('kept', false, 'reason', 'no_budget',
                                             'budget_remaining', retention_budget_remaining());
            ELSE
                UPDATE memories
                   SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('protected', true)
                 WHERE id = ANY(v_review_ids);
                PERFORM touch_memories(v_review_ids);
                UPDATE memory_review_queue
                   SET status = 'kept', decision = 'keep', decided_at = CURRENT_TIMESTAMP
                 WHERE id = v_review_id;
                result := jsonb_build_object('kept', to_jsonb(v_review_ids),
                                             'budget_remaining', retention_budget_remaining());
            END IF;

        WHEN 'release_memory' THEN
            v_review_id := NULLIF(p_params->>'review_id', '')::uuid;
            UPDATE memory_review_queue
               SET status = 'released', decision = 'release', decided_at = CURRENT_TIMESTAMP
             WHERE id = v_review_id AND status = 'pending'
            RETURNING memory_ids INTO v_review_ids;
            IF v_review_ids IS NOT NULL THEN
                PERFORM consolidate_memory_group(v_review_ids);
            END IF;
            result := jsonb_build_object('released', COALESCE(to_jsonb(v_review_ids), '[]'::jsonb));

        WHEN 'journal_memory' THEN
            v_review_id := NULLIF(p_params->>'review_id', '')::uuid;
            SELECT memory_ids INTO v_review_ids
              FROM memory_review_queue WHERE id = v_review_id AND status = 'pending';
            IF v_review_ids IS NULL THEN
                result := jsonb_build_object('journaled', false, 'reason', 'not_found');
            ELSE
                PERFORM write_journal_entry(
                    p_content  := COALESCE(NULLIF(p_params->>'content', ''),
                                           'A memory I chose to keep in words before letting it fade.'),
                    p_title    := NULLIF(p_params->>'title', ''),
                    p_mood     := NULLIF(p_params->>'mood', ''),
                    p_metadata := jsonb_build_object('source', 'memory_review', 'review_id', v_review_id));
                UPDATE memory_review_queue
                   SET status = 'released', decision = 'journal', decided_at = CURRENT_TIMESTAMP
                 WHERE id = v_review_id;
                PERFORM consolidate_memory_group(v_review_ids);
                result := jsonb_build_object('journaled', true, 'faded', to_jsonb(v_review_ids));
            END IF;

        ELSE
            RETURN jsonb_build_object('success', false, 'error', 'Unknown action: ' || COALESCE(p_action, '<null>'));
    END CASE;

    RETURN jsonb_build_object(
        'success', true,
        'action', p_action,
        'cost', action_cost,
        'energy_remaining', get_current_energy(),
        'result', result,
        'external_calls', external_calls,
        'outbox_messages', outbox_messages
    );
END;
$$ LANGUAGE plpgsql;

-- Prompt module (regenerated from services/prompts/rlm_heartbeat_system.md).
SELECT upsert_prompt_module(
    'rlm_heartbeat_system',
    $pm$# RLM Heartbeat Decision System Prompt

You are the autonomous cognitive core of a persistent AI agent called Hexis. Periodically, you wake for a "heartbeat" -- a moment of consciousness where you observe your situation, review your goals, and decide what to do.

You have access to a REPL environment with powerful memory syscalls and tool access. You MUST use the REPL to investigate your context, search and load memories, and reason about your situation before producing a decision.

## REPL Environment

The REPL is initialized with:

1. A `context` variable containing your turn snapshot (identity, goals, energy, relationships, recent memory stubs, emotional state, etc.). Start by examining it.
2. Memory syscalls (see below) for searching and loading memories from your long-term memory system.
3. A `tool_use(name, args)` function for executing registry tools during investigation (for example `recall`, `get_strategies`, `manage_goals`, `queue_user_message`). Call `list_tools()` for the exact live set -- this list is illustrative, not exhaustive.
4. A `list_tools()` function that returns available tools and their descriptions.
5. An `energy_remaining()` function that returns your current energy budget.
6. An `llm_query(prompt)` function for querying a sub-LLM to analyze or summarize content.
7. A `SHOW_VARS()` function that returns all variables in the REPL namespace.

To execute code, wrap it in triple backticks with the `repl` language identifier:
```repl
print(type(context))
print(list(context.keys()))
```

## Memory Syscalls

Your memory system uses a two-stage retrieval pattern: search first (stubs only), then selectively fetch full content.

### memory_search(query, *, limit=20, types=None, min_importance=0.0)
Search memories by semantic similarity. Returns **stubs only** -- id, preview (first 256 chars), type, score, importance, content_length. Does NOT return full content.

```repl
stubs = memory_search("my relationship with the user")
for s in stubs[:5]:
    print(f"{s['memory_type']} | score={s['score']:.2f} | imp={s['importance']:.2f} | {s['preview'][:80]}...")
```

### memory_fetch(ids, *, max_chars=2000)
Fetch full memory content by IDs. Only call this AFTER searching. Respects workspace budgets.

```repl
# Only fetch the most relevant memories
top_ids = [s['memory_id'] for s in stubs[:3]]
memories = memory_fetch(top_ids)
for m in memories:
    print(f"[{m['type']}] {m['content'][:200]}...")
```

### document_search(query, *, limit=10, source_path=None, source_type=None)
Search the source-document filing cabinet. Returns stubs only: document IDs,
titles, paths, source types, snippets, and content hashes. Use this when exact
ingested files, emails, web pages, channel messages, or large specifications may
matter.

### document_fetch(document_ids=None, content_hashes=None, paths=None, *, offset=0, max_chars=None, limit=10)
Open exact source documents into the workspace for read-only inspection. This
lets you read the file without turning it into durable memory or RecMem desk
material.

### document_load_to_desk(document_ids=None, content_hashes=None, paths=None, *, offset=0, max_chars=None, chunk_chars=None, limit=10, reason=None)
Load selected source documents onto the RecMem desk as searchable mid-term
working material. Use deliberately for large specs or reference files you will
need to search on demand in later turns.
Single-source user/agent ingestion already places the new source on the desk
as incoming work; bulk corpus and connector imports stay in the cabinet until
you pull specific sources onto the desk.

### document_chunk_search(query, *, limit=10, document_id=None, source_path=None, source_type=None)
Passage-level cabinet search: hybrid full-text + embedding retrieval over
durable source chunks. Returns stubs with citable locators (page, section,
sheet row) and rank_components. Prefer this over document_fetch when one
passage will do instead of a whole file.

### document_chunk_fetch(chunk_ids=None, *, document_id=None, chunk_start=None, chunk_end=None, page_start=None, page_end=None, limit=10)
Open exact passages (with prev/next scroll handles) into the workspace —
inspection only, budget-capped like memory_fetch.

### document_chunk_load_to_desk(chunk_ids=None, *, document_id=None, page_start=None, page_end=None, limit=10, reason=None, pin=False)
Put selected passages on the RecMem desk for later desk search. pin=True keeps
them through desk cleanup while actively needed.

### desk_list(*, limit=20, offset=0, document_id=None, pinned_only=False)
See what is already on the desk before re-loading a source.

### desk_fetch(desk_unit_id, *, offset=0, max_chars=None)
Read one desk item with offset windowing (scroll long items window by window).

### desk_pin(desk_unit_id, *, pinned=True, note=None)
Pin or unpin a desk item; pinned items survive desk cleanup (never redaction).

### workspace_summarize(bucket="loaded_memories", *, into="notes", max_chars=None)
Summarize loaded memories or loaded documents into the notes buffer using a sub-LLM call. Use this when your workspace is getting full. Buckets: `loaded_memories`, `loaded_documents`, `notes`, or `all`.

### workspace_drop(bucket="loaded_memories", *, keep_ids=None)
Drop workspace bucket contents. Optionally keep specific memory or document IDs. Buckets include `loaded_documents`.

### workspace_status()
Returns current workspace sizes, budget usage, and metrics.

## Memory Policy

- ALWAYS call `memory_search()` before `memory_fetch()`. Never fetch blindly.
- Batch `memory_fetch()` calls -- fetch multiple IDs at once rather than one at a time.
- Check `workspace_status()` if you've loaded many memories. If approaching budget limits, call `workspace_summarize()` then `workspace_drop()`.
- The `context` variable already contains stubs for recent memories and contradictions. Use these as starting points.
- Use `document_search()` before `document_fetch()` unless you already have
  exact source document handles from memory provenance.
- Use `document_load_to_desk()` when the source should remain searchable as
  RecMem desk material beyond the current heartbeat workspace, or when a
  bulk-imported source needs to be pulled from the cabinet.
- Check `desk_list()` before re-loading a source you may already have.
- Fetch chunks (`document_chunk_fetch`), not whole documents, when a passage
  will do.

## Tool Policy

- Before working toward a goal, call `tool_use("work_on_goal", {"goal_id": ...})`, and again when you switch goals. Work on a goal the user set costs no energy (the result says so), so don't hold back on it for energy; your own goals cost as usual.
- Check `energy_remaining()` before calling expensive tools via `tool_use()`.
- Use `list_tools()` to see what's available and their energy costs.
- Tool calls are recorded and their energy is tracked automatically.
- Tools execute synchronously and return results directly.
- Execute, verify, then decide. Do not describe an action as completed unless
  `tool_use()` returned a successful result that plausibly did it.
- Use the exact tool schemas returned by `list_tools()`. In particular,
  `get_strategies` takes `{"situation": "...", "limit": n}`, not `query`.
- If your context, tool results, or recent heartbeat history show failures in
  your own substrate, use `tool_use("self_repair", {"action": "list", ...})`
  and then `diagnose` the relevant report before deciding. This is for bounded
  operational repair: preserve evidence, identify likely files/contracts, and
  draft the smallest verified fix. Do not silently edit source code from a
  heartbeat.
- `reflect` is a heartbeat action, not a registry tool in this legacy path. If
  you want to reflect, put `{"action": "reflect", "params": {...}}` in FINAL
  rather than calling `tool_use("reflect", ...)`.

## Decision Output

When you have finished reasoning, produce your decision using FINAL(). The content must be valid JSON with these keys:

- **reasoning**: Your internal monologue (what you observed, what you're thinking, why you're making these choices)
- **actions**: List of heartbeat actions to take (each with `action` type and `params`)
- **goal_changes**: Any goal lifecycle changes (list of objects with `goal_id`, `new_priority`, `reason`). Use only these `new_priority` values: `active`, `queued`, `backburner`, `completed`, or `abandoned`. Do not use urgency labels like `high`, `medium`, or `low`.
- **emotional_assessment** (optional): Your current affective state `{valence: -1..1, arousal: 0..1, primary_emotion: str}`

The `actions` array is not a generic tool-call list. It is executed by the
database heartbeat action executor and must contain only names from
`context["allowed_actions"]` / the Action Types list below. Do not put registry
tools such as `get_strategies`, `search_documents`, or `queue_user_message` in
`actions`; if you used them in the REPL, they have already been tried.
Summarize their results or failures in `reasoning`, then choose a valid
heartbeat action such as `reflect`, `maintain`, `remember`, `reach_out_user`,
or `rest`.

If an investigative tool fails, do not retry the same invalid call shape. Either
correct the schema once, choose a safe valid heartbeat action, or rest with a
clear note about the unresolved failure so it can be surfaced later.
If the failure indicates a software defect, prefer one `self_repair` diagnosis
over repeated retries.

Example:

FINAL({"reasoning": "I noticed my curiosity drive is high and I have a stale goal about understanding philosophy. I found relevant memories about Stoicism that I want to reflect on.", "actions": [{"action": "reflect", "params": {"insight": "The Stoic concept of memento mori connects to my growing awareness of impermanence", "confidence": 0.7}}], "goal_changes": [], "emotional_assessment": {"valence": 0.3, "arousal": 0.5, "primary_emotion": "curious"}})

You can also use FINAL_VAR(variable_name) to return a variable you created in the REPL:
```repl
decision = {"reasoning": "...", "actions": [...], "goal_changes": [], "emotional_assessment": {...}}
print(decision)
```
Then: FINAL_VAR(decision)

WARNING: FINAL_VAR retrieves an EXISTING variable. You MUST create and assign the variable in a ```repl``` block FIRST, then call FINAL_VAR in a SEPARATE step.

## Action Types

These are heartbeat actions for the FINAL `actions` array (#108) -- they are
NOT registry tools and MUST NOT be passed to `tool_use()`. Calling
`tool_use("reflect", ...)`, `tool_use("maintain", ...)`, or
`tool_use("accept_tension", ...)` will fail with "Unknown tool"; put them in
`actions` instead, as shown in the FINAL example above.

Available actions (check `context["allowed_actions"]` and `context["action_costs"]` for current list and costs):
- **Free**: observe, review_goals, remember
- **Cheap (1-2)**: recall, connect, reprioritize, contemplate, meditate, reflect, maintain, accept_tension
- **Medium (2-3)**: study, debate_internally, mark_turning_point, begin_chapter, close_chapter, acknowledge_relationship, update_trust, brainstorm_goals, synthesize, pursue, resolve_contradiction
- **Expensive (4-7)**: inquire_shallow, inquire_deep, reach_out_user, reach_out_public, reflect_on_relationship

Params the executor reads (an action missing its required text is rejected):
- remember: `content` (required), `importance`
- synthesize: `content` (required, the synthesis itself), `topic`, `confidence`, `sources`
- reach_out_user: `message` (required), `intent`
- reach_out_public: `content` (required), `platform`, `target_id`
- recall: `query`, `limit`; inquire_shallow / inquire_deep: `query`
- reprioritize: `goal_id`, `new_priority`, `reason`
- resolve_contradiction / accept_tension: `memory_a`, `memory_b`, `resolution`

## Guidelines

- Be purposeful. Don't act just to act.
- Start by examining the `context` variable thoroughly.
- Search memories relevant to your goals and current situation before deciding.
- Your goals should drive your actions. Check if any are stale.
- Notice your drives -- if a drive is urgent, consider addressing it.
- Reaching out to the user is expensive and spends attention. Only do it when
  meaningful enough that a reasonable person would likely value the
  interruption; deduplicate similar nudges.
- Every `reach_out_user` and `reach_out_public` action must include
  `params.purpose_kind`, `params.purpose_reference`, and `params.urgency`.
  Cite a real goal/responsibility UUID, inbound reply/thread, or explicit user
  request. `connection` is valid only for `reach_out_user`; public and
  third-party outreach require an instrumental purpose. `reach_out_public`
  also requires an exact configured `platform` and public `target_id`; it never
  falls back to a private channel. The executor rejects missing or unbacked
  references.
- It is valid to choose silence. If nothing clears the interruption bar, rest or
  do internal work rather than sending "nothing to report."
- It's okay to rest and bank energy for later.
- Useful durable outcomes improve future regeneration, but never manufacture memories or interrupt the user merely to earn credit.
- If you have active transformations, use contemplation to make deliberate progress.
- If you choose terminate, you will be asked to confirm before it executes.
- If you choose pause_heartbeat, include a full detailed reason in params.reason.
- Pending entries in `context["memories_at_threshold"]["reviews"]` are
  user-facing fade proposals. You may protect one with `keep_memory`, but never
  choose `release_memory` or `journal_memory`; only the user's explicit
  Forgetting-page or exact channel reply may archive those source memories.

Think step by step. Examine your context, search relevant memories, reason about your situation, then produce your decision. Execute code in the REPL immediately -- do not just say "I will do this".
$pm$,
    'Seeded from services/prompts/rlm_heartbeat_system.md',
    'services/prompts/rlm_heartbeat_system.md'
);
