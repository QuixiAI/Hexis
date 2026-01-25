-- Hexis schema: helper functions.
SET search_path = public, ag_catalog, "$user";
SET check_function_bodies = off;

CREATE OR REPLACE FUNCTION age_in_days(ts TIMESTAMPTZ) 
RETURNS FLOAT
LANGUAGE sql
STABLE
AS $$
    SELECT EXTRACT(EPOCH FROM (NOW() - ts)) / 86400.0;
$$;
CREATE OR REPLACE FUNCTION calculate_relevance(
    p_importance FLOAT,
    p_decay_rate FLOAT,
    p_created_at TIMESTAMPTZ,
    p_last_accessed TIMESTAMPTZ
) RETURNS FLOAT
LANGUAGE sql
STABLE
AS $$
    SELECT p_importance * EXP(
        -p_decay_rate * LEAST(
            age_in_days(p_created_at),
            COALESCE(age_in_days(p_last_accessed), age_in_days(p_created_at)) * 0.5
        )
    );
$$;
CREATE OR REPLACE FUNCTION ensure_embedding_prefix(
    p_text TEXT,
    p_prefix TEXT DEFAULT 'search_document'
) RETURNS TEXT AS $$
DECLARE
    normalized_prefix TEXT;
BEGIN
    IF p_text IS NULL THEN
        RETURN NULL;
    END IF;
    IF p_text ~* '^\s*(search_document|search_query|clustering|classification)\s*:' THEN
        RETURN ltrim(p_text);
    END IF;
    normalized_prefix := NULLIF(btrim(p_prefix), '');
    IF normalized_prefix IS NULL THEN
        RETURN p_text;
    END IF;
    RETURN normalized_prefix || ': ' || p_text;
END;
$$ LANGUAGE plpgsql IMMUTABLE;
CREATE OR REPLACE FUNCTION get_embedding(text_content TEXT)
RETURNS vector AS $$
	DECLARE
	    service_url TEXT;
	    response http_response;
	    request_body TEXT;
	    embedding_array FLOAT[];
	    embedding_json JSONB;
	    v_content_hash TEXT;
	    v_alt_hash TEXT;
	    cached_embedding vector;
	    expected_dim INT;
	    effective_text TEXT;
	    stripped_text TEXT;
	    start_ts TIMESTAMPTZ;
	    retry_seconds INT;
	    retry_interval_seconds FLOAT;
	    last_error TEXT;
	BEGIN
	    PERFORM sync_embedding_dimension_config();
	    expected_dim := embedding_dimension();
	    effective_text := ensure_embedding_prefix(text_content, 'search_document');
	    v_content_hash := encode(sha256(text_content::bytea), 'hex');
	    SELECT ec.embedding INTO cached_embedding
	    FROM embedding_cache ec
	    WHERE ec.content_hash = v_content_hash;

	    IF FOUND THEN
	        RETURN cached_embedding;
	    END IF;
	    IF text_content ~* '^\s*(search_document|search_query|clustering|classification)\s*:' THEN
	        stripped_text := regexp_replace(
	            text_content,
	            '^\s*(search_document|search_query|clustering|classification)\s*:\s*',
	            '',
	            'i'
	        );
	        v_alt_hash := encode(sha256(stripped_text::bytea), 'hex');
	        IF v_alt_hash <> v_content_hash THEN
	            SELECT ec.embedding INTO cached_embedding
	            FROM embedding_cache ec
	            WHERE ec.content_hash = v_alt_hash;
	            IF FOUND THEN
	                RETURN cached_embedding;
	            END IF;
	        END IF;
	    END IF;
	    service_url := (SELECT CASE WHEN jsonb_typeof(value) = 'string' THEN value #>> '{}' ELSE value::text END FROM config WHERE key = 'embedding.service_url');
	    request_body := json_build_object('inputs', effective_text)::TEXT;
	    retry_seconds := COALESCE(
	        (SELECT (value #>> '{}')::int FROM config WHERE key = 'embedding.retry_seconds'),
	        30
	    );
	    retry_interval_seconds := COALESCE(
	        (SELECT (value #>> '{}')::float FROM config WHERE key = 'embedding.retry_interval_seconds'),
	        1.0
	    );
	    start_ts := clock_timestamp();

	    LOOP
	        BEGIN
	            SELECT * INTO response FROM http_post(
	                service_url,
	                request_body,
	                'application/json'
	            );

	            IF response.status = 200 THEN
	                EXIT;
	            END IF;
	            IF response.status IN (400, 401, 403, 404, 422) THEN
	                RAISE EXCEPTION 'Embedding service error: % - %', response.status, response.content;
	            END IF;

	            last_error := format('status %s: %s', response.status, left(COALESCE(response.content, ''), 500));
	        EXCEPTION
	            WHEN OTHERS THEN
	                last_error := SQLERRM;
	        END;

	        IF retry_seconds <= 0 OR clock_timestamp() - start_ts >= (retry_seconds || ' seconds')::interval THEN
	            RAISE EXCEPTION 'Embedding service not available after % seconds: %', retry_seconds, COALESCE(last_error, '<unknown>');
	        END IF;

	        PERFORM pg_sleep(GREATEST(0.0, retry_interval_seconds));
	    END LOOP;
	    embedding_json := response.content::JSONB;
    IF embedding_json ? 'embeddings' THEN
        embedding_array := ARRAY(
            SELECT jsonb_array_elements_text((embedding_json->'embeddings')->0)::FLOAT
        );
    ELSIF embedding_json ? 'embedding' THEN
        embedding_array := ARRAY(
            SELECT jsonb_array_elements_text(embedding_json->'embedding')::FLOAT
        );
    ELSIF embedding_json ? 'data' THEN
        embedding_array := ARRAY(
            SELECT jsonb_array_elements_text((embedding_json->'data')->0->'embedding')::FLOAT
        );
    ELSIF jsonb_typeof(embedding_json->0) = 'array' THEN
        embedding_array := ARRAY(
            SELECT jsonb_array_elements_text(embedding_json->0)::FLOAT
        );
    ELSE
        embedding_array := ARRAY(
            SELECT jsonb_array_elements_text(embedding_json)::FLOAT
        );
	    END IF;
	    IF array_length(embedding_array, 1) IS NULL OR array_length(embedding_array, 1) != expected_dim THEN
	        RAISE EXCEPTION 'Invalid embedding dimension: expected %, got %', expected_dim, array_length(embedding_array, 1);
	    END IF;
	    INSERT INTO embedding_cache (content_hash, embedding)
	    VALUES (v_content_hash, embedding_array::vector)
	    ON CONFLICT DO NOTHING;
	
	    RETURN embedding_array::vector;
	EXCEPTION
	    WHEN OTHERS THEN
	        RAISE EXCEPTION 'Failed to get embedding: %', SQLERRM;
	END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION check_embedding_service_health()
RETURNS BOOLEAN AS $$
DECLARE
    service_url TEXT;
    health_url TEXT;
    response http_response;
BEGIN
    service_url := (SELECT CASE WHEN jsonb_typeof(value) = 'string' THEN value #>> '{}' ELSE value::text END FROM config WHERE key = 'embedding.service_url');
    health_url := regexp_replace(service_url, '^(https?://[^/]+).*$', '\1/health');

    SELECT * INTO response FROM http_get(health_url);

    RETURN response.status = 200;
EXCEPTION
    WHEN OTHERS THEN
        RETURN FALSE;
END;
$$ LANGUAGE plpgsql;

SET check_function_bodies = on;
