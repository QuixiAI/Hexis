-- 0259: Google Drive, where messages to the user go, and connector fixes.
--
-- - Google Drive is a connector: it signs in with the same Google client and
--   callback as Gmail but keeps its own token (integration.google_drive.default)
--   and asks only for read-only Drive access. Chosen folders
--   (integrations.google_drive.folders) are imported as connector source items
--   by services/google_drive.py; nothing is written to Drive.
-- - Messages meant for the user (a heartbeat reaching out, queue_user_message
--   from a heartbeat) always reach the dashboard inbox and otherwise go where
--   outreach.destination says (core/outreach_destination.py) instead of to
--   whichever chat was most recently active. reach_out_user may name a channel
--   (params.channel); the rest of execute_heartbeat_action is unchanged.
-- - One document per imported connector item: the ingestion job used to store
--   the text again under its plain content hash, so each imported item existed
--   twice. 0256 stopped that going forward; this merges the pairs already
--   there (the copy with chunks and memories takes the connector's identity;
--   receipts and memory provenance follow).
-- Idempotent.
SET search_path = public, ag_catalog;

-- Google Drive: signs in with the same Google client and callback as Gmail but
-- keeps its own token (integration.google_drive.default) and asks only for
-- read-only Drive access. Folders are imported as connector source items by
-- services/google_drive.py; nothing is ever written to Drive.
INSERT INTO integration_connectors (
    id, display_name, category, auth_type, status,
    capability_manifest, setup_manifest, docs_url, metadata
)
VALUES (
    'google_drive',
    'Google Drive',
    'personal_data',
    'oauth2',
    'available',
    jsonb_build_object(
        'read', jsonb_build_object(
            'label', 'Read files in chosen folders',
            'scopes', jsonb_build_array('https://www.googleapis.com/auth/drive.readonly'),
            'status', 'available',
            'scope_kind', 'read'),
        'ingest', jsonb_build_object(
            'label', 'Remember what is read',
            'scopes', jsonb_build_array(),
            'status', 'available',
            'scope_kind', 'local')
    ),
    jsonb_build_object(
        'flow', 'oauth2_authorization_code_pkce',
        'setup_label', 'Google Drive sign-in',
        'default_capabilities', jsonb_build_array('read', 'ingest'),
        'capability_order', jsonb_build_array('read', 'ingest'),
        'required_scopes', jsonb_build_array('https://www.googleapis.com/auth/userinfo.email'),
        'scope_order', jsonb_build_array(
            'https://www.googleapis.com/auth/userinfo.email',
            'https://www.googleapis.com/auth/drive.readonly'),
        'shares_client_with', 'gmail',
        'setup_steps', jsonb_build_array(
            'Open the Google setup page.',
            'Create or choose a project named Hexis.',
            'Enable the Google Drive API for that project.',
            'Set up the app consent screen. For a personal Google account, choose External and add your address as a test user if Google asks.',
            'On the Credentials page, click Create credentials, choose Google''s sign-in client option, set Application type to Desktop app, and name it Hexis.',
            'Download the setup file Google gives you, and upload it on the Gmail card, which Drive shares.')
    ),
    'https://console.cloud.google.com/apis/library/drive.googleapis.com',
    jsonb_build_object('provider', 'google', 'seeded_by', 'db/migrations/0259_google_drive_outreach_destination_and_connector_fixes.sql')
)
ON CONFLICT (id) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    category = EXCLUDED.category,
    auth_type = EXCLUDED.auth_type,
    status = EXCLUDED.status,
    capability_manifest = EXCLUDED.capability_manifest,
    setup_manifest = EXCLUDED.setup_manifest,
    docs_url = EXCLUDED.docs_url,
    metadata = EXCLUDED.metadata,
    updated_at = CURRENT_TIMESTAMP;

-- Where the agent's messages to the user go (core/outreach_destination.py),
-- and the Drive folders it reads (services/google_drive.py).
INSERT INTO config_defaults (key, value, description) VALUES
    ('outreach.destination', '"auto"'::jsonb,
     'Where messages meant for the user go besides the dashboard inbox: auto (the agent decides), slack, telegram, email, or inbox (dashboard only)'),
    ('outreach.slack_user_id', '""'::jsonb,
     'The user''s own Slack account (U... id) for messages to them; empty means the channel''s operator setting, else the most recent Slack DM'),
    ('outreach.telegram_chat_id', '""'::jsonb,
     'The user''s Telegram chat for messages to them; empty means the most recent Telegram chat'),
    ('integrations.google_drive.folders', '[]'::jsonb,
     'Google Drive folders the agent reads: [{"id", "name", "url"}], managed from the Connections page')
ON CONFLICT (key) DO NOTHING;

-- reach_out_user may name a channel (params.channel).
-- execute_heartbeat_action is a narrow wrapper (0233, db/46j) that handles
-- resolve_contradiction / accept_tension and hands every other action to
-- execute_heartbeat_action_legacy_contradictions. 0257 replaced the wrapper
-- itself with the full action body, so a database that applied 0257 lost the
-- contradiction handling. The full body is replaced under the legacy name
-- here, and the wrapper is installed again on top of it.
DO $rename$
BEGIN
    IF to_regprocedure('execute_heartbeat_action_legacy_contradictions(uuid,text,jsonb)') IS NULL THEN
        ALTER FUNCTION execute_heartbeat_action(UUID, TEXT, JSONB)
            RENAME TO execute_heartbeat_action_legacy_contradictions;
    END IF;
END;
$rename$;

CREATE OR REPLACE FUNCTION execute_heartbeat_action_legacy_contradictions(
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
                jsonb_strip_nulls(jsonb_build_object(
                    'message', p_params->>'message',
                    'intent', p_params->>'intent',
                    -- Where the agent wants it to reach the user
                    -- (core.outreach_destination decides whether it may).
                    'channel', NULLIF(p_params->>'channel', ''),
                    'heartbeat_id', p_heartbeat_id,
                    'purpose_kind', p_params->>'purpose_kind',
                    'purpose_reference', p_params->>'purpose_reference',
                    'urgency', COALESCE(p_params->>'urgency', 'normal')
                ))
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

-- One document per imported connector item. The copy reading made (plain
-- hash, chunks, memories) keeps its id and takes the connector row's hash,
-- path and attribution; the empty connector row goes. Pairs where the
-- connector's row was read too are left alone.
DROP TABLE IF EXISTS _dup_connector_docs, _dup_stored;
CREATE TEMP TABLE _dup_connector_docs ON COMMIT DROP AS
SELECT DISTINCT ON (s.id)
    c.id AS copy_id, c.content_hash AS copy_hash,
    s.id AS stored_id, s.content_hash AS stored_hash
FROM source_documents s
JOIN source_documents c
  ON c.content = s.content AND c.id <> s.id
WHERE s.content_hash LIKE 'connector:%'
  AND c.content_hash NOT LIKE 'connector:%'
  AND c.source_type LIKE 'connector\_%'
  AND EXISTS (SELECT 1 FROM connector_source_items i WHERE i.source_document_id = s.id)
  AND NOT EXISTS (SELECT 1 FROM connector_source_items i WHERE i.source_document_id = c.id)
  AND NOT EXISTS (SELECT 1 FROM source_document_chunks k WHERE k.source_document_id = s.id)
  AND NOT EXISTS (SELECT 1 FROM ingestion_receipts r WHERE r.doc_ref = s.content_hash)
ORDER BY s.id, c.created_at DESC;

-- Rows that point at the connector's row move to the copy.
UPDATE connector_source_items t SET source_document_id = d.copy_id
FROM _dup_connector_docs d WHERE t.source_document_id = d.stored_id;
UPDATE connector_item_importance t SET source_document_id = d.copy_id
FROM _dup_connector_docs d WHERE t.source_document_id = d.stored_id;
UPDATE channel_source_items t SET source_document_id = d.copy_id
FROM _dup_connector_docs d WHERE t.source_document_id = d.stored_id;
UPDATE source_extraction_runs t SET source_document_id = d.copy_id
FROM _dup_connector_docs d WHERE t.source_document_id = d.stored_id;
UPDATE source_artifacts t SET source_document_id = d.copy_id
FROM _dup_connector_docs d WHERE t.source_document_id = d.stored_id;

-- The copy takes the connector's identity (content_hash is unique, so the
-- empty row goes first).
CREATE TEMP TABLE _dup_stored ON COMMIT DROP AS
SELECT s.* FROM source_documents s JOIN _dup_connector_docs d ON d.stored_id = s.id;
DELETE FROM source_documents s USING _dup_connector_docs d WHERE s.id = d.stored_id;
UPDATE source_documents c
SET content_hash = s.content_hash,
    original_hash = s.content_hash,
    path = s.path,
    file_type = s.file_type,
    source_attribution = c.source_attribution || s.source_attribution,
    metadata = c.metadata || s.metadata,
    updated_at = CURRENT_TIMESTAMP
FROM _dup_connector_docs d JOIN _dup_stored s ON s.id = d.stored_id
WHERE c.id = d.copy_id;

-- Receipts and provenance that name the copy's old hash. A content hash is a
-- unique 64-character hex string, so replacing it as text is exact.
UPDATE ingestion_receipts r SET doc_ref = d.stored_hash
FROM _dup_connector_docs d WHERE r.doc_ref = d.copy_hash;
UPDATE ingestion_receipts r SET section_hash = d.stored_hash
FROM _dup_connector_docs d WHERE r.section_hash = d.copy_hash;
UPDATE ingestion_receipts r SET section_hash = 'enc:' || d.stored_hash
FROM _dup_connector_docs d WHERE r.section_hash = 'enc:' || d.copy_hash;

-- A memory can cite several documents (source_references), so each pair is
-- applied on its own.
DO $$
DECLARE
    d RECORD;
BEGIN
    FOR d IN SELECT copy_id, copy_hash, stored_hash FROM _dup_connector_docs LOOP
        UPDATE memories m
        SET source_attribution = replace(m.source_attribution::text, d.copy_hash, d.stored_hash)::jsonb,
            metadata = replace(m.metadata::text, d.copy_hash, d.stored_hash)::jsonb
        WHERE m.source_attribution->>'content_hash' = d.copy_hash
           OR m.source_attribution->>'source_document_id' = d.copy_id::text
           OR m.metadata @> jsonb_build_object('source_references',
                  jsonb_build_array(jsonb_build_object('content_hash', d.copy_hash)));
        UPDATE subconscious_units u
        SET source_attribution = replace(u.source_attribution::text, d.copy_hash, d.stored_hash)::jsonb
        WHERE u.source_attribution->>'content_hash' = d.copy_hash;
    END LOOP;
END;
$$;

CREATE OR REPLACE FUNCTION execute_heartbeat_action(
    p_heartbeat_id UUID,
    p_action TEXT,
    p_params JSONB DEFAULT '{}'
)
RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    v_case_id UUID := _db_brain_try_uuid(p_params->>'case_id');
    v_memory_a UUID := _db_brain_try_uuid(p_params->>'memory_a');
    v_memory_b UUID := _db_brain_try_uuid(p_params->>'memory_b');
    v_filed JSONB;
BEGIN
    IF p_action NOT IN ('resolve_contradiction', 'accept_tension') THEN
        RETURN execute_heartbeat_action_legacy_contradictions(p_heartbeat_id, p_action, p_params);
    END IF;
    IF v_case_id IS NULL AND v_memory_a IS NOT NULL AND v_memory_b IS NOT NULL THEN
        SELECT id INTO v_case_id
        FROM contradiction_cases
        WHERE status = 'pending'
          AND LEAST(memory_a, memory_b) = LEAST(v_memory_a, v_memory_b)
          AND GREATEST(memory_a, memory_b) = GREATEST(v_memory_a, v_memory_b)
        LIMIT 1;
    END IF;
    IF v_case_id IS NULL AND v_memory_a IS NOT NULL AND v_memory_b IS NOT NULL THEN
        v_filed := file_contradiction_case(
            v_memory_a, v_memory_b, v_memory_b,
            COALESCE(NULLIF(p_params->>'resolution', ''), NULLIF(p_params->>'note', ''), 'Contradictory memories need a user decision.'),
            COALESCE(NULLIF(p_params->>'confidence', '')::float, 0.8),
            'heartbeat',
            jsonb_build_object('heartbeat_id', p_heartbeat_id)
        );
        v_case_id := _db_brain_try_uuid(v_filed->>'case_id');
    END IF;
    IF v_case_id IS NULL THEN
        RETURN jsonb_build_object(
            'success', FALSE,
            'error', 'No pending contradiction case was found. Detection must file a case before review.',
            'action', p_action,
            'cost', 0,
            'energy_remaining', get_current_energy()
        );
    END IF;
    UPDATE contradiction_cases
    SET proposed_at = COALESCE(proposed_at, CURRENT_TIMESTAMP),
        metadata = metadata || jsonb_build_object('noticed_by_heartbeat_id', p_heartbeat_id),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = v_case_id AND status = 'pending';
    RETURN jsonb_build_object(
        'success', TRUE,
        'action', p_action,
        'cost', 0,
        'energy_remaining', get_current_energy(),
        'result', jsonb_build_object(
            'case_id', v_case_id,
            'decision_required', TRUE,
            'changed_memories', FALSE,
            'next_step', 'Include this case in the next daily contradiction review; only the user chooses new, old, or both.'
        ),
        'external_calls', '[]'::jsonb,
        'outbox_messages', '[]'::jsonb
    );
END;
$$;
