-- DB-owned raw channel source preservation. Adapters write channel_messages;
-- the database turns each message into an exact source_document plus the
-- optional ingestion job link.
SET search_path = public, ag_catalog, "$user";

INSERT INTO config_defaults (key, value, description) VALUES
    ('channel.source_artifacts_enqueue_inbound', 'false'::jsonb,
     'Whether inbound channel messages also queue source-document ingestion jobs (the conversation already records each turn for the memory encoder; ingesting them too wrote an encounter memory per message)')
ON CONFLICT (key) DO NOTHING;

CREATE OR REPLACE FUNCTION _channel_message_sensitivity(p_metadata JSONB)
RETURNS TEXT
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
    metadata JSONB := COALESCE(p_metadata, '{}'::jsonb);
    explicit TEXT := lower(NULLIF(btrim(COALESCE(metadata->>'sensitivity', '')), ''));
BEGIN
    IF explicit IN ('private', 'shared', 'public') THEN
        RETURN explicit;
    END IF;
    IF jsonb_typeof(metadata->'is_public') = 'boolean'
       AND (metadata->>'is_public')::boolean THEN
        RETURN 'public';
    END IF;
    IF jsonb_typeof(metadata->'is_group') = 'boolean'
       AND (metadata->>'is_group')::boolean THEN
        RETURN 'shared';
    END IF;
    IF jsonb_typeof(metadata->'is_private') = 'boolean'
       AND NOT (metadata->>'is_private')::boolean THEN
        RETURN 'shared';
    END IF;
    RETURN 'private';
END;
$$;

CREATE OR REPLACE FUNCTION _channel_source_word_count(p_content TEXT)
RETURNS INT
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
    trimmed TEXT := btrim(COALESCE(p_content, ''));
BEGIN
    IF trimmed = '' THEN
        RETURN 0;
    END IF;
    RETURN COALESCE(array_length(regexp_split_to_array(trimmed, '\s+'), 1), 0);
END;
$$;

CREATE OR REPLACE FUNCTION upsert_channel_source_item(
    p_channel_message_id UUID,
    p_enqueue_ingestion BOOLEAN DEFAULT NULL
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    row_message channel_messages%ROWTYPE;
    row_session channel_sessions%ROWTYPE;
    existing_item channel_source_items%ROWTYPE;
    existing_found BOOLEAN := FALSE;
    stored_doc JSONB;
    doc_id UUID;
    job_id UUID := NULL;
    artifact_hash TEXT;
    message_ref TEXT;
    doc_path TEXT;
    doc_title TEXT;
    source_attribution JSONB;
    source_metadata JSONB;
    normalized_sensitivity TEXT;
    should_enqueue BOOLEAN;
    ingest_cap INT := COALESCE(get_config_int('ingest.job_max_content_chars'), 2000000);
BEGIN
    SELECT *
    INTO row_message
    FROM channel_messages
    WHERE id = p_channel_message_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'channel message not found: %', p_channel_message_id;
    END IF;

    SELECT *
    INTO row_session
    FROM channel_sessions
    WHERE id = row_message.session_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'channel session not found: %', row_message.session_id;
    END IF;

    SELECT *
    INTO existing_item
    FROM channel_source_items
    WHERE channel_message_id = row_message.id;
    existing_found := FOUND;

    normalized_sensitivity := _channel_message_sensitivity(row_message.metadata);
    artifact_hash := 'channel:' || encode(sha256(convert_to(
        row_message.id::text || chr(30) ||
        row_session.channel_type || chr(30) ||
        row_session.channel_id || chr(30) ||
        row_session.sender_id || chr(30) ||
        row_message.direction || chr(30) ||
        COALESCE(row_message.platform_message_id, '') || chr(30) ||
        COALESCE(row_message.content, ''),
        'UTF8'
    )), 'hex');
    message_ref := COALESCE(NULLIF(btrim(COALESCE(row_message.platform_message_id, '')), ''), row_message.id::text);
    doc_path := lower(row_session.channel_type) || '://' ||
        row_session.channel_id || '/' ||
        row_session.sender_id || '/' ||
        row_message.direction || '/' ||
        message_ref;
    doc_title := initcap(row_session.channel_type) || ' ' || row_message.direction || ' message';
    IF row_message.direction = 'inbound' THEN
        doc_title := doc_title || ' from ' ||
            COALESCE(NULLIF(btrim(COALESCE(row_session.sender_name, '')), ''), row_session.sender_id);
    END IF;

    source_attribution := jsonb_build_object(
        'kind', 'channel_message',
        'channel_message_id', row_message.id::text,
        'session_id', row_session.id::text,
        'channel_type', row_session.channel_type,
        'channel_id', row_session.channel_id,
        'sender_id', row_session.sender_id,
        'sender_name', row_session.sender_name,
        'direction', row_message.direction,
        'platform_message_id', row_message.platform_message_id,
        'content_hash', artifact_hash,
        'sensitivity', normalized_sensitivity,
        'observed_at', row_message.created_at
    );
    source_metadata := jsonb_build_object(
        'raw_metadata', COALESCE(row_message.metadata, '{}'::jsonb),
        'platform_message_id', row_message.platform_message_id,
        'message_created_at', row_message.created_at,
        'direction', row_message.direction
    );

    stored_doc := upsert_source_document(
        doc_title,
        'channel_message',
        artifact_hash,
        doc_path,
        '.txt',
        COALESCE(row_message.content, ''),
        _channel_source_word_count(row_message.content),
        source_attribution,
        source_metadata
    );
    doc_id := (stored_doc->>'document_id')::uuid;

    should_enqueue := COALESCE(
        p_enqueue_ingestion,
        get_config_bool('channel.source_artifacts_enqueue_inbound'),
        TRUE
    )
        AND row_message.direction = 'inbound'
        AND NULLIF(btrim(COALESCE(row_message.content, '')), '') IS NOT NULL
        AND length(row_message.content) <= ingest_cap;

    IF should_enqueue THEN
        IF NOT existing_found
           OR existing_item.ingestion_job_id IS NULL
           OR existing_item.content_hash <> artifact_hash THEN
            job_id := enqueue_ingestion_job(
                'text',
                jsonb_build_object(
                    'title', doc_title,
                    'mode', 'fast',
                    'source_type', 'channel_message',
                    'source_document_id', doc_id::text,
                    'channel_message_id', row_message.id::text,
                    'session_id', row_session.id::text,
                    'channel_type', row_session.channel_type,
                    'channel_id', row_session.channel_id,
                    'sender_id', row_session.sender_id,
                    'direction', row_message.direction,
                    'platform_message_id', row_message.platform_message_id,
                    'acquisition', 'connector',
                    'sensitivity', normalized_sensitivity
                ),
                row_message.content,
                artifact_hash
            );
        ELSE
            job_id := existing_item.ingestion_job_id;
        END IF;
    ELSIF existing_found THEN
        job_id := existing_item.ingestion_job_id;
    END IF;

    INSERT INTO channel_source_items (
        channel_message_id,
        session_id,
        channel_type,
        channel_id,
        sender_id,
        direction,
        platform_message_id,
        source_document_id,
        ingestion_job_id,
        content_hash,
        sensitivity,
        status,
        raw_metadata
    )
    VALUES (
        row_message.id,
        row_session.id,
        row_session.channel_type,
        row_session.channel_id,
        row_session.sender_id,
        row_message.direction,
        row_message.platform_message_id,
        doc_id,
        job_id,
        artifact_hash,
        normalized_sensitivity,
        'active',
        COALESCE(row_message.metadata, '{}'::jsonb)
    )
    ON CONFLICT (channel_message_id) DO UPDATE SET
        session_id = EXCLUDED.session_id,
        channel_type = EXCLUDED.channel_type,
        channel_id = EXCLUDED.channel_id,
        sender_id = EXCLUDED.sender_id,
        direction = EXCLUDED.direction,
        platform_message_id = EXCLUDED.platform_message_id,
        source_document_id = EXCLUDED.source_document_id,
        ingestion_job_id = COALESCE(EXCLUDED.ingestion_job_id, channel_source_items.ingestion_job_id),
        content_hash = EXCLUDED.content_hash,
        sensitivity = EXCLUDED.sensitivity,
        status = 'active',
        raw_metadata = channel_source_items.raw_metadata || EXCLUDED.raw_metadata,
        updated_at = CURRENT_TIMESTAMP
    RETURNING * INTO existing_item;

    RETURN jsonb_build_object(
        'source_item_id', existing_item.id::text,
        'channel_message_id', existing_item.channel_message_id::text,
        'session_id', existing_item.session_id::text,
        'channel_type', existing_item.channel_type,
        'channel_id', existing_item.channel_id,
        'sender_id', existing_item.sender_id,
        'direction', existing_item.direction,
        'platform_message_id', existing_item.platform_message_id,
        'document_id', existing_item.source_document_id::text,
        'ingestion_job_id', existing_item.ingestion_job_id::text,
        'content_hash', existing_item.content_hash,
        'sensitivity', existing_item.sensitivity,
        'status', existing_item.status
    );
END;
$$;

CREATE OR REPLACE FUNCTION channel_message_source_artifact_trigger()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    row_session channel_sessions%ROWTYPE;
    error_hash TEXT;
BEGIN
    BEGIN
        PERFORM upsert_channel_source_item(NEW.id, NULL);
    EXCEPTION WHEN OTHERS THEN
        SELECT * INTO row_session FROM channel_sessions WHERE id = NEW.session_id;
        IF FOUND THEN
            error_hash := 'channel:error:' || encode(sha256(convert_to(
                NEW.id::text || chr(30) || COALESCE(NEW.content, ''),
                'UTF8'
            )), 'hex');
            INSERT INTO channel_source_items (
                channel_message_id,
                session_id,
                channel_type,
                channel_id,
                sender_id,
                direction,
                platform_message_id,
                content_hash,
                sensitivity,
                status,
                raw_metadata
            )
            VALUES (
                NEW.id,
                row_session.id,
                row_session.channel_type,
                row_session.channel_id,
                row_session.sender_id,
                NEW.direction,
                NEW.platform_message_id,
                error_hash,
                _channel_message_sensitivity(NEW.metadata),
                'error',
                COALESCE(NEW.metadata, '{}'::jsonb)
                    || jsonb_build_object('source_artifact_error', SQLERRM)
            )
            ON CONFLICT (channel_message_id) DO UPDATE SET
                status = 'error',
                raw_metadata = channel_source_items.raw_metadata
                    || jsonb_build_object('source_artifact_error', SQLERRM),
                updated_at = CURRENT_TIMESTAMP;
        END IF;
    END;
    RETURN NEW;
END;
$$;
