-- Hexis schema: reflect pipeline functions.
SET search_path = public, ag_catalog, "$user";
SET check_function_bodies = off;

CREATE OR REPLACE FUNCTION process_reflection_result(
    p_heartbeat_id UUID,
    p_result JSONB
)
RETURNS VOID AS $$
	DECLARE
    insight JSONB;
    ident JSONB;
    wupd JSONB;
    rel JSONB;
    contra JSONB;
    selfupd JSONB;
    content TEXT;
    conf FLOAT;
    category TEXT;
    aspect_type TEXT;
    change_text TEXT;
    reason_text TEXT;
	    wid UUID;
	    new_conf FLOAT;
	    winf JSONB;
	    wmem UUID;
	    wstrength FLOAT;
	    wtype TEXT;
	    from_id UUID;
	    to_id UUID;
	    rel_type graph_edge_type;
	    rel_conf FLOAT;
    ma UUID;
    mb UUID;
    sm_kind TEXT;
    sm_concept TEXT;
    sm_strength FLOAT;
    sm_evidence UUID;
    ident_embedding vector;
    ident_existing_id UUID;
    ident_similarity FLOAT;
    ident_conf FLOAT;
    ident_stability FLOAT;
BEGIN
    IF p_result IS NULL THEN
        RETURN;
    END IF;

    IF p_result ? 'insights' THEN
        FOR insight IN SELECT * FROM jsonb_array_elements(COALESCE(p_result->'insights', '[]'::jsonb))
        LOOP
            content := COALESCE(insight->>'content', '');
            IF content <> '' THEN
                conf := COALESCE((insight->>'confidence')::float, 0.7);
                category := COALESCE(insight->>'category', 'pattern');
                PERFORM create_semantic_memory(
                    content,
                    conf,
                    ARRAY['reflection', category],
                    NULL,
                    jsonb_build_object('heartbeat_id', p_heartbeat_id, 'source', 'reflect'),
                    0.6
                );
            END IF;
        END LOOP;
    END IF;
    IF p_result ? 'identity_updates' THEN
        FOR ident IN SELECT * FROM jsonb_array_elements(COALESCE(p_result->'identity_updates', '[]'::jsonb))
        LOOP
            aspect_type := COALESCE(ident->>'aspect_type', '');
            change_text := COALESCE(ident->>'change', '');
            reason_text := COALESCE(ident->>'reason', '');
            IF aspect_type <> '' AND change_text <> '' THEN
                ident_existing_id := NULL;
                ident_similarity := NULL;
                ident_conf := NULL;
                ident_stability := NULL;
                BEGIN
                    ident_embedding := (get_embedding(ARRAY[change_text]))[1];
                    IF ident_embedding IS NOT NULL THEN
                        SELECT m.id,
                               (m.metadata->>'confidence')::float,
                               (m.metadata->>'stability')::float,
                               (1 - (m.embedding <=> ident_embedding))::float
                        INTO ident_existing_id, ident_conf, ident_stability, ident_similarity
                        FROM memories m
                        WHERE m.type = 'worldview'
                          AND m.status = 'active'
                          AND m.metadata->>'category' = 'self'
                          AND m.embedding IS NOT NULL
                        ORDER BY m.embedding <=> ident_embedding
                        LIMIT 1;
                    END IF;
                EXCEPTION
                    WHEN OTHERS THEN
                        ident_existing_id := NULL;
                END;

                IF ident_existing_id IS NOT NULL AND COALESCE(ident_similarity, 0.0) >= 0.85 THEN
                    UPDATE memories
                    SET metadata = jsonb_set(
                            jsonb_set(
                                metadata,
                                '{stability}',
                                to_jsonb(LEAST(1.0, COALESCE(ident_stability, 0.7) + 0.05))
                            ),
                            '{confidence}',
                            to_jsonb(LEAST(1.0, COALESCE(ident_conf, 0.7) + 0.02))
                        ),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ident_existing_id;
                ELSE
                    PERFORM create_worldview_memory(
                        change_text,
                        'self',
                        0.7,
                        0.5,
                        0.6,
                        'discovered'
                    );
                END IF;
            END IF;
        END LOOP;
    END IF;
    IF p_result ? 'worldview_updates' THEN
        FOR wupd IN SELECT * FROM jsonb_array_elements(COALESCE(p_result->'worldview_updates', '[]'::jsonb))
        LOOP
            wid := NULLIF(wupd->>'id', '')::uuid;
            new_conf := COALESCE((wupd->>'new_confidence')::float, NULL);
            IF wid IS NOT NULL AND new_conf IS NOT NULL THEN
                UPDATE memories
                SET metadata = jsonb_set(metadata, '{confidence}', to_jsonb(new_conf)),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = wid AND type = 'worldview';
            END IF;
        END LOOP;
    END IF;
    IF p_result ? 'worldview_influences' THEN
        FOR winf IN SELECT * FROM jsonb_array_elements(COALESCE(p_result->'worldview_influences', '[]'::jsonb))
        LOOP
            BEGIN
                wid := NULLIF(winf->>'worldview_id', '')::uuid;
                wmem := NULLIF(winf->>'memory_id', '')::uuid;
                wstrength := COALESCE(NULLIF(winf->>'strength', '')::float, NULL);
                wtype := COALESCE(NULLIF(winf->>'influence_type', ''), 'evidence');

                IF wid IS NOT NULL AND wmem IS NOT NULL AND wstrength IS NOT NULL THEN
                    IF wstrength > 0 THEN
                        PERFORM create_memory_relationship(
                            wmem, wid, 'SUPPORTS',
                            jsonb_build_object('strength', wstrength, 'type', wtype)
                        );
                    ELSIF wstrength < 0 THEN
                        PERFORM create_memory_relationship(
                            wmem, wid, 'CONTRADICTS',
                            jsonb_build_object('strength', ABS(wstrength), 'type', wtype)
                        );
                    END IF;
                END IF;
            EXCEPTION
                WHEN OTHERS THEN
                    NULL;
            END;
        END LOOP;
    END IF;

    IF p_result ? 'discovered_relationships' THEN
        FOR rel IN SELECT * FROM jsonb_array_elements(COALESCE(p_result->'discovered_relationships', '[]'::jsonb))
        LOOP
            BEGIN
                from_id := NULLIF(rel->>'from_id', '')::uuid;
                to_id := NULLIF(rel->>'to_id', '')::uuid;
                rel_type := (rel->>'type')::graph_edge_type;
                rel_conf := COALESCE((rel->>'confidence')::float, 0.8);
                IF from_id IS NOT NULL AND to_id IS NOT NULL THEN
                    PERFORM discover_relationship(from_id, to_id, rel_type, rel_conf, 'reflection', p_heartbeat_id, 'reflect');
                END IF;
            EXCEPTION
                WHEN OTHERS THEN
                    NULL;
            END;
        END LOOP;
    END IF;

    IF p_result ? 'contradictions_noted' THEN
        FOR contra IN SELECT * FROM jsonb_array_elements(COALESCE(p_result->'contradictions_noted', '[]'::jsonb))
        LOOP
            ma := NULLIF(contra->>'memory_a', '')::uuid;
            mb := NULLIF(contra->>'memory_b', '')::uuid;
            reason_text := COALESCE(contra->>'resolution', '');
            IF ma IS NOT NULL AND mb IS NOT NULL THEN
                PERFORM discover_relationship(
                    ma,
                    mb,
                    'CONTRADICTS',
                    0.8,
                    'reflection',
                    p_heartbeat_id,
                    COALESCE(reason_text, '')
                );
            END IF;
        END LOOP;
    END IF;
    IF p_result ? 'self_updates' THEN
        FOR selfupd IN SELECT * FROM jsonb_array_elements(COALESCE(p_result->'self_updates', '[]'::jsonb))
        LOOP
            sm_kind := NULLIF(COALESCE(selfupd->>'kind', ''), '');
            sm_concept := NULLIF(COALESCE(selfupd->>'concept', ''), '');
            sm_strength := COALESCE(NULLIF(selfupd->>'strength', '')::float, 0.8);

            sm_evidence := NULL;
            BEGIN
                IF NULLIF(COALESCE(selfupd->>'evidence_memory_id', ''), '') IS NOT NULL THEN
                    sm_evidence := (selfupd->>'evidence_memory_id')::uuid;
                END IF;
            EXCEPTION
                WHEN OTHERS THEN
                    sm_evidence := NULL;
            END;

            PERFORM upsert_self_concept_edge(sm_kind, sm_concept, sm_strength, sm_evidence);
        END LOOP;
    END IF;
END;
$$ LANGUAGE plpgsql;

SET check_function_bodies = on;
