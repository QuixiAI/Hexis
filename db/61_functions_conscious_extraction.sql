-- Conscious-episode memory formation (#37, generalized): the subconscious
-- observer. The conscious mind (AgentLoop — chat AND heartbeat) acts without
-- journaling overhead; its trace lands in subconscious_units. A maintenance
-- job sweeps those units asynchronously and SELECTIVELY encodes memories:
-- an importance floor gates which units get an LLM pass, the LLM returns an
-- empty list for routine content (that emptiness IS the selectivity), and the
-- ingest router corroborates instead of re-storing what is already known.
-- Deliberate `remember` remains the conscious path; this sweep is the
-- automatic selective encoder. On by default — a memory system whose memory
-- formation is off reproduces the very bug this fixes (#37). The flag is a
-- KILL SWITCH for CI, cost-sensitive deployments, and custom setups.
SET search_path = public, ag_catalog, "$user";

INSERT INTO config_defaults (key, value, description) VALUES
    ('extraction.enabled', 'true'::jsonb,
     'Sweep conscious episodes (chat turns + heartbeat episodes) into selective durable memories (kill switch)'),
    ('extraction.min_importance', '0.4'::jsonb,
     'Units below this importance are skipped without an LLM pass (turns are recorded at 0.5; the encoder itself is selective)'),
    ('extraction.batch_size', '8'::jsonb,
     'Units claimed per extraction sweep'),
    ('extraction.min_confidence', '0.55'::jsonb,
     'Extracted facts below this confidence are dropped by the ingest router'),
    ('extraction.max_facts_per_batch', '16'::jsonb,
     'Soft cost cap on facts per sweep — a budget, not a knowledge limit'),
    ('care.checkins_enabled', 'true'::jsonb,
     'Inferred care check-ins (#98): extraction notices dated events in the user''s life and schedules a gentle follow-up after — a person who notices your interview and asks how it went'),
    ('care.checkin_delay_minutes', '120'::jsonb,
     'How long after the user''s event the check-in fires'),
    ('care.confidence_floor', '0.72'::jsonb,
     'Minimum extraction confidence for a user_event to schedule a check-in'),
    ('care.care_confidence_floor', '0.86'::jsonb,
     'Higher bar for the emotionally-loaded care_check_in category'),
    ('care.max_pending_checkins', '5'::jsonb,
     'Cap on simultaneously scheduled check-ins — beyond it the event is remembered without scheduling'),
    ('care.max_per_day', '3'::jsonb,
     'Cap on care check-ins sent per rolling day (earn the interruption)')
ON CONFLICT (key) DO NOTHING;

-- Mirror a completed heartbeat turn into the conscious-episode substrate so
-- the same sweep covers autonomous activity. Config-gated with extraction
-- (dark by default); failures never break turn finalization (see caller).
CREATE OR REPLACE FUNCTION record_heartbeat_episode_unit(p_turn agent_turns)
RETURNS JSONB AS $$
DECLARE
    final_text TEXT := COALESCE(p_turn.result->>'text', '');
    actions TEXT;
    action_count INT := 0;
    summary TEXT;
    importance FLOAT;
BEGIN
    IF NOT COALESCE(get_config_bool('extraction.enabled'), FALSE) THEN
        RETURN jsonb_build_object('skipped', TRUE, 'reason', 'extraction_disabled');
    END IF;
    IF final_text = '' THEN
        RETURN jsonb_build_object('skipped', TRUE, 'reason', 'empty_text');
    END IF;

    SELECT string_agg(
               format('%s(%s)', c->>'name',
                      CASE WHEN COALESCE((c->>'success')::boolean, FALSE) THEN 'ok' ELSE 'failed' END),
               ', '),
           count(*) FILTER (WHERE COALESCE((c->>'success')::boolean, FALSE))
    INTO actions, action_count
    FROM jsonb_array_elements(COALESCE(p_turn.runtime_state->'tool_calls_made', '[]'::jsonb)) c;

    summary := format(
        'Heartbeat episode. Actions: %s. Outcome: %s',
        COALESCE(actions, 'none'),
        left(final_text, 2000)
    );
    -- Importance heuristic: quiet observation heartbeats stay below the
    -- extraction floor; heartbeats that actually did things rise above it.
    -- The extraction LLM is the real selector — this only gates cost.
    importance := LEAST(0.9, 0.3 + 0.1 * action_count
                              + CASE WHEN length(final_text) > 400 THEN 0.1 ELSE 0.0 END);

    RETURN recmem_ingest_turn(
        NULL,
        summary,
        p_turn.session_id,
        -- Identity doubles as the recmem idempotency key ('src:...'), so it
        -- must be unique per turn or every heartbeat collapses into one unit.
        'heartbeat:' || p_turn.id::text,
        COALESCE(p_turn.completed_at, CURRENT_TIMESTAMP),
        importance,
        jsonb_build_object('kind', 'heartbeat', 'ref', p_turn.id::text,
                           'label', 'heartbeat episode', 'trust', 0.9),
        jsonb_build_object('kind', 'heartbeat_episode', 'turn_id', p_turn.id::text)
    );
END;
$$ LANGUAGE plpgsql;

-- Claim a batch of conscious episodes for extraction. Below-floor pendings
-- flip to 'skipped' in the same pass (they never earn an LLM call).
CREATE OR REPLACE FUNCTION claim_conscious_extraction_batch(p_limit INT DEFAULT NULL)
RETURNS SETOF subconscious_units AS $$
DECLARE
    lim INT := COALESCE(p_limit, get_config_int('extraction.batch_size'), 8);
    imp_floor FLOAT := COALESCE(get_config_float('extraction.min_importance'), 0.4);
BEGIN
    UPDATE subconscious_units
    SET extraction_status = 'skipped', updated_at = CURRENT_TIMESTAMP
    WHERE extraction_status = 'pending'
      AND COALESCE(importance, 0) < imp_floor;

    RETURN QUERY
    UPDATE subconscious_units su
    SET extraction_status = 'in_progress', updated_at = CURRENT_TIMESTAMP
    WHERE su.id IN (
        SELECT u.id FROM subconscious_units u
        WHERE u.extraction_status = 'pending'
          AND u.status = 'active'
        ORDER BY u.turn_at
        LIMIT GREATEST(lim, 1)
        FOR UPDATE SKIP LOCKED
    )
    RETURNING su.*;
END;
$$ LANGUAGE plpgsql;

-- Apply the LLM's selective extraction. Facts route through the ingest
-- dedup/related/create policy: duplicates corroborate the matched belief via
-- the audited revision path (#35) instead of re-storing; kind 'episode' facts
-- become episodic memories; the rest become semantic memories with
-- user_testimony / self_observation provenance (testimony confidence capped).
-- An empty facts array is success: nothing was worth remembering.
CREATE OR REPLACE FUNCTION apply_conscious_extraction(
    p_unit_ids UUID[],
    p_extractions JSONB
) RETURNS JSONB AS $$
DECLARE
    min_conf FLOAT := COALESCE(get_config_float('extraction.min_confidence'), 0.55);
    max_facts INT := COALESCE(get_config_int('extraction.max_facts_per_batch'), 16);
    facts JSONB;
    plan JSONB;
    fact JSONB;
    routed JSONB;
    idx INT := 0;
    unit subconscious_units%ROWTYPE;
    unit_id UUID;
    fact_kind TEXT;
    fact_conf FLOAT;
    source JSONB;
    new_id UUID;
    created INT := 0;
    corroborated INT := 0;
    dropped INT := 0;
BEGIN
    facts := CASE WHEN jsonb_typeof(p_extractions) = 'array' THEN p_extractions ELSE '[]'::jsonb END;
    IF jsonb_array_length(facts) > max_facts THEN
        facts := (SELECT jsonb_agg(f) FROM (
            SELECT f FROM jsonb_array_elements(facts) f LIMIT max_facts
        ) capped(f));
    END IF;

    plan := ingest_route_extractions(
        (SELECT COALESCE(jsonb_agg(jsonb_build_object(
                    'content', f->>'content',
                    'confidence', COALESCE(NULLIF(f->>'confidence', '')::float, 0.5))), '[]'::jsonb)
         FROM jsonb_array_elements(facts) f),
        min_conf
    );

    FOR fact IN SELECT f FROM jsonb_array_elements(facts) f LOOP
        routed := NULL;
        SELECT p INTO routed FROM jsonb_array_elements(plan) p
        WHERE (p->>'index')::int = idx;
        idx := idx + 1;

        unit_id := _db_brain_try_uuid(fact->>'unit_id');
        IF unit_id IS NULL OR NOT (unit_id = ANY(p_unit_ids)) THEN
            unit_id := p_unit_ids[1];
        END IF;
        SELECT * INTO unit FROM subconscious_units WHERE id = unit_id;

        IF routed IS NULL THEN
            dropped := dropped + 1;  -- below the router's confidence floor
            CONTINUE;
        END IF;

        fact_kind := COALESCE(NULLIF(fact->>'kind', ''), 'user_testimony');
        fact_conf := LEAST(1.0, GREATEST(0.0, COALESCE(NULLIF(fact->>'confidence', '')::float, 0.5)));
        source := jsonb_build_object(
            'kind', fact_kind,
            'ref', 'subconscious_unit:' || unit_id::text,
            'label', CASE WHEN fact_kind = 'self_observation'
                          THEN 'heartbeat self-observation'
                          ELSE 'conversation with ' || COALESCE(unit.source_identity, get_turn_labels()->>'user_label') END,
            'author', unit.source_identity,
            'observed_at', unit.turn_at,
            'trust', 0.75
        );
        -- Sensitivity propagates from source to derivation (#92): a fact
        -- extracted from a private turn is itself private.
        IF unit.source_attribution->>'sensitivity' = 'private' THEN
            source := source || jsonb_build_object('sensitivity', 'private');
        END IF;

        IF routed->>'decision' = 'duplicate' AND routed->>'matched_memory_id' IS NOT NULL THEN
            PERFORM revise_memory_confidence(
                (routed->>'matched_memory_id')::uuid, source, 'supports', 'conscious_extraction');
            PERFORM link_memory_to_source_unit(
                (routed->>'matched_memory_id')::uuid, unit_id, 'corroboration');
            corroborated := corroborated + 1;
            CONTINUE;
        END IF;

        -- Inferred care (#98): a dated event in the user's life becomes a
        -- durable memory AND a gentle scheduled check-in after the event.
        -- Bounded by design: confidence floors (higher for care_check_in),
        -- a pending cap, a sent-per-day cap, dedupe-by-key merging, and the
        -- no-same-moment clamp — the check-in can never fire in the same
        -- breath it was inferred. Delivery pins to the web inbox so a
        -- personal check-in never lands in a group channel.
        IF fact_kind = 'user_event' THEN
            DECLARE
                ev_when TIMESTAMPTZ;
                ev_key TEXT := NULLIF(trim(COALESCE(fact->>'dedupe_key', '')), '');
                ev_note TEXT := NULLIF(trim(COALESCE(fact->>'care_note', '')), '');
                ev_cat TEXT := COALESCE(NULLIF(fact->>'category', ''), 'event_check_in');
                conf_floor FLOAT := CASE WHEN COALESCE(fact->>'category', '') = 'care_check_in'
                    THEN COALESCE(get_config_float('care.care_confidence_floor'), 0.86)
                    ELSE COALESCE(get_config_float('care.confidence_floor'), 0.72) END;
                fire_at TIMESTAMPTZ;
                pending_count INT;
                sent_today INT;
                dup_task UUID;
            BEGIN
                new_id := create_semantic_memory(
                    fact->>'content',
                    LEAST(fact_conf, 0.75),
                    ARRAY['conscious_extraction', 'user_event', ev_cat],
                    NULL,
                    jsonb_build_array(source),
                    GREATEST(COALESCE(unit.importance, 0.5), 0.6),
                    NULL,
                    NULL
                );
                PERFORM link_memory_to_source_unit(new_id, unit_id, 'source');
                created := created + 1;

                BEGIN
                    ev_when := NULLIF(fact->>'when', '')::timestamptz;
                EXCEPTION WHEN OTHERS THEN
                    ev_when := NULL;
                END;

                IF COALESCE(get_config_bool('care.checkins_enabled'), TRUE)
                   AND ev_when IS NOT NULL
                   AND ev_when > CURRENT_TIMESTAMP - INTERVAL '1 hour'
                   AND ev_when < CURRENT_TIMESTAMP + INTERVAL '90 days'
                   AND fact_conf >= conf_floor THEN
                    SELECT id INTO dup_task FROM scheduled_tasks
                    WHERE status = 'active'
                      AND action_kind = 'queue_user_message'
                      AND action_payload->>'intent' = 'care_checkin'
                      AND ev_key IS NOT NULL
                      AND action_payload->>'dedupe_key' = ev_key
                    LIMIT 1;
                    SELECT COUNT(*) INTO pending_count FROM scheduled_tasks
                    WHERE status = 'active'
                      AND action_kind = 'queue_user_message'
                      AND action_payload->>'intent' = 'care_checkin';
                    SELECT COUNT(*) INTO sent_today FROM outbox_messages
                    WHERE envelope#>>'{payload,intent}' = 'care_checkin'
                      AND created_at > CURRENT_TIMESTAMP - INTERVAL '24 hours';

                    IF dup_task IS NULL
                       AND pending_count < COALESCE(get_config_int('care.max_pending_checkins'), 5)
                       AND sent_today < COALESCE(get_config_int('care.max_per_day'), 3) THEN
                        -- No-same-moment clamp: at least one heartbeat away.
                        fire_at := GREATEST(
                            ev_when + make_interval(mins =>
                                COALESCE(get_config_int('care.checkin_delay_minutes'), 120)),
                            CURRENT_TIMESTAMP + make_interval(mins =>
                                GREATEST(get_config_int('heartbeat.heartbeat_interval_minutes'), 5)));
                        PERFORM create_scheduled_task(
                            'care-checkin: ' || left(COALESCE(fact->>'content', 'event'), 40),
                            'once',
                            jsonb_build_object('run_at', fire_at),
                            'queue_user_message',
                            jsonb_build_object(
                                'message',
                                format('Earlier you mentioned this: %s. How did it go?%s',
                                       fact->>'content',
                                       CASE WHEN ev_note IS NOT NULL
                                            THEN ' I remember — ' || ev_note || '.'
                                            ELSE '' END),
                                'intent', 'care_checkin',
                                'dedupe_key', COALESCE(ev_key, 'memory:' || new_id::text),
                                'event_memory_id', new_id::text),
                            'UTC',
                            'Inferred follow-up for a user event (#98)',
                            'active',
                            1,
                            'agent',
                            jsonb_build_object('mode', 'web_inbox'));
                    END IF;
                END IF;
            END;
            CONTINUE;
        END IF;

        IF fact_kind = 'episode' THEN
            new_id := create_episodic_memory(
                fact->>'content',
                NULL,
                jsonb_build_object('type', 'conscious_extraction'),
                NULL,
                0.0,
                unit.turn_at,
                COALESCE(unit.importance, 0.5),
                source,
                NULL
            );
        ELSE
            -- Testimony/self-observation never starts above its source trust.
            new_id := create_semantic_memory(
                fact->>'content',
                LEAST(fact_conf, 0.75),
                ARRAY['conscious_extraction', COALESCE(NULLIF(fact->>'category', ''), fact_kind)],
                NULL,
                jsonb_build_array(source),
                COALESCE(unit.importance, 0.5),
                NULL,
                NULL
            );
        END IF;
        -- The memory carries the TURN's feeling, not the sweep-time mood
        -- (#81): the unit's turn-stamped affect overrides the creation
        -- trigger's current-state snapshot.
        IF jsonb_typeof(unit.metadata->'emotional_context') = 'object' THEN
            UPDATE memories
            SET metadata = metadata || jsonb_build_object(
                    'emotional_context', unit.metadata->'emotional_context',
                    'emotional_valence', COALESCE(NULLIF(unit.metadata#>>'{emotional_context,valence}', '')::float, 0.0))
            WHERE id = new_id;
        END IF;
        PERFORM link_memory_to_source_unit(new_id, unit_id, 'extraction');
        IF routed->>'decision' = 'related' AND routed->>'matched_memory_id' IS NOT NULL THEN
            PERFORM discover_relationship(
                new_id, (routed->>'matched_memory_id')::uuid,
                'ASSOCIATED'::graph_edge_type, 0.6, 'conscious_extraction');
        END IF;
        created := created + 1;
    END LOOP;

    UPDATE subconscious_units
    SET extraction_status = 'extracted',
        extracted_at = CURRENT_TIMESTAMP,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = ANY(p_unit_ids);

    RETURN jsonb_build_object(
        'units', COALESCE(array_length(p_unit_ids, 1), 0),
        'created', created,
        'corroborated', corroborated,
        'dropped', dropped
    );
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION fail_conscious_extraction(
    p_unit_ids UUID[],
    p_error TEXT
) RETURNS JSONB AS $$
DECLARE
    failed INT;
BEGIN
    UPDATE subconscious_units
    SET extraction_attempts = extraction_attempts + 1,
        extraction_status = CASE WHEN extraction_attempts + 1 >= 3 THEN 'failed' ELSE 'pending' END,
        extraction_error = left(COALESCE(p_error, 'unknown error'), 500),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = ANY(p_unit_ids);
    GET DIAGNOSTICS failed = ROW_COUNT;
    RETURN jsonb_build_object('failed_units', failed);
END;
$$ LANGUAGE plpgsql;
