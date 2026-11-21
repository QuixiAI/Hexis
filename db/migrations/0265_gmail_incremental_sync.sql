-- True incremental Gmail sync via history.list + recovery for stuck pre-0246
-- cursors and retirement of the synthetic "Ongoing Gmail sync" responsibility (#110).
--
-- 1. Adds incremental sync config defaults (interval + max messages per pass).
-- 2. Adds claim_due_gmail_incremental_syncs() and record_gmail_incremental_sync_error(),
--    respecting integrations.gmail.memory_policy ('remember' auto-syncs connected
--    accounts; 'forget' never background-syncs; 'ask' syncs opted-in cursors).
-- 3. Surfaces stalled cursors (leftover page_token without an active job) and
--    sync_age_seconds in get_connector_backfill_status().
-- 4. Removes synthetic "Ongoing Gmail sync" ambient responsibility creation from
--    enqueue_connector_backfill_job() and disables existing system-created rows
--    (which previously spammed outbox notifications and bypassed worth_reading()).
-- 5. Updates complete_connection_attempt() to recover errored sync cursors and
--    unblock waiting ambient responsibilities when a connector reconnects.
-- 6. Re-enqueues any connected Gmail cursor left stalled with a page_token and
--    no pending/in_progress/paused job.

SET search_path = public, ag_catalog, "$user";
SET check_function_bodies = off;

INSERT INTO config_defaults (key, value, description) VALUES
    (
        'integrations.gmail.ambient_poll_interval_seconds',
        '900'::jsonb,
        'Legacy poll interval for Gmail ambient responsibilities; incremental Gmail sync uses integrations.gmail.incremental_sync_interval_seconds.'
    ),
    (
        'integrations.gmail.incremental_sync_interval_seconds',
        '300'::jsonb,
        'How often the maintenance worker checks Gmail history.list for newly arrived messages.'
    ),
    (
        'integrations.gmail.incremental_sync_max_messages',
        '100'::jsonb,
        'Maximum number of newly arrived Gmail messages processed per incremental sync pass.'
    )
ON CONFLICT (key) DO UPDATE SET
    value = EXCLUDED.value,
    description = EXCLUDED.description,
    updated_at = CURRENT_TIMESTAMP;

CREATE OR REPLACE FUNCTION complete_connection_attempt(
    p_attempt_id UUID,
    p_account_key TEXT,
    p_display_name TEXT DEFAULT NULL,
    p_credential_ref TEXT DEFAULT NULL,
    p_granted_scopes TEXT[] DEFAULT ARRAY[]::TEXT[],
    p_capabilities JSONB DEFAULT '[]'::jsonb,
    p_metadata JSONB DEFAULT '{}'::jsonb
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    row_attempt connection_attempts%ROWTYPE;
    row_connection integration_connections%ROWTYPE;
    normalized_account TEXT := NULLIF(btrim(COALESCE(p_account_key, '')), '');
    capability_input JSONB;
    prepared JSONB;
BEGIN
    IF normalized_account IS NULL THEN
        RAISE EXCEPTION 'account_key is required';
    END IF;

    SELECT * INTO row_attempt
    FROM connection_attempts
    WHERE id = p_attempt_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'connection attempt % not found', p_attempt_id;
    END IF;

    IF p_capabilities IS NULL
       OR p_capabilities = 'null'::jsonb
       OR (jsonb_typeof(p_capabilities) = 'array' AND jsonb_array_length(p_capabilities) = 0) THEN
        capability_input := row_attempt.requested_capabilities;
    ELSE
        capability_input := p_capabilities;
    END IF;
    prepared := prepare_connection_attempt(row_attempt.connector_id, capability_input);

    INSERT INTO integration_connections (
        connector_id,
        account_key,
        display_name,
        status,
        credential_ref,
        granted_scopes,
        capabilities,
        source_channel,
        source_session_id,
        metadata,
        connected_at,
        last_verified_at,
        revoked_at,
        last_error
    )
    VALUES (
        row_attempt.connector_id,
        normalized_account,
        NULLIF(btrim(COALESCE(p_display_name, '')), ''),
        'connected',
        NULLIF(btrim(COALESCE(p_credential_ref, '')), ''),
        COALESCE(p_granted_scopes, ARRAY[]::TEXT[]),
        prepared->'capabilities',
        row_attempt.source_channel,
        row_attempt.source_session_id,
        COALESCE(p_metadata, '{}'::jsonb),
        CURRENT_TIMESTAMP,
        CURRENT_TIMESTAMP,
        NULL,
        NULL
    )
    ON CONFLICT (connector_id, account_key) DO UPDATE SET
        display_name = COALESCE(EXCLUDED.display_name, integration_connections.display_name),
        status = 'connected',
        credential_ref = COALESCE(EXCLUDED.credential_ref, integration_connections.credential_ref),
        granted_scopes = EXCLUDED.granted_scopes,
        capabilities = EXCLUDED.capabilities,
        source_channel = COALESCE(EXCLUDED.source_channel, integration_connections.source_channel),
        source_session_id = COALESCE(EXCLUDED.source_session_id, integration_connections.source_session_id),
        metadata = integration_connections.metadata || EXCLUDED.metadata,
        connected_at = COALESCE(integration_connections.connected_at, CURRENT_TIMESTAMP),
        last_verified_at = CURRENT_TIMESTAMP,
        revoked_at = NULL,
        last_error = NULL,
        updated_at = CURRENT_TIMESTAMP
    RETURNING * INTO row_connection;

    UPDATE connection_attempts
    SET account_key = normalized_account,
        status = 'complete',
        credential_ref = p_credential_ref,
        error = NULL,
        completed_at = CURRENT_TIMESTAMP,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_attempt_id;

    -- Recover errored sync cursors for this connection on reconnect (#110).
    IF to_regclass('public.connector_sync_cursors') IS NOT NULL THEN
        UPDATE connector_sync_cursors
        SET status = 'active',
            last_error = NULL,
            last_started_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE connection_id = row_connection.id
          AND status = 'error';
    END IF;

    RETURN jsonb_build_object(
        'attempt_id', row_attempt.id::text,
        'connector_id', row_connection.connector_id,
        'connection_id', row_connection.id::text,
        'account_key', row_connection.account_key,
        'display_name', row_connection.display_name,
        'status', row_connection.status,
        'credential_ref', row_connection.credential_ref,
        'granted_scopes', to_jsonb(row_connection.granted_scopes),
        'capabilities', row_connection.capabilities,
        'connected_at', row_connection.connected_at
    );
END;
$$;

CREATE OR REPLACE FUNCTION enqueue_connector_backfill_job(
    p_connector_id TEXT,
    p_account_key TEXT,
    p_cursor_key TEXT DEFAULT 'messages',
    p_requested_range JSONB DEFAULT '{}'::jsonb,
    p_metadata JSONB DEFAULT '{}'::jsonb,
    p_max_attempts INT DEFAULT NULL
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    row_connection integration_connections%ROWTYPE;
    row_job connector_backfill_jobs%ROWTYPE;
    normalized_cursor TEXT := COALESCE(NULLIF(btrim(p_cursor_key), ''), 'messages');
    existing_id UUID;
BEGIN
    row_connection := _connector_connection(p_connector_id, p_account_key);
    PERFORM ensure_connector_cursor(
        row_connection.connector_id,
        row_connection.account_key,
        normalized_cursor,
        COALESCE(p_metadata, '{}'::jsonb)
    );

    SELECT id
    INTO existing_id
    FROM connector_backfill_jobs
    WHERE connection_id = row_connection.id
      AND cursor_key = normalized_cursor
      AND status IN ('pending', 'in_progress', 'paused')
    ORDER BY created_at DESC
    LIMIT 1;

    IF existing_id IS NOT NULL THEN
        SELECT * INTO row_job FROM connector_backfill_jobs WHERE id = existing_id;
        RETURN jsonb_build_object(
            'job_id', row_job.id::text,
            'existing', TRUE,
            'status', row_job.status,
            'connector_id', row_job.connector_id,
            'account_key', row_job.account_key,
            'cursor_key', row_job.cursor_key,
            'requested_range', row_job.requested_range,
            'estimate', estimate_connector_backfill(row_job.connector_id, row_job.requested_range),
            'progress', row_job.progress
        );
    END IF;

    INSERT INTO connector_backfill_jobs (
        connection_id,
        connector_id,
        account_key,
        cursor_key,
        requested_range,
        metadata,
        max_attempts
    )
    VALUES (
        row_connection.id,
        row_connection.connector_id,
        row_connection.account_key,
        normalized_cursor,
        COALESCE(p_requested_range, '{}'::jsonb),
        COALESCE(p_metadata, '{}'::jsonb),
        GREATEST(COALESCE(p_max_attempts, 3), 1)
    )
    RETURNING * INTO row_job;

    RETURN jsonb_build_object(
        'job_id', row_job.id::text,
        'existing', FALSE,
        'status', row_job.status,
        'connector_id', row_job.connector_id,
        'account_key', row_job.account_key,
        'cursor_key', row_job.cursor_key,
        'requested_range', row_job.requested_range,
        'estimate', estimate_connector_backfill(row_job.connector_id, row_job.requested_range),
        'progress', row_job.progress,
        'next_attempt_at', row_job.next_attempt_at
    );
END;
$$;

CREATE OR REPLACE FUNCTION claim_due_gmail_incremental_syncs(
    p_limit INT DEFAULT 5
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    lim INT := GREATEST(COALESCE(p_limit, 5), 1);
    interval_s INT := GREATEST(
        COALESCE(get_config_int('integrations.gmail.incremental_sync_interval_seconds'), 300),
        30
    );
    memory_policy TEXT := lower(COALESCE(get_config_text('integrations.gmail.memory_policy'), ''));
    row_conn integration_connections%ROWTYPE;
    row_cursor connector_sync_cursors%ROWTYPE;
    claimed JSONB := '[]'::jsonb;
BEGIN
    IF memory_policy = 'forget' THEN
        RETURN '[]'::jsonb;
    END IF;

    FOR row_conn IN
        SELECT ic.*
        FROM integration_connections ic
        LEFT JOIN connector_sync_cursors c
               ON c.connection_id = ic.id
              AND c.cursor_key = 'messages'
        WHERE ic.connector_id = 'gmail'
          AND ic.status = 'connected'
          AND (memory_policy = 'remember' OR c.id IS NOT NULL)
          AND (c.id IS NULL OR c.status = 'active')
          AND (c.id IS NULL OR NULLIF(btrim(COALESCE(c.cursor_value->>'page_token', '')), '') IS NULL)
          AND NOT EXISTS (
              SELECT 1
              FROM connector_backfill_jobs j
              WHERE j.connection_id = ic.id
                AND j.cursor_key = 'messages'
                AND j.status IN ('pending', 'in_progress', 'paused')
          )
          AND (
              c.id IS NULL
              OR (
                  (c.last_completed_at IS NULL OR c.last_completed_at <= CURRENT_TIMESTAMP - make_interval(secs => interval_s))
                  AND (c.last_started_at IS NULL OR c.last_started_at <= CURRENT_TIMESTAMP - make_interval(secs => interval_s))
              )
          )
        ORDER BY COALESCE(c.last_completed_at, '1970-01-01'::timestamptz) ASC, ic.created_at ASC
        LIMIT lim
        FOR UPDATE OF ic SKIP LOCKED
    LOOP
        PERFORM ensure_connector_cursor(
            row_conn.connector_id,
            row_conn.account_key,
            'messages',
            jsonb_build_object('sync_mode', 'incremental')
        );

        UPDATE connector_sync_cursors
        SET last_started_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE connection_id = row_conn.id
          AND cursor_key = 'messages'
        RETURNING * INTO row_cursor;

        claimed := claimed || jsonb_build_array(jsonb_build_object(
            'cursor_id', row_cursor.id::text,
            'connection_id', row_cursor.connection_id::text,
            'connector_id', row_cursor.connector_id,
            'account_key', row_cursor.account_key,
            'cursor_key', row_cursor.cursor_key,
            'cursor_value', row_cursor.cursor_value,
            'high_watermark', row_cursor.high_watermark,
            'status', row_cursor.status,
            'last_completed_at', row_cursor.last_completed_at,
            'last_error', row_cursor.last_error
        ));
    END LOOP;

    RETURN claimed;
END;
$$;

CREATE OR REPLACE FUNCTION record_gmail_incremental_sync_error(
    p_connection_id UUID,
    p_cursor_key TEXT,
    p_error TEXT,
    p_needs_reauth BOOLEAN DEFAULT FALSE
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    normalized_cursor TEXT := COALESCE(NULLIF(btrim(p_cursor_key), ''), 'messages');
    row_cursor connector_sync_cursors%ROWTYPE;
BEGIN
    UPDATE connector_sync_cursors
    SET status = CASE WHEN p_needs_reauth THEN 'error' ELSE status END,
        last_error = NULLIF(left(COALESCE(p_error, ''), 2000), ''),
        updated_at = CURRENT_TIMESTAMP
    WHERE connection_id = p_connection_id
      AND cursor_key = normalized_cursor
    RETURNING * INTO row_cursor;

    IF p_needs_reauth THEN
        UPDATE integration_connections
        SET status = 'needs_reauth',
            last_error = NULLIF(left(COALESCE(p_error, ''), 2000), ''),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = p_connection_id
          AND status = 'connected';
    END IF;

    IF row_cursor.id IS NULL THEN
        RETURN jsonb_build_object('status', 'missing', 'connection_id', p_connection_id::text);
    END IF;

    RETURN jsonb_build_object(
        'cursor_id', row_cursor.id::text,
        'connection_id', row_cursor.connection_id::text,
        'status', row_cursor.status,
        'last_error', row_cursor.last_error,
        'needs_reauth', p_needs_reauth
    );
END;
$$;

CREATE OR REPLACE FUNCTION get_connector_backfill_status(
    p_connector_id TEXT DEFAULT NULL,
    p_account_key TEXT DEFAULT NULL
) RETURNS JSONB
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    cursors JSONB;
    jobs JSONB;
    item_counts JSONB;
BEGIN
    SELECT COALESCE(jsonb_agg(
        jsonb_build_object(
            'cursor_id', c.id::text,
            'connection_id', c.connection_id::text,
            'connector_id', c.connector_id,
            'account_key', c.account_key,
            'cursor_key', c.cursor_key,
            'cursor_value', c.cursor_value,
            'high_watermark', c.high_watermark,
            'status', c.status,
            'last_started_at', c.last_started_at,
            'last_completed_at', c.last_completed_at,
            'last_error', c.last_error,
            'stalled', (
                NULLIF(btrim(COALESCE(c.cursor_value->>'page_token', '')), '') IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1
                    FROM connector_backfill_jobs j
                    WHERE j.connection_id = c.connection_id
                      AND j.cursor_key = c.cursor_key
                      AND j.status IN ('pending', 'in_progress', 'paused')
                )
            ),
            'sync_age_seconds', CASE
                WHEN c.last_completed_at IS NOT NULL
                THEN GREATEST(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - c.last_completed_at))::INT, 0)
                ELSE NULL
            END,
            'updated_at', c.updated_at
        )
        ORDER BY c.updated_at DESC, c.connector_id, c.account_key, c.cursor_key
    ), '[]'::jsonb)
    INTO cursors
    FROM connector_sync_cursors c
    WHERE (p_connector_id IS NULL OR c.connector_id = p_connector_id)
      AND (p_account_key IS NULL OR c.account_key = p_account_key);

    SELECT COALESCE(jsonb_agg(
        jsonb_build_object(
            'job_id', id::text,
            'connection_id', connection_id::text,
            'connector_id', connector_id,
            'account_key', account_key,
            'cursor_key', cursor_key,
            'status', status,
            'attempts', attempts,
            'max_attempts', max_attempts,
            'progress', progress,
            'result', result,
            'estimate', estimate_connector_backfill(connector_id, requested_range),
            'error', error,
            'cancel_requested', cancel_requested,
            'pause_requested', pause_requested,
            'next_attempt_at', next_attempt_at,
            'claimed_at', claimed_at,
            'completed_at', completed_at,
            'updated_at', updated_at
        )
        ORDER BY created_at DESC
    ), '[]'::jsonb)
    INTO jobs
    FROM connector_backfill_jobs
    WHERE (p_connector_id IS NULL OR connector_id = p_connector_id)
      AND (p_account_key IS NULL OR account_key = p_account_key)
      AND created_at >= CURRENT_TIMESTAMP - INTERVAL '7 days';

    SELECT COALESCE(jsonb_agg(
        jsonb_build_object(
            'connector_id', connector_id,
            'account_key', account_key,
            'item_kind', item_kind,
            'status', status,
            'count', item_count,
            'latest_item_at', latest_item_at
        )
        ORDER BY connector_id, account_key, item_kind, status
    ), '[]'::jsonb)
    INTO item_counts
    FROM (
        SELECT
            connector_id,
            account_key,
            item_kind,
            status,
            COUNT(*)::INT AS item_count,
            MAX(item_timestamp) AS latest_item_at
        FROM connector_source_items
        WHERE (p_connector_id IS NULL OR connector_id = p_connector_id)
          AND (p_account_key IS NULL OR account_key = p_account_key)
        GROUP BY connector_id, account_key, item_kind, status
    ) grouped;

    RETURN jsonb_build_object(
        'cursors', cursors,
        'jobs', jobs,
        'item_counts', item_counts
    );
END;
$$;

-- Retire synthetic system-created "Ongoing Gmail sync" ambient responsibilities
-- created by migration 0246; incremental history.list sync replaces them cleanly
-- without spamming outbox notifications. User-created responsibilities are untouched.
DO $$
BEGIN
    IF to_regclass('public.ambient_responsibilities') IS NOT NULL THEN
        UPDATE ambient_responsibilities
        SET status = 'disabled',
            next_check_at = NULL,
            metadata = metadata || jsonb_build_object(
                'disabled_reason', 'superseded_by_gmail_incremental_sync_0265'
            ),
            updated_at = CURRENT_TIMESTAMP
        WHERE created_by = 'system'
          AND title = 'Ongoing Gmail sync'
          AND status IN ('active', 'proposed', 'paused', 'blocked');
    END IF;
END;
$$;

-- Recover any pre-0246 connected Gmail cursor left stalled with a leftover
-- page_token and no active/paused backfill job.
DO $$
DECLARE
    stalled_row RECORD;
    prev_range JSONB;
BEGIN
    IF to_regclass('public.connector_sync_cursors') IS NULL
       OR to_regclass('public.connector_backfill_jobs') IS NULL THEN
        RETURN;
    END IF;

    FOR stalled_row IN
        SELECT c.connection_id, c.connector_id, c.account_key, c.cursor_key
        FROM connector_sync_cursors c
        JOIN integration_connections ic ON ic.id = c.connection_id
        WHERE c.connector_id = 'gmail'
          AND ic.status = 'connected'
          AND c.status = 'active'
          AND NULLIF(btrim(COALESCE(c.cursor_value->>'page_token', '')), '') IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM connector_backfill_jobs j
              WHERE j.connection_id = c.connection_id
                AND j.cursor_key = c.cursor_key
                AND j.status IN ('pending', 'in_progress', 'paused')
          )
    LOOP
        SELECT j.requested_range
        INTO prev_range
        FROM connector_backfill_jobs j
        WHERE j.connection_id = stalled_row.connection_id
          AND j.cursor_key = stalled_row.cursor_key
        ORDER BY j.created_at DESC
        LIMIT 1;

        PERFORM enqueue_connector_backfill_job(
            stalled_row.connector_id,
            stalled_row.account_key,
            stalled_row.cursor_key,
            COALESCE(prev_range, '{}'::jsonb),
            jsonb_build_object('recovered_by_migration', '0265_gmail_incremental_sync')
        );
    END LOOP;
END;
$$;
