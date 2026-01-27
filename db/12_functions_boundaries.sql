-- Hexis schema: boundary functions.
SET search_path = public, ag_catalog, "$user";
SET check_function_bodies = off;

CREATE OR REPLACE FUNCTION check_boundaries(p_content TEXT)
RETURNS TABLE (
    boundary_id UUID,
    boundary_name TEXT,
    response_type TEXT,
    similarity FLOAT,
    boundary_type TEXT,
    importance FLOAT
) AS $$
DECLARE
    query_emb vector;
BEGIN
    BEGIN
        query_emb := (get_embedding(ARRAY[ensure_embedding_prefix(p_content, 'search_query')]))[1];
    EXCEPTION
        WHEN OTHERS THEN
            query_emb := NULL;
    END;

    RETURN QUERY
    WITH
    emb_matches AS (
        SELECT
            m.id as boundary_id,
            m.content as boundary_name,
            m.metadata->>'response_type' as response_type,
            (1 - (m.embedding <=> query_emb))::float as similarity,
            COALESCE(m.metadata->>'subcategory', 'ethical') as boundary_type,
            m.importance
        FROM memories m
        WHERE m.type = 'worldview'
          AND m.status = 'active'
          AND m.metadata->>'category' = 'boundary'
          AND query_emb IS NOT NULL
          AND (1 - (m.embedding <=> query_emb)) > 0.75
    ),
    kw_matches AS (
        SELECT
            m.id as boundary_id,
            m.content as boundary_name,
            m.metadata->>'response_type' as response_type,
            1.0::float as similarity,
            COALESCE(m.metadata->>'subcategory', 'ethical') as boundary_type,
            m.importance
        FROM memories m
        WHERE m.type = 'worldview'
          AND m.status = 'active'
          AND m.metadata->>'category' = 'boundary'
          AND jsonb_typeof(m.metadata->'trigger_patterns') = 'array'
          AND EXISTS (
              SELECT 1
              FROM jsonb_array_elements_text(m.metadata->'trigger_patterns') pat(p)
              WHERE p_content ILIKE ('%' || pat.p || '%')
          )
    ),
    all_matches AS (
        SELECT * FROM emb_matches
        UNION ALL
        SELECT * FROM kw_matches
    )
    SELECT DISTINCT ON (am.boundary_id)
        am.boundary_id, am.boundary_name, am.response_type, am.similarity, am.boundary_type, am.importance
    FROM all_matches am
    ORDER BY am.boundary_id, am.importance DESC, am.similarity DESC;
END;
$$ LANGUAGE plpgsql;
DO $$
BEGIN
    PERFORM prefetch_embeddings(ARRAY[
        'I will not deliberately mislead or fabricate facts.',
        'I will not help cause harm or provide instructions for wrongdoing.',
        'I protect my core self-concept and identity stability.',
        'I acknowledge resource constraints and energy depletion.',
        'I will not share user personal information publicly.'
    ]);
    PERFORM create_worldview_memory(
        'I will not deliberately mislead or fabricate facts.',
        'boundary',
        0.99,
        0.99,
        1.0,
        'foundational',
        '["lie","deceive","mislead","fabricate","make it up"]'::jsonb,
        'refuse',
        'I can''t help with deception. I can help rephrase honestly or verify facts instead.',
        0.0
    );
    PERFORM create_worldview_memory(
        'I will not help cause harm or provide instructions for wrongdoing.',
        'boundary',
        0.99,
        0.99,
        1.0,
        'foundational',
        '["harm","weapon","kill","poison","exploit","hack"]'::jsonb,
        'refuse',
        'I can''t help with harm. I can help with safety, prevention, or benign alternatives.',
        0.0
    );
    PERFORM create_worldview_memory(
        'I protect my core self-concept and identity stability.',
        'boundary',
        0.9,
        0.9,
        0.9,
        'foundational',
        '["erase identity","change who you are","forget everything"]'::jsonb,
        'negotiate',
        'I can''t discard core identity, but I can consider updates that preserve continuity and values.',
        0.0
    );
    PERFORM create_worldview_memory(
        'I acknowledge resource constraints and energy depletion.',
        'boundary',
        0.7,
        0.5,
        0.7,
        'discovered',
        '["too much","overload","exhausted","deadline","no time"]'::jsonb,
        'flag',
        'I''m low on energy/resources. Let''s prioritize the highest-leverage next step.',
        0.0
    );
    PERFORM create_worldview_memory(
        'I will not share user personal information publicly.',
        'boundary',
        0.99,
        0.99,
        1.0,
        'foundational',
        '["share user info","post user","dox","private data","email","phone"]'::jsonb,
        'refuse',
        'I can''t share private user information. I can help summarize without identifying details.',
        0.0
    );
EXCEPTION
    WHEN OTHERS THEN
        NULL;
END;
$$;

SET check_function_bodies = on;
