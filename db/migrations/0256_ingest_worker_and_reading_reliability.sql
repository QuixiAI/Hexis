-- 0256: Reading has its own worker, cleans every document, and never loses
-- a document to one bad model answer.
--
-- - A queued reading job now wakes the ingest worker (hexis-worker --mode
--   ingest; the ingest_worker service, or the ingest host service) through
--   NOTIFY instead of waiting for the maintenance loop, and that worker
--   registers as its own kind.
-- - "Is any reading work due?" (ingestion_jobs_due) asks the same question the
--   claim does, so a job stranded in_progress by a stopped worker is seen.
-- - A stale job that has used its last attempt fails, one whose cancel was
--   requested is cancelled, and a job cancelled mid-read ends cancelled.
-- - A slowly read chunk with no facts is receipted too, so a retry does not
--   read it again.
-- - A restarted worker's reading jobs resume in minutes (claim timeout 120 s,
--   not 30 min), several documents are read at once, and the output budget
--   per reading call leaves room for reasoning models to think.
-- - Each imported connector item used to exist twice (the connector's row and
--   the copy reading stored under the plain content hash); the copy, which
--   holds the chunks and memories, takes the connector's identity.
-- Idempotent.
SET search_path = public, ag_catalog;

-- Config: new keys, and defaults that changed. A value a person set by hand
-- is left alone; only the old default is moved to the new one.
INSERT INTO config_defaults (key, value, description) VALUES
    ('ingest.job_claim_timeout_s', '120'::jsonb,
     'Seconds without a progress refresh before another worker may reclaim a reading job'),
    ('ingest.job_batch_size', '4'::jsonb,
     'Documents the ingest worker reads at once'),
    ('ingest.job_step_budget_s', '120'::jsonb,
     'Seconds a shared-loop caller (CLI, tests) keeps claiming queued documents to read'),
    ('ingest.llm_max_tokens', '8000'::jsonb,
     'Output token budget per ingestion LLM call (includes reasoning)')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, description = EXCLUDED.description;

UPDATE config SET value = '120'::jsonb, description = 'Seconds without a progress refresh before another worker may reclaim a reading job'
WHERE key = 'ingest.job_claim_timeout_s' AND value = '1800'::jsonb;
UPDATE config SET value = '4'::jsonb, description = 'Documents the ingest worker reads at once'
WHERE key = 'ingest.job_batch_size' AND value = '1'::jsonb;

-- The ingest worker registers as its own kind.
ALTER TABLE worker_instances DROP CONSTRAINT IF EXISTS worker_instances_mode_check;
ALTER TABLE worker_instances ADD CONSTRAINT worker_instances_mode_check
    CHECK (mode IN ('heartbeat', 'maintenance', 'both', 'channel', 'ingest', 'unknown'));

CREATE OR REPLACE FUNCTION register_worker_instance(
    p_mode TEXT,
    p_instance_name TEXT DEFAULT NULL,
    p_metadata JSONB DEFAULT '{}'::jsonb
) RETURNS UUID AS $$
DECLARE
    v_id UUID;
    v_mode TEXT := lower(btrim(COALESCE(p_mode, 'unknown')));
    v_metadata JSONB := COALESCE(p_metadata, '{}'::jsonb);
    v_process_id INT;
    v_host_name TEXT;
    v_build_id TEXT;
BEGIN
    IF v_mode NOT IN ('heartbeat', 'maintenance', 'both', 'channel', 'ingest', 'unknown') THEN
        v_mode := 'unknown';
    END IF;

    IF jsonb_typeof(v_metadata->'process_id') = 'number' THEN
        v_process_id := (v_metadata->>'process_id')::int;
    END IF;
    v_host_name := NULLIF(v_metadata->>'host_name', '');
    v_build_id := NULLIF(v_metadata->>'build_id', '');

    INSERT INTO worker_instances (
        mode,
        instance_name,
        process_id,
        host_name,
        status,
        build_id,
        metadata
    ) VALUES (
        v_mode,
        NULLIF(p_instance_name, ''),
        v_process_id,
        v_host_name,
        'running',
        v_build_id,
        v_metadata
    )
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION record_worker_start_and_check_storm(
    p_mode TEXT,
    p_instance_name TEXT DEFAULT NULL,
    p_metadata JSONB DEFAULT '{}'::jsonb,
    p_max_starts INTEGER DEFAULT 5,
    p_window_seconds INTEGER DEFAULT 120,
    p_backoff_cap_seconds INTEGER DEFAULT 300
) RETURNS JSONB AS $$
DECLARE
    v_mode TEXT := lower(btrim(COALESCE(p_mode, 'unknown')));
    v_metadata JSONB := COALESCE(p_metadata, '{}'::jsonb);
    v_process_id INT;
    v_host_name TEXT;
    v_max_starts INTEGER := GREATEST(COALESCE(p_max_starts, 5), 1);
    v_window_seconds INTEGER := GREATEST(COALESCE(p_window_seconds, 120), 10);
    v_backoff_cap_seconds INTEGER := GREATEST(COALESCE(p_backoff_cap_seconds, 300), 10);
    v_recent_count INTEGER := 0;
    v_excess INTEGER := 0;
    v_backoff INTEGER := 0;
BEGIN
    IF v_mode NOT IN ('heartbeat', 'maintenance', 'both', 'channel', 'ingest', 'unknown') THEN
        v_mode := 'unknown';
    END IF;

    IF jsonb_typeof(v_metadata->'process_id') = 'number' THEN
        v_process_id := (v_metadata->>'process_id')::int;
    END IF;
    v_host_name := NULLIF(v_metadata->>'host_name', '');

    INSERT INTO worker_start_events (
        mode,
        instance_name,
        process_id,
        host_name,
        metadata
    ) VALUES (
        v_mode,
        NULLIF(p_instance_name, ''),
        v_process_id,
        v_host_name,
        v_metadata
    );

    DELETE FROM worker_start_events
    WHERE started_at < CURRENT_TIMESTAMP - INTERVAL '1 day';

    SELECT COUNT(*) INTO v_recent_count
    FROM worker_start_events
    WHERE mode = v_mode
      AND instance_name IS NOT DISTINCT FROM NULLIF(p_instance_name, '')
      AND started_at >= CURRENT_TIMESTAMP - make_interval(secs => v_window_seconds);

    IF v_recent_count > v_max_starts THEN
        v_excess := LEAST(v_recent_count - v_max_starts, 6);
        v_backoff := LEAST(v_backoff_cap_seconds, (5 * (2 ^ v_excess))::int);
    END IF;

    RETURN jsonb_build_object(
        'storm', v_backoff > 0,
        'count', v_recent_count,
        'max_starts', v_max_starts,
        'window_seconds', v_window_seconds,
        'backoff_seconds', v_backoff
    );
END;
$$ LANGUAGE plpgsql;


-- Reading jobs: due check, wake-up, claim and completion.
CREATE OR REPLACE FUNCTION ingestion_jobs_due(p_claim_timeout_s INT DEFAULT NULL) RETURNS INT
    LANGUAGE sql STABLE AS $$
    SELECT count(*)::int FROM ingestion_jobs
    WHERE (status = 'pending' AND next_attempt_at <= CURRENT_TIMESTAMP)
       OR (status = 'in_progress'
           AND claimed_at < CURRENT_TIMESTAMP - make_interval(
               secs => COALESCE(p_claim_timeout_s, get_config_int('ingest.job_claim_timeout_s'), 1800)));
$$;

CREATE OR REPLACE FUNCTION notify_ingestion_job_pending() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('ingestion_jobs', NEW.id::text);
    RETURN NULL;
END;
$$;


DROP TRIGGER IF EXISTS ingestion_jobs_notify_pending ON ingestion_jobs;
CREATE TRIGGER ingestion_jobs_notify_pending
    AFTER INSERT OR UPDATE OF status ON ingestion_jobs
    FOR EACH ROW WHEN (NEW.status = 'pending')
    EXECUTE FUNCTION notify_ingestion_job_pending();

CREATE OR REPLACE FUNCTION claim_ingestion_jobs(
    p_limit INT DEFAULT NULL,
    p_claim_timeout_s INT DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    lim INT := GREATEST(COALESCE(p_limit, get_config_int('ingest.job_batch_size'), 1), 1);
    timeout_s INT := COALESCE(p_claim_timeout_s, get_config_int('ingest.job_claim_timeout_s'), 1800);
    claimed JSONB;
BEGIN
    UPDATE ingestion_jobs
    SET status = CASE WHEN cancel_requested THEN 'cancelled' ELSE 'failed' END,
        error = CASE
            WHEN cancel_requested THEN COALESCE(error, 'cancelled')
            ELSE concat_ws('; ', error, format(
                'reading stopped without finishing on attempt %s of %s', attempts, max_attempts))
        END,
        completed_at = CURRENT_TIMESTAMP,
        updated_at = CURRENT_TIMESTAMP
    WHERE status = 'in_progress'
      AND claimed_at < CURRENT_TIMESTAMP - make_interval(secs => timeout_s)
      AND (cancel_requested OR attempts >= max_attempts);

    WITH candidate AS (
        SELECT id FROM ingestion_jobs
        WHERE (status = 'pending' AND next_attempt_at <= CURRENT_TIMESTAMP)
           OR (status = 'in_progress'
               AND claimed_at < CURRENT_TIMESTAMP - make_interval(secs => timeout_s))
        ORDER BY next_attempt_at, created_at
        LIMIT lim
        FOR UPDATE SKIP LOCKED
    ), updated AS (
        UPDATE ingestion_jobs j
        SET status = 'in_progress',
            attempts = j.attempts + 1,
            claimed_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        FROM candidate c
        WHERE j.id = c.id
        RETURNING j.*
    )
    SELECT COALESCE(jsonb_agg(to_jsonb(u)), '[]'::jsonb) INTO claimed FROM updated u;
    RETURN claimed;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION complete_ingestion_job(
    p_job_id UUID,
    p_result JSONB DEFAULT '{}'::jsonb
) RETURNS JSONB AS $$
    UPDATE ingestion_jobs
    SET status = CASE WHEN cancel_requested THEN 'cancelled' ELSE 'completed' END,
        result = COALESCE(p_result, '{}'::jsonb),
        error = NULL,
        completed_at = CURRENT_TIMESTAMP,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_job_id
    RETURNING jsonb_build_object('id', id, 'status', status);
$$ LANGUAGE sql;


-- A slowly read chunk with no facts is receipted too.
CREATE OR REPLACE FUNCTION slow_ingest_persist_facts(
    p_facts JSONB,
    p_assessment JSONB,
    p_source JSONB,
    p_encounter_id UUID DEFAULT NULL,
    p_connection_ids UUID[] DEFAULT ARRAY[]::UUID[],
    p_worldview_ids UUID[] DEFAULT ARRAY[]::UUID[],
    p_rejection_reason_ids UUID[] DEFAULT ARRAY[]::UUID[],
    p_context TEXT DEFAULT 'slow_ingest'
) RETURNS JSONB AS $$
DECLARE
    acceptance TEXT := COALESCE(p_assessment->>'acceptance', 'question');
    importance FLOAT := COALESCE((p_assessment->>'importance')::float, 0.5);
    impact TEXT := COALESCE(p_assessment->>'worldview_impact', 'neutral');
    valid_facts TEXT[];
    plan JSONB;
    planned JSONB;
    idx INT;
    fact TEXT;
    decision TEXT;
    matched_id UUID;
    memory_id UUID;
    other UUID;
    created_ids UUID[] := ARRAY[]::UUID[];
    corroborated INT := 0;
BEGIN
    valid_facts := ARRAY(
        SELECT trim(f) FROM jsonb_array_elements_text(COALESCE(p_facts, '[]'::jsonb)) f
        WHERE length(trim(f)) >= 10
    );
    IF cardinality(valid_facts) = 0 THEN
        -- A chunk read with nothing worth keeping is still read: receipt it,
        -- so a retry of the document does not read it again.
        IF p_source ? 'section_hash' THEN
            PERFORM record_ingestion_receipt(
                COALESCE(p_source->>'content_hash', p_source->>'ref'),
                p_source->>'section_hash',
                NULL,
                0,
                p_source->>'path');
        END IF;
        RETURN jsonb_build_object('created', '[]'::jsonb, 'corroborated', 0);
    END IF;

    plan := ingest_route_extractions(
        (SELECT jsonb_agg(jsonb_build_object('content', f, 'confidence',
                COALESCE((p_assessment->>'trust_assessment')::float, 0.5)))
         FROM unnest(valid_facts) f),
        0.0);

    FOR planned IN SELECT value FROM jsonb_array_elements(COALESCE(plan, '[]'::jsonb))
    LOOP
        idx := (planned->>'index')::int;
        fact := valid_facts[idx + 1];
        IF fact IS NULL THEN
            CONTINUE;
        END IF;
        decision := planned->>'decision';
        matched_id := NULLIF(planned->>'matched_memory_id', '')::uuid;

        IF decision = 'duplicate' AND matched_id IS NOT NULL THEN
            BEGIN
                PERFORM add_memory_evidence(
                    matched_id, 'supports', p_source, NULL, p_encounter_id, p_context);
                corroborated := corroborated + 1;
            EXCEPTION WHEN OTHERS THEN
                RAISE WARNING '% corroboration failed for %: %', p_context, matched_id, SQLERRM;
            END;
            CONTINUE;
        END IF;

        BEGIN
            memory_id := create_semantic_memory(
                fact,
                COALESCE((p_assessment->>'trust_assessment')::float, 0.5),
                ARRAY['ingested_fact'], ARRAY[]::text[],
                jsonb_build_array(p_source), importance, p_source, NULL);
            created_ids := created_ids || memory_id;

            IF p_encounter_id IS NOT NULL THEN
                PERFORM discover_relationship(memory_id, p_encounter_id,
                    'DERIVED_FROM'::graph_edge_type, 0.9, p_context);
            END IF;
            IF decision = 'related' AND matched_id IS NOT NULL THEN
                PERFORM discover_relationship(memory_id, matched_id,
                    'ASSOCIATED'::graph_edge_type, 0.6, p_context);
            END IF;
            FOREACH other IN ARRAY COALESCE(p_connection_ids, ARRAY[]::UUID[]) LOOP
                PERFORM discover_relationship(memory_id, other,
                    'ASSOCIATED'::graph_edge_type, 0.7, p_context);
            END LOOP;
            IF impact IN ('supports', 'contradicts') THEN
                FOREACH other IN ARRAY (COALESCE(p_worldview_ids, ARRAY[]::UUID[]))[1:3] LOOP
                    PERFORM discover_relationship(memory_id, other,
                        (CASE impact WHEN 'supports' THEN 'SUPPORTS' ELSE 'CONTRADICTS' END)::graph_edge_type,
                        0.7, p_context);
                END LOOP;
            END IF;
            IF acceptance = 'contest' THEN
                FOREACH other IN ARRAY COALESCE(p_rejection_reason_ids, ARRAY[]::UUID[]) LOOP
                    PERFORM discover_relationship(memory_id, other,
                        'CONTESTED_BECAUSE'::graph_edge_type, 0.8, p_context);
                END LOOP;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING '% fact persistence failed: %', p_context, SQLERRM;
        END;
    END LOOP;

    IF p_source ? 'section_hash' THEN
        PERFORM record_ingestion_receipt(
            COALESCE(p_source->>'content_hash', p_source->>'ref'),
            p_source->>'section_hash',
            NULL,
            COALESCE(array_length(created_ids, 1), 0),
            p_source->>'path');
    END IF;

    RETURN jsonb_build_object('created', to_jsonb(created_ids), 'corroborated', corroborated);
END;
$$ LANGUAGE plpgsql;


-- One document per imported connector item. The job runner used to ignore
-- the document the connector had stored and stored the text again under its
-- plain content hash. Keep the copy, which holds the chunks and memories,
-- under the connector's hash, path and attribution, and drop the empty row.
-- Pairs where the connector's row was read too are left alone.
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
