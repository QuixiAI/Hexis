-- Hexis schema: tip-of-tongue and partial activation functions.
SET search_path = public, ag_catalog, "$user";
SET check_function_bodies = off;

CREATE OR REPLACE FUNCTION find_partial_activations(
    p_query_text TEXT,
    p_cluster_threshold FLOAT DEFAULT 0.7,
    p_memory_threshold FLOAT DEFAULT 0.5
)
RETURNS TABLE (
    cluster_id UUID,
    cluster_name TEXT,
    keywords TEXT[],
    emotional_signature JSONB,
    cluster_similarity FLOAT,
    best_memory_similarity FLOAT
) AS $$
DECLARE
    query_embedding vector;
BEGIN
    BEGIN
        query_embedding := (get_embedding(ARRAY[ensure_embedding_prefix(p_query_text, 'search_query')]))[1];
    EXCEPTION
        WHEN OTHERS THEN
            query_embedding := NULL;
    END;
    IF query_embedding IS NULL THEN
        RETURN;
    END IF;

    RETURN QUERY
    SELECT
        mc.id,
        mc.name,
        ARRAY[]::TEXT[] as keywords,
        NULL::JSONB as emotional_signature,
        (1 - (mc.centroid_embedding <=> query_embedding))::float as cluster_sim,
        MAX((1 - (m.embedding <=> query_embedding))::float) as best_mem_sim
    FROM clusters mc
    JOIN get_cluster_members_graph(mc.id) gcm ON TRUE
    JOIN memories m ON gcm.memory_id = m.id
    WHERE m.status = 'active'
      AND mc.centroid_embedding IS NOT NULL
    GROUP BY mc.id, mc.name, mc.centroid_embedding
    HAVING
        (1 - (mc.centroid_embedding <=> query_embedding)) >= p_cluster_threshold
        AND MAX(1 - (m.embedding <=> query_embedding)) < p_memory_threshold;
END;
$$ LANGUAGE plpgsql;

SET check_function_bodies = on;
