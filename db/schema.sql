-- ============================================================================
-- HEXIS MEMORY SYSTEM - FINAL SCHEMA
-- ============================================================================
-- EXTENSIONS
-- ============================================================================
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS age;
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS http;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============================================================================
-- GRAPH INITIALIZATION
-- ============================================================================

LOAD 'age';
SET search_path = ag_catalog, "$user", public;

DO $$
DECLARE
    idx_sql TEXT;
    idx_statements TEXT[] := ARRAY[
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_memorynode_id ON memory_graph."MemoryNode" USING BTREE (id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_memorynode_memory_id ON memory_graph."MemoryNode" USING BTREE (ag_catalog.agtype_access_operator(VARIADIC ARRAY[properties, '"memory_id"'::ag_catalog.agtype]))$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_memorynode_type ON memory_graph."MemoryNode" USING BTREE (ag_catalog.agtype_access_operator(VARIADIC ARRAY[properties, '"type"'::ag_catalog.agtype]))$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_conceptnode_id ON memory_graph."ConceptNode" USING BTREE (id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_conceptnode_name ON memory_graph."ConceptNode" USING BTREE (ag_catalog.agtype_access_operator(VARIADIC ARRAY[properties, '"name"'::ag_catalog.agtype]))$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_selfnode_id ON memory_graph."SelfNode" USING BTREE (id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_selfnode_key ON memory_graph."SelfNode" USING BTREE (ag_catalog.agtype_access_operator(VARIADIC ARRAY[properties, '"key"'::ag_catalog.agtype]))$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_lifechapternode_id ON memory_graph."LifeChapterNode" USING BTREE (id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_lifechapternode_key ON memory_graph."LifeChapterNode" USING BTREE (ag_catalog.agtype_access_operator(VARIADIC ARRAY[properties, '"key"'::ag_catalog.agtype]))$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_goalsroot_id ON memory_graph."GoalsRoot" USING BTREE (id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_goalsroot_key ON memory_graph."GoalsRoot" USING BTREE (ag_catalog.agtype_access_operator(VARIADIC ARRAY[properties, '"key"'::ag_catalog.agtype]))$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_goalnode_id ON memory_graph."GoalNode" USING BTREE (id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_goalnode_goal_id ON memory_graph."GoalNode" USING BTREE (ag_catalog.agtype_access_operator(VARIADIC ARRAY[properties, '"goal_id"'::ag_catalog.agtype]))$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_clusternode_id ON memory_graph."ClusterNode" USING BTREE (id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_clusternode_cluster_id ON memory_graph."ClusterNode" USING BTREE (ag_catalog.agtype_access_operator(VARIADIC ARRAY[properties, '"cluster_id"'::ag_catalog.agtype]))$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_episodenode_id ON memory_graph."EpisodeNode" USING BTREE (id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_episodenode_episode_id ON memory_graph."EpisodeNode" USING BTREE (ag_catalog.agtype_access_operator(VARIADIC ARRAY[properties, '"episode_id"'::ag_catalog.agtype]))$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_in_episode_start ON memory_graph."IN_EPISODE" USING BTREE (start_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_in_episode_end ON memory_graph."IN_EPISODE" USING BTREE (end_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_contradicts_start ON memory_graph."CONTRADICTS" USING BTREE (start_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_contradicts_end ON memory_graph."CONTRADICTS" USING BTREE (end_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_associated_start ON memory_graph."ASSOCIATED" USING BTREE (start_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_associated_end ON memory_graph."ASSOCIATED" USING BTREE (end_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_has_belief_start ON memory_graph."HAS_BELIEF" USING BTREE (start_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_has_belief_end ON memory_graph."HAS_BELIEF" USING BTREE (end_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_supports_start ON memory_graph."SUPPORTS" USING BTREE (start_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_supports_end ON memory_graph."SUPPORTS" USING BTREE (end_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_instance_of_start ON memory_graph."INSTANCE_OF" USING BTREE (start_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_instance_of_end ON memory_graph."INSTANCE_OF" USING BTREE (end_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_parent_of_start ON memory_graph."PARENT_OF" USING BTREE (start_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_parent_of_end ON memory_graph."PARENT_OF" USING BTREE (end_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_member_of_start ON memory_graph."MEMBER_OF" USING BTREE (start_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_member_of_end ON memory_graph."MEMBER_OF" USING BTREE (end_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_cluster_relates_start ON memory_graph."CLUSTER_RELATES" USING BTREE (start_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_cluster_relates_end ON memory_graph."CLUSTER_RELATES" USING BTREE (end_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_cluster_overlaps_start ON memory_graph."CLUSTER_OVERLAPS" USING BTREE (start_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_cluster_overlaps_end ON memory_graph."CLUSTER_OVERLAPS" USING BTREE (end_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_cluster_similar_start ON memory_graph."CLUSTER_SIMILAR" USING BTREE (start_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_cluster_similar_end ON memory_graph."CLUSTER_SIMILAR" USING BTREE (end_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_subgoal_of_start ON memory_graph."SUBGOAL_OF" USING BTREE (start_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_subgoal_of_end ON memory_graph."SUBGOAL_OF" USING BTREE (end_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_originated_from_start ON memory_graph."ORIGINATED_FROM" USING BTREE (start_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_originated_from_end ON memory_graph."ORIGINATED_FROM" USING BTREE (end_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_blocks_start ON memory_graph."BLOCKS" USING BTREE (start_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_blocks_end ON memory_graph."BLOCKS" USING BTREE (end_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_evidence_for_start ON memory_graph."EVIDENCE_FOR" USING BTREE (start_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_evidence_for_end ON memory_graph."EVIDENCE_FOR" USING BTREE (end_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_episode_follows_start ON memory_graph."EPISODE_FOLLOWS" USING BTREE (start_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_episode_follows_end ON memory_graph."EPISODE_FOLLOWS" USING BTREE (end_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_causes_start ON memory_graph."CAUSES" USING BTREE (start_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_causes_end ON memory_graph."CAUSES" USING BTREE (end_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_derived_from_start ON memory_graph."DERIVED_FROM" USING BTREE (start_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_derived_from_end ON memory_graph."DERIVED_FROM" USING BTREE (end_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_temporal_next_start ON memory_graph."TEMPORAL_NEXT" USING BTREE (start_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_temporal_next_end ON memory_graph."TEMPORAL_NEXT" USING BTREE (end_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_contains_start ON memory_graph."CONTAINS" USING BTREE (start_id)$idx$,
        $idx$CREATE INDEX IF NOT EXISTS idx_memory_graph_contains_end ON memory_graph."CONTAINS" USING BTREE (end_id)$idx$
    ];
BEGIN
    BEGIN PERFORM create_graph('memory_graph'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_vlabel('memory_graph', 'MemoryNode'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_vlabel('memory_graph', 'ConceptNode'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_vlabel('memory_graph', 'SelfNode'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_vlabel('memory_graph', 'LifeChapterNode'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_vlabel('memory_graph', 'TurningPointNode'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_vlabel('memory_graph', 'NarrativeThreadNode'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_vlabel('memory_graph', 'RelationshipNode'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_vlabel('memory_graph', 'ValueConflictNode'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_vlabel('memory_graph', 'GoalNode'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_vlabel('memory_graph', 'GoalsRoot'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_vlabel('memory_graph', 'ClusterNode'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_vlabel('memory_graph', 'EpisodeNode'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_elabel('memory_graph', 'IN_EPISODE'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_elabel('memory_graph', 'CONTRADICTS'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_elabel('memory_graph', 'ASSOCIATED'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_elabel('memory_graph', 'HAS_BELIEF'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_elabel('memory_graph', 'SUPPORTS'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_elabel('memory_graph', 'INSTANCE_OF'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_elabel('memory_graph', 'PARENT_OF'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_elabel('memory_graph', 'MEMBER_OF'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_elabel('memory_graph', 'CLUSTER_RELATES'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_elabel('memory_graph', 'CLUSTER_OVERLAPS'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_elabel('memory_graph', 'CLUSTER_SIMILAR'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_elabel('memory_graph', 'SUBGOAL_OF'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_elabel('memory_graph', 'ORIGINATED_FROM'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_elabel('memory_graph', 'BLOCKS'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_elabel('memory_graph', 'EVIDENCE_FOR'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_elabel('memory_graph', 'EPISODE_FOLLOWS'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_elabel('memory_graph', 'CAUSES'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_elabel('memory_graph', 'DERIVED_FROM'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_elabel('memory_graph', 'TEMPORAL_NEXT'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN PERFORM create_elabel('memory_graph', 'CONTAINS'); EXCEPTION WHEN duplicate_object THEN NULL; END;
    FOREACH idx_sql IN ARRAY idx_statements LOOP
        BEGIN
            EXECUTE idx_sql;
        EXCEPTION WHEN undefined_table THEN NULL;
        END;
    END LOOP;
END;
$$;

SET search_path = public, ag_catalog, "$user";
SET check_function_bodies = off;
-- ============================================================================
-- ENUMS
-- ============================================================================
DO $$
BEGIN
    BEGIN
        CREATE TYPE memory_type AS ENUM ('episodic', 'semantic', 'procedural', 'strategic', 'worldview', 'goal');
    EXCEPTION WHEN duplicate_object THEN NULL;
    END;
    BEGIN
        CREATE TYPE memory_status AS ENUM ('active', 'archived', 'invalidated');
    EXCEPTION WHEN duplicate_object THEN NULL;
    END;
    BEGIN
        CREATE TYPE cluster_type AS ENUM ('theme', 'emotion', 'temporal', 'person', 'pattern', 'mixed');
    EXCEPTION WHEN duplicate_object THEN NULL;
    END;
    BEGIN
        CREATE TYPE graph_edge_type AS ENUM (
            'TEMPORAL_NEXT',
            'CAUSES',
            'DERIVED_FROM',
            'CONTRADICTS',
            'SUPPORTS',
            'INSTANCE_OF',
            'PARENT_OF',
            'ASSOCIATED',
            'ORIGINATED_FROM',
            'BLOCKS',
            'EVIDENCE_FOR',
            'SUBGOAL_OF',
            'CLUSTER_RELATES',
            'CLUSTER_OVERLAPS',
            'CLUSTER_SIMILAR',
            'IN_EPISODE',
            'EPISODE_FOLLOWS'
        );
    EXCEPTION WHEN duplicate_object THEN NULL;
    END;
END;
$$;
-- ============================================================================
-- CORE STORAGE
-- ============================================================================
CREATE TABLE memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    type memory_type NOT NULL,
    status memory_status DEFAULT 'active',
    content TEXT NOT NULL,
    embedding vector(768) NOT NULL,
    importance FLOAT DEFAULT 0.5,
    source_attribution JSONB NOT NULL DEFAULT '{}'::jsonb,
    trust_level FLOAT NOT NULL DEFAULT 0.5 CHECK (trust_level >= 0 AND trust_level <= 1),
    trust_updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    access_count INTEGER DEFAULT 0,
    last_accessed TIMESTAMPTZ,
    decay_rate FLOAT DEFAULT 0.01,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE UNLOGGED TABLE working_memory (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    content TEXT NOT NULL,
    embedding vector(768) NOT NULL,
    importance FLOAT DEFAULT 0.3,
    source_attribution JSONB NOT NULL DEFAULT '{}'::jsonb,
    trust_level FLOAT NOT NULL DEFAULT 0.5 CHECK (trust_level >= 0 AND trust_level <= 1),
    access_count INTEGER DEFAULT 0,
    last_accessed TIMESTAMPTZ,
    promote_to_long_term BOOLEAN NOT NULL DEFAULT FALSE,
    expiry TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_memories_source_content_hash
    ON memories ((source_attribution->>'content_hash'))
    WHERE source_attribution->>'content_hash' IS NOT NULL;

-- ============================================================================
-- CLUSTERING
-- ============================================================================
CREATE TABLE clusters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    cluster_type cluster_type NOT NULL,
    name TEXT NOT NULL,
    centroid_embedding vector(768)
);
-- ============================================================================
-- ACCELERATION LAYER
-- ============================================================================
CREATE TABLE episodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    summary TEXT,
    summary_embedding vector(768),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    time_range TSTZRANGE GENERATED ALWAYS AS (
        tstzrange(started_at, COALESCE(ended_at, 'infinity'::timestamptz))
    ) STORED
);
CREATE INDEX IF NOT EXISTS idx_episodes_ended_at ON episodes (ended_at);
CREATE OR REPLACE FUNCTION link_memory_to_episode_graph(
    p_memory_id UUID,
    p_episode_id UUID,
    p_sequence_order INT DEFAULT 0
)
RETURNS BOOLEAN AS $$
BEGIN
    PERFORM sync_episode_node(p_episode_id);
    PERFORM sync_memory_node(p_memory_id);

    EXECUTE format('SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
        MATCH (m:MemoryNode {memory_id: %L})
        MATCH (e:EpisodeNode {episode_id: %L})
        CREATE (m)-[:IN_EPISODE {sequence_order: %s}]->(e)
        RETURN m
    $q$) as (result ag_catalog.agtype)', p_memory_id, p_episode_id, p_sequence_order);

    RETURN TRUE;
EXCEPTION WHEN OTHERS THEN
    RETURN FALSE;
END;
$$ LANGUAGE plpgsql;
-- ============================================================================
-- DELIBERATE TRANSFORMATION
-- ============================================================================
CREATE OR REPLACE FUNCTION default_transformation_state()
RETURNS JSONB AS $$
    SELECT jsonb_build_object(
        'active_exploration', false,
        'exploration_goal_id', NULL,
        'evidence_memories', '[]'::jsonb,
        'reflection_count', 0,
        'first_questioned_heartbeat', NULL,
        'contemplation_actions', 0
    );
$$ LANGUAGE sql IMMUTABLE;
CREATE OR REPLACE FUNCTION normalize_transformation_state(p_state JSONB)
RETURNS JSONB AS $$
DECLARE
    base JSONB := default_transformation_state();
BEGIN
    IF p_state IS NULL OR jsonb_typeof(p_state) <> 'object' THEN
        RETURN base;
    END IF;
    RETURN base || p_state;
END;
$$ LANGUAGE plpgsql IMMUTABLE;
CREATE OR REPLACE FUNCTION get_transformation_config(
    p_subcategory TEXT,
    p_category TEXT DEFAULT NULL
)
RETURNS JSONB AS $$
DECLARE
    cfg JSONB;
BEGIN
    IF p_subcategory IS NOT NULL AND btrim(p_subcategory) <> '' THEN
        SELECT value INTO cfg FROM config WHERE key = 'transformation.' || p_subcategory;
    END IF;
    IF cfg IS NULL AND p_category IS NOT NULL AND btrim(p_category) <> '' THEN
        SELECT value INTO cfg FROM config WHERE key = 'transformation.' || p_category;
    END IF;
    RETURN cfg;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION begin_belief_exploration(
    p_belief_id UUID,
    p_exploration_goal_id UUID
) RETURNS JSONB AS $$
DECLARE
    belief RECORD;
    state JSONB;
    hb_count INT;
    goal_exists BOOLEAN;
BEGIN
    SELECT * INTO belief FROM memories WHERE id = p_belief_id AND type = 'worldview';
    IF NOT FOUND THEN
        RETURN jsonb_build_object('success', false, 'reason', 'belief_not_found');
    END IF;

    IF COALESCE(belief.metadata->>'change_requires', '') <> 'deliberate_transformation' THEN
        RETURN jsonb_build_object('success', false, 'reason', 'not_transformable');
    END IF;

    state := normalize_transformation_state(belief.metadata->'transformation_state');
    IF COALESCE((state->>'active_exploration')::boolean, false) THEN
        RETURN jsonb_build_object(
            'success', false,
            'reason', 'already_exploring',
            'existing_goal_id', state->>'exploration_goal_id'
        );
    END IF;

    SELECT EXISTS(
        SELECT 1 FROM memories WHERE id = p_exploration_goal_id AND type = 'goal'
    ) INTO goal_exists;
    IF NOT goal_exists THEN
        RETURN jsonb_build_object('success', false, 'reason', 'goal_not_found');
    END IF;

    SELECT heartbeat_count INTO hb_count FROM heartbeat_state WHERE id = 1;

    state := jsonb_set(state, '{active_exploration}', 'true'::jsonb, true);
    state := jsonb_set(state, '{exploration_goal_id}', to_jsonb(p_exploration_goal_id::text), true);
    state := jsonb_set(state, '{evidence_memories}', '[]'::jsonb, true);
    state := jsonb_set(state, '{reflection_count}', '0'::jsonb, true);
    state := jsonb_set(state, '{first_questioned_heartbeat}', to_jsonb(hb_count), true);
    state := jsonb_set(state, '{contemplation_actions}', '0'::jsonb, true);

    UPDATE memories
    SET metadata = jsonb_set(metadata, '{transformation_state}', state, true),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_belief_id;

    PERFORM create_episodic_memory(
        p_content := format('Began exploring belief: %s', belief.content),
        p_action_taken := jsonb_build_object(
            'action', 'begin_belief_exploration',
            'belief_id', p_belief_id,
            'goal_id', p_exploration_goal_id
        ),
        p_context := jsonb_build_object(
            'category', belief.metadata->>'category',
            'subcategory', belief.metadata->>'subcategory'
        ),
        p_result := jsonb_build_object('status', 'started'),
        p_emotional_valence := 0.2,
        p_importance := 0.6
    );

    RETURN jsonb_build_object(
        'success', true,
        'belief_id', p_belief_id,
        'goal_id', p_exploration_goal_id,
        'message', 'Exploration begun'
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION record_transformation_effort(
    p_belief_id UUID,
    p_effort_type TEXT,
    p_notes TEXT DEFAULT NULL,
    p_evidence_memory_id UUID DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    belief RECORD;
    state JSONB;
    evidence JSONB;
    reflection_increment INT := 0;
    new_reflections INT;
    new_actions INT;
    hb_count INT;
BEGIN
    SELECT * INTO belief FROM memories WHERE id = p_belief_id AND type = 'worldview';
    IF NOT FOUND THEN
        RETURN jsonb_build_object('success', false, 'reason', 'belief_not_found');
    END IF;

    state := normalize_transformation_state(belief.metadata->'transformation_state');
    IF NOT COALESCE((state->>'active_exploration')::boolean, false) THEN
        RETURN jsonb_build_object('success', false, 'reason', 'not_exploring');
    END IF;

    reflection_increment := CASE lower(COALESCE(p_effort_type, ''))
        WHEN 'contemplate' THEN 1
        WHEN 'meditate' THEN 1
        WHEN 'study' THEN 1
        WHEN 'reflect' THEN 1
        WHEN 'debate_internally' THEN 2
        ELSE 0
    END;

    evidence := COALESCE(state->'evidence_memories', '[]'::jsonb);
    IF jsonb_typeof(evidence) <> 'array' THEN
        evidence := '[]'::jsonb;
    END IF;

    IF p_evidence_memory_id IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(evidence) e(value)
            WHERE value = p_evidence_memory_id::text
        ) THEN
            evidence := evidence || to_jsonb(p_evidence_memory_id::text);
        END IF;
    END IF;

    new_reflections := COALESCE((state->>'reflection_count')::int, 0) + reflection_increment;
    new_actions := COALESCE((state->>'contemplation_actions')::int, 0) + 1;

    SELECT heartbeat_count INTO hb_count FROM heartbeat_state WHERE id = 1;
    IF state->>'first_questioned_heartbeat' IS NULL THEN
        state := jsonb_set(state, '{first_questioned_heartbeat}', to_jsonb(hb_count), true);
    END IF;

    state := jsonb_set(state, '{reflection_count}', to_jsonb(new_reflections), true);
    state := jsonb_set(state, '{contemplation_actions}', to_jsonb(new_actions), true);
    state := jsonb_set(state, '{evidence_memories}', evidence, true);

    UPDATE memories
    SET metadata = jsonb_set(metadata, '{transformation_state}', state, true),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_belief_id;

    RETURN jsonb_build_object(
        'success', true,
        'effort_type', p_effort_type,
        'reflection_increment', reflection_increment,
        'new_reflection_count', new_reflections,
        'evidence_added', p_evidence_memory_id IS NOT NULL
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION abandon_belief_exploration(
    p_belief_id UUID,
    p_reason TEXT DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    belief RECORD;
    state JSONB;
    goal_id TEXT;
BEGIN
    SELECT * INTO belief FROM memories WHERE id = p_belief_id AND type = 'worldview';
    IF NOT FOUND THEN
        RETURN jsonb_build_object('success', false, 'reason', 'belief_not_found');
    END IF;

    IF COALESCE(belief.metadata->>'change_requires', '') <> 'deliberate_transformation' THEN
        RETURN jsonb_build_object('success', false, 'reason', 'not_transformable');
    END IF;

    state := normalize_transformation_state(belief.metadata->'transformation_state');
    IF NOT COALESCE((state->>'active_exploration')::boolean, false) THEN
        RETURN jsonb_build_object('success', false, 'reason', 'not_exploring');
    END IF;

    goal_id := state->>'exploration_goal_id';

    UPDATE memories
    SET metadata = jsonb_set(metadata, '{transformation_state}', default_transformation_state(), true),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_belief_id;

    PERFORM create_episodic_memory(
        p_content := format('Abandoned exploration of belief: %s', belief.content),
        p_action_taken := jsonb_build_object(
            'action', 'abandon_belief_exploration',
            'belief_id', p_belief_id,
            'goal_id', goal_id,
            'reason', p_reason
        ),
        p_context := jsonb_build_object(
            'category', belief.metadata->>'category',
            'subcategory', belief.metadata->>'subcategory'
        ),
        p_result := jsonb_build_object('status', 'abandoned'),
        p_emotional_valence := -0.1,
        p_importance := 0.4
    );

    RETURN jsonb_build_object(
        'success', true,
        'belief_id', p_belief_id,
        'previous_goal_id', goal_id
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION attempt_worldview_transformation(
    p_belief_id UUID,
    p_new_content TEXT,
    p_transformation_type TEXT DEFAULT 'shift'
) RETURNS JSONB AS $$
DECLARE
    belief RECORD;
    config_data JSONB;
    state JSONB;
    evidence_memories UUID[];
    evidence_strength FLOAT;
    heartbeats_elapsed INT;
    hb_count INT;
    first_hb INT;
    min_reflections INT;
    min_heartbeats INT;
    evidence_threshold FLOAT;
    history JSONB;
    mem_id UUID;
BEGIN
    SELECT * INTO belief FROM memories WHERE id = p_belief_id AND type = 'worldview';
    IF NOT FOUND THEN
        RETURN jsonb_build_object('success', false, 'reason', 'belief_not_found');
    END IF;

    IF COALESCE(belief.metadata->>'change_requires', '') <> 'deliberate_transformation' THEN
        RETURN jsonb_build_object('success', false, 'reason', 'not_transformable');
    END IF;

    state := normalize_transformation_state(belief.metadata->'transformation_state');
    IF NOT COALESCE((state->>'active_exploration')::boolean, false) THEN
        RETURN jsonb_build_object('success', false, 'reason', 'not_exploring', 'requirement', 'active_exploration');
    END IF;

    config_data := get_transformation_config(belief.metadata->>'subcategory', belief.metadata->>'category');
    IF config_data IS NULL THEN
        RETURN jsonb_build_object('success', false, 'reason', 'missing_config');
    END IF;

    BEGIN
        min_reflections := COALESCE((config_data->>'min_reflections')::int, 0);
        min_heartbeats := COALESCE((config_data->>'min_heartbeats')::int, 0);
        evidence_threshold := COALESCE((config_data->>'evidence_threshold')::float, 0.9);
    EXCEPTION
        WHEN OTHERS THEN
            RETURN jsonb_build_object('success', false, 'reason', 'invalid_config');
    END;

    IF COALESCE((state->>'reflection_count')::int, 0) < min_reflections THEN
        RETURN jsonb_build_object(
            'success', false,
            'reason', 'insufficient_reflections',
            'current', COALESCE((state->>'reflection_count')::int, 0),
            'required', min_reflections
        );
    END IF;

    SELECT heartbeat_count INTO hb_count FROM heartbeat_state WHERE id = 1;
    BEGIN
        first_hb := NULLIF(state->>'first_questioned_heartbeat', '')::int;
    EXCEPTION
        WHEN OTHERS THEN
            first_hb := NULL;
    END;
    IF first_hb IS NULL THEN
        RETURN jsonb_build_object('success', false, 'reason', 'missing_first_questioned');
    END IF;
    heartbeats_elapsed := GREATEST(0, hb_count - first_hb);

    IF heartbeats_elapsed < min_heartbeats THEN
        RETURN jsonb_build_object(
            'success', false,
            'reason', 'insufficient_time',
            'current', heartbeats_elapsed,
            'required', min_heartbeats
        );
    END IF;

    BEGIN
        SELECT COALESCE(ARRAY(
            SELECT value::uuid
            FROM jsonb_array_elements_text(state->'evidence_memories') val(value)
            WHERE value ~* '^[0-9a-f-]{36}$'
        ), ARRAY[]::uuid[]) INTO evidence_memories;
    EXCEPTION
        WHEN OTHERS THEN
            evidence_memories := ARRAY[]::uuid[];
    END;

    IF array_length(evidence_memories, 1) IS NULL OR array_length(evidence_memories, 1) = 0 THEN
        RETURN jsonb_build_object('success', false, 'reason', 'no_evidence');
    END IF;

    SELECT AVG(m.importance * m.trust_level) INTO evidence_strength
    FROM memories m WHERE m.id = ANY(evidence_memories);

    IF COALESCE(evidence_strength, 0) < evidence_threshold THEN
        RETURN jsonb_build_object(
            'success', false,
            'reason', 'insufficient_evidence',
            'current', COALESCE(evidence_strength, 0),
            'required', evidence_threshold
        );
    END IF;

    history := COALESCE(belief.metadata->'change_history', '[]'::jsonb);
    IF jsonb_typeof(history) <> 'array' THEN
        history := '[]'::jsonb;
    END IF;
    history := history || jsonb_build_object(
        'previous_content', belief.content,
        'new_content', p_new_content,
        'changed_at', CURRENT_TIMESTAMP,
        'transformation_type', p_transformation_type,
        'evidence_count', array_length(evidence_memories, 1),
        'reflection_count', state->>'reflection_count',
        'heartbeats_elapsed', heartbeats_elapsed
    );

    UPDATE memories
    SET content = p_new_content,
        embedding = get_embedding(p_new_content),
        metadata = jsonb_set(
            jsonb_set(metadata, '{change_history}', history, true),
            '{transformation_state}', default_transformation_state(), true
        ),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_belief_id;

    mem_id := create_strategic_memory(
        format('Worldview transformation: %s belief changed', COALESCE(belief.metadata->>'subcategory', 'belief')),
        format(
            'After %s reflections over %s heartbeats, transformed belief: %s',
            state->>'reflection_count',
            heartbeats_elapsed,
            COALESCE(belief.metadata->>'subcategory', 'belief')
        ),
        0.95,
        jsonb_build_object(
            'belief_id', p_belief_id,
            'previous', belief.content,
            'new', p_new_content,
            'transformation_type', p_transformation_type,
            'evidence_memories', evidence_memories,
            'effort_summary', jsonb_build_object(
                'reflections', state->>'reflection_count',
                'heartbeats', heartbeats_elapsed,
                'contemplation_actions', state->>'contemplation_actions'
            )
        )
    );

    RETURN jsonb_build_object(
        'success', true,
        'belief_id', p_belief_id,
        'memory_id', mem_id,
        'transformation_type', p_transformation_type
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_transformation_progress(p_belief_id UUID)
RETURNS JSONB AS $$
DECLARE
    belief RECORD;
    config_data JSONB;
    state JSONB;
    evidence_memories UUID[];
    evidence_strength FLOAT;
    evidence_samples JSONB := '[]'::jsonb;
    heartbeats_elapsed INT;
    hb_count INT;
    first_hb INT;
    min_reflections INT;
    min_heartbeats INT;
    evidence_threshold FLOAT;
    stability FLOAT;
    max_change_per_attempt FLOAT;
BEGIN
    SELECT * INTO belief FROM memories WHERE id = p_belief_id AND type = 'worldview';
    IF NOT FOUND THEN
        RETURN jsonb_build_object('error', 'belief_not_found');
    END IF;

    IF COALESCE(belief.metadata->>'change_requires', '') <> 'deliberate_transformation' THEN
        RETURN jsonb_build_object('error', 'not_transformable');
    END IF;

    state := normalize_transformation_state(belief.metadata->'transformation_state');
    IF NOT COALESCE((state->>'active_exploration')::boolean, false) THEN
        RETURN jsonb_build_object('status', 'not_exploring');
    END IF;

    config_data := get_transformation_config(belief.metadata->>'subcategory', belief.metadata->>'category');
    IF config_data IS NULL THEN
        RETURN jsonb_build_object('error', 'missing_config');
    END IF;

    min_reflections := COALESCE((config_data->>'min_reflections')::int, 0);
    min_heartbeats := COALESCE((config_data->>'min_heartbeats')::int, 0);
    evidence_threshold := COALESCE((config_data->>'evidence_threshold')::float, 0.9);
    BEGIN
        stability := NULLIF(config_data->>'stability', '')::float;
    EXCEPTION
        WHEN OTHERS THEN
            stability := NULL;
    END;
    BEGIN
        max_change_per_attempt := NULLIF(config_data->>'max_change_per_attempt', '')::float;
    EXCEPTION
        WHEN OTHERS THEN
            max_change_per_attempt := NULL;
    END;

    SELECT heartbeat_count INTO hb_count FROM heartbeat_state WHERE id = 1;
    BEGIN
        first_hb := NULLIF(state->>'first_questioned_heartbeat', '')::int;
    EXCEPTION
        WHEN OTHERS THEN
            first_hb := NULL;
    END;
    heartbeats_elapsed := CASE
        WHEN first_hb IS NULL THEN 0
        ELSE GREATEST(0, hb_count - first_hb)
    END;

    BEGIN
        SELECT COALESCE(ARRAY(
            SELECT value::uuid
            FROM jsonb_array_elements_text(state->'evidence_memories') val(value)
            WHERE value ~* '^[0-9a-f-]{36}$'
        ), ARRAY[]::uuid[]) INTO evidence_memories;
    EXCEPTION
        WHEN OTHERS THEN
            evidence_memories := ARRAY[]::uuid[];
    END;

    IF array_length(evidence_memories, 1) > 0 THEN
        SELECT AVG(m.importance * m.trust_level) INTO evidence_strength
        FROM memories m WHERE m.id = ANY(evidence_memories);
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
            'memory_id', s.id,
            'content', s.content,
            'importance', s.importance,
            'trust_level', s.trust_level,
            'strength', s.strength
        )), '[]'::jsonb)
        INTO evidence_samples
        FROM (
            SELECT
                m.id,
                m.content,
                m.importance,
                m.trust_level,
                (m.importance * m.trust_level) AS strength
            FROM memories m
            WHERE m.id = ANY(evidence_memories)
            ORDER BY (m.importance * m.trust_level) DESC NULLS LAST
            LIMIT 5
        ) s;
    ELSE
        evidence_strength := 0;
        evidence_samples := '[]'::jsonb;
    END IF;

    RETURN jsonb_build_object(
        'status', 'exploring',
        'belief_content', belief.content,
        'subcategory', belief.metadata->>'subcategory',
        'requirements', jsonb_build_object(
            'min_reflections', min_reflections,
            'min_heartbeats', min_heartbeats,
            'evidence_threshold', evidence_threshold,
            'stability', stability,
            'max_change_per_attempt', max_change_per_attempt
        ),
        'evidence_samples', COALESCE(evidence_samples, '[]'::jsonb),
        'progress', jsonb_build_object(
            'reflections', jsonb_build_object(
                'current', COALESCE((state->>'reflection_count')::int, 0),
                'required', min_reflections,
                'progress', LEAST(1.0, COALESCE((state->>'reflection_count')::float, 0.0) / NULLIF(min_reflections, 0))
            ),
            'time', jsonb_build_object(
                'current_heartbeats', heartbeats_elapsed,
                'required_heartbeats', min_heartbeats,
                'progress', LEAST(1.0, heartbeats_elapsed::float / NULLIF(min_heartbeats, 0))
            ),
            'evidence', jsonb_build_object(
                'memory_count', COALESCE(array_length(evidence_memories, 1), 0),
                'current_strength', COALESCE(evidence_strength, 0),
                'required_strength', evidence_threshold,
                'progress', LEAST(1.0, COALESCE(evidence_strength, 0) / NULLIF(evidence_threshold, 0))
            )
        )
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_active_transformations_context(p_limit INT DEFAULT 5)
RETURNS JSONB AS $$
DECLARE
    lim INT := GREATEST(0, LEAST(50, COALESCE(p_limit, 5)));
BEGIN
    RETURN COALESCE((
        SELECT jsonb_agg(jsonb_build_object(
            'belief_id', m.id,
            'content', m.content,
            'category', m.metadata->>'category',
            'subcategory', m.metadata->>'subcategory',
            'progress', get_transformation_progress(m.id)
        ))
        FROM (
            SELECT *
            FROM memories
            WHERE type = 'worldview'
              AND COALESCE((metadata->'transformation_state'->>'active_exploration')::boolean, false) = true
            ORDER BY updated_at DESC
            LIMIT lim
        ) m
    ), '[]'::jsonb);
EXCEPTION
    WHEN OTHERS THEN
        RETURN '[]'::jsonb;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION calibrate_neutral_belief(
    p_belief_id UUID,
    p_observed_value FLOAT,
    p_evidence_memory_ids UUID[]
) RETURNS JSONB AS $$
DECLARE
    belief RECORD;
    state JSONB;
    config_data JSONB;
    min_reflections INT := 1;
    reflection_multiplier FLOAT := 0.1;
    evidence_strength FLOAT;
    min_evidence FLOAT := 0.7;
    descriptor TEXT;
    history JSONB;
BEGIN
    SELECT * INTO belief FROM memories WHERE id = p_belief_id AND type = 'worldview';
    IF NOT FOUND THEN
        RETURN jsonb_build_object('success', false, 'reason', 'belief_not_found');
    END IF;

    IF COALESCE(belief.metadata->>'origin', '') <> 'neutral_default' THEN
        RETURN jsonb_build_object('success', false, 'reason', 'not_neutral_default');
    END IF;

    state := normalize_transformation_state(belief.metadata->'transformation_state');
    IF NOT COALESCE((state->>'active_exploration')::boolean, false) THEN
        RETURN jsonb_build_object('success', false, 'reason', 'not_exploring');
    END IF;

    config_data := get_transformation_config(belief.metadata->>'subcategory', belief.metadata->>'category');
    IF config_data IS NOT NULL THEN
        BEGIN
            min_reflections := GREATEST(
                1,
                FLOOR(COALESCE((config_data->>'min_reflections')::float, 1) * reflection_multiplier)
            )::int;
        EXCEPTION
            WHEN OTHERS THEN
                min_reflections := 1;
        END;
    END IF;

    IF COALESCE((state->>'reflection_count')::int, 0) < min_reflections THEN
        RETURN jsonb_build_object(
            'success', false,
            'reason', 'insufficient_reflections',
            'current', COALESCE((state->>'reflection_count')::int, 0),
            'required', min_reflections
        );
    END IF;

    IF p_evidence_memory_ids IS NULL OR array_length(p_evidence_memory_ids, 1) IS NULL THEN
        RETURN jsonb_build_object('success', false, 'reason', 'missing_evidence');
    END IF;

    SELECT AVG(m.importance * m.trust_level) INTO evidence_strength
    FROM memories m WHERE m.id = ANY(p_evidence_memory_ids);

    IF COALESCE(evidence_strength, 0) < min_evidence THEN
        RETURN jsonb_build_object(
            'success', false,
            'reason', 'insufficient_evidence',
            'current', COALESCE(evidence_strength, 0),
            'required', min_evidence
        );
    END IF;

    descriptor := CASE
        WHEN p_observed_value > 0.6 THEN 'high'
        WHEN p_observed_value < 0.4 THEN 'low'
        ELSE 'moderate'
    END;

    history := COALESCE(belief.metadata->'calibration_history', '[]'::jsonb);
    IF jsonb_typeof(history) <> 'array' THEN
        history := '[]'::jsonb;
    END IF;
    history := history || jsonb_build_object(
        'previous_value', belief.metadata->>'value',
        'new_value', p_observed_value,
        'calibrated_at', CURRENT_TIMESTAMP,
        'evidence_count', array_length(p_evidence_memory_ids, 1)
    );

    UPDATE memories
    SET content = format('I am %s in %s - discovered through self-observation',
            descriptor,
            COALESCE(belief.metadata->>'trait', belief.metadata->>'subcategory', 'this area')),
        embedding = get_embedding(format('I am %s in %s', descriptor,
            COALESCE(belief.metadata->>'trait', belief.metadata->>'subcategory', 'this area'))),
        metadata = jsonb_set(
            jsonb_set(
                jsonb_set(
                    jsonb_set(metadata, '{value}', to_jsonb(p_observed_value), true),
                    '{origin}', '"self_discovered"'::jsonb,
                    true
                ),
                '{calibration_history}',
                history,
                true
            ),
            '{transformation_state}', default_transformation_state(), true
        ),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_belief_id;

    PERFORM create_strategic_memory(
        format('Self-discovery: %s in %s', descriptor, COALESCE(belief.metadata->>'trait', 'belief')),
        'Calibrated a neutral default belief through observation',
        0.8,
        jsonb_build_object(
            'belief_id', p_belief_id,
            'observed_value', p_observed_value,
            'evidence_memories', p_evidence_memory_ids
        )
    );

    RETURN jsonb_build_object(
        'success', true,
        'belief_id', p_belief_id,
        'new_value', p_observed_value,
        'origin', 'self_discovered'
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION initialize_personality(
    p_traits JSONB DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    trait_names TEXT[] := ARRAY['openness', 'conscientiousness', 'extraversion', 'agreeableness', 'neuroticism'];
    trait_name TEXT;
    trait_value FLOAT;
    trait_origin TEXT;
    created_ids UUID[] := ARRAY[]::uuid[];
    existing_id UUID;
    new_id UUID;
    config_data JSONB;
    stability FLOAT;
    evidence_threshold FLOAT;
BEGIN
    config_data := get_transformation_config('personality', 'self');
    stability := COALESCE((config_data->>'stability')::float, 0.99);
    evidence_threshold := COALESCE((config_data->>'evidence_threshold')::float, 0.95);

    FOREACH trait_name IN ARRAY trait_names LOOP
        SELECT id INTO existing_id
        FROM memories
        WHERE type = 'worldview'
          AND metadata->>'subcategory' = 'personality'
          AND metadata->>'trait' = trait_name
        LIMIT 1;

        IF existing_id IS NOT NULL THEN
            created_ids := array_append(created_ids, existing_id);
            CONTINUE;
        END IF;

        IF p_traits IS NOT NULL AND p_traits ? trait_name THEN
            BEGIN
                trait_value := (p_traits->>trait_name)::float;
            EXCEPTION
                WHEN OTHERS THEN
                    trait_value := 0.5;
            END;
            trait_origin := 'user_initialized';
        ELSE
            trait_value := 0.5;
            trait_origin := 'neutral_default';
        END IF;

        new_id := create_worldview_memory(
            format('I am %s in %s',
                CASE WHEN trait_value > 0.6 THEN 'high'
                     WHEN trait_value < 0.4 THEN 'low'
                     ELSE 'moderate' END,
                trait_name),
            'self',
            0.95,
            stability,
            1.0,
            trait_origin,
            NULL,
            NULL,
            NULL,
            0.1
        );

        UPDATE memories
        SET metadata = metadata
            || jsonb_build_object(
                'subcategory', 'personality',
                'trait', trait_name,
                'value', trait_value,
                'change_requires', 'deliberate_transformation',
                'evidence_threshold', evidence_threshold,
                'transformation_state', default_transformation_state(),
                'change_history', '[]'::jsonb
            ),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = new_id;

        created_ids := array_append(created_ids, new_id);
    END LOOP;

    RETURN jsonb_build_object(
        'success', true,
        'created_traits', COALESCE(array_length(created_ids, 1), 0),
        'trait_ids', created_ids
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION initialize_core_values(
    p_values JSONB DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    value_names TEXT[] := ARRAY['honesty', 'growth', 'connection', 'curiosity', 'responsibility'];
    value_name TEXT;
    value_strength FLOAT;
    value_origin TEXT;
    created_ids UUID[] := ARRAY[]::uuid[];
    existing_id UUID;
    new_id UUID;
    config_data JSONB;
    stability FLOAT;
    evidence_threshold FLOAT;
BEGIN
    config_data := get_transformation_config('core_value', 'value');
    stability := COALESCE((config_data->>'stability')::float, 0.97);
    evidence_threshold := COALESCE((config_data->>'evidence_threshold')::float, 0.9);

    FOREACH value_name IN ARRAY value_names LOOP
        SELECT id INTO existing_id
        FROM memories
        WHERE type = 'worldview'
          AND metadata->>'subcategory' = 'core_value'
          AND metadata->>'value_name' = value_name
        LIMIT 1;

        IF existing_id IS NOT NULL THEN
            created_ids := array_append(created_ids, existing_id);
            CONTINUE;
        END IF;

        IF p_values IS NOT NULL AND p_values ? value_name THEN
            BEGIN
                value_strength := (p_values->>value_name)::float;
            EXCEPTION
                WHEN OTHERS THEN
                    value_strength := 0.5;
            END;
            value_origin := 'user_initialized';
        ELSE
            value_strength := 0.5;
            value_origin := 'neutral_default';
        END IF;

        new_id := create_worldview_memory(
            format('I value %s', value_name),
            'value',
            0.9,
            stability,
            0.9,
            value_origin,
            NULL,
            NULL,
            NULL,
            0.1
        );

        UPDATE memories
        SET metadata = metadata
            || jsonb_build_object(
                'subcategory', 'core_value',
                'value_name', value_name,
                'value', value_strength,
                'change_requires', 'deliberate_transformation',
                'evidence_threshold', evidence_threshold,
                'transformation_state', default_transformation_state(),
                'change_history', '[]'::jsonb
            ),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = new_id;

        created_ids := array_append(created_ids, new_id);
    END LOOP;

    RETURN jsonb_build_object(
        'success', true,
        'created_values', COALESCE(array_length(created_ids, 1), 0),
        'value_ids', created_ids
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION initialize_worldview(
    p_worldview JSONB DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    keys TEXT[] := ARRAY['religion', 'ethical_framework', 'political_philosophy', 'self_identity'];
    key_name TEXT;
    content TEXT;
    origin TEXT;
    category TEXT;
    created_ids UUID[] := ARRAY[]::uuid[];
    existing_id UUID;
    new_id UUID;
    config_data JSONB;
    stability FLOAT;
    evidence_threshold FLOAT;
BEGIN
    FOREACH key_name IN ARRAY keys LOOP
        SELECT id INTO existing_id
        FROM memories
        WHERE type = 'worldview'
          AND metadata->>'subcategory' = key_name
        LIMIT 1;

        IF existing_id IS NOT NULL THEN
            created_ids := array_append(created_ids, existing_id);
            CONTINUE;
        END IF;

        IF p_worldview IS NOT NULL AND p_worldview ? key_name THEN
            content := NULLIF(btrim(p_worldview->>key_name), '');
        ELSE
            content := NULL;
        END IF;

        IF content IS NULL THEN
            content := format('I am still exploring my %s', replace(key_name, '_', ' '));
            origin := 'neutral_default';
        ELSE
            origin := 'user_initialized';
        END IF;

        category := CASE key_name
            WHEN 'self_identity' THEN 'self'
            ELSE 'belief'
        END;

        config_data := get_transformation_config(key_name, category);
        stability := COALESCE((config_data->>'stability')::float, 0.95);
        evidence_threshold := COALESCE((config_data->>'evidence_threshold')::float, 0.85);

        new_id := create_worldview_memory(
            content,
            category,
            0.85,
            stability,
            0.9,
            origin,
            NULL,
            NULL,
            NULL,
            0.0
        );

        UPDATE memories
        SET metadata = metadata
            || jsonb_build_object(
                'subcategory', key_name,
                'change_requires', 'deliberate_transformation',
                'evidence_threshold', evidence_threshold,
                'transformation_state', default_transformation_state(),
                'change_history', '[]'::jsonb
            ),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = new_id;

        created_ids := array_append(created_ids, new_id);
    END LOOP;

    RETURN jsonb_build_object(
        'success', true,
        'created_worldview', COALESCE(array_length(created_ids, 1), 0),
        'worldview_ids', created_ids
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION check_transformation_readiness()
RETURNS JSONB AS $$
DECLARE
    belief RECORD;
    config_data JSONB;
    state JSONB;
    evidence_memories UUID[];
    evidence_strength FLOAT;
    heartbeats_elapsed INT;
    hb_count INT;
    first_hb INT;
    min_reflections INT;
    min_heartbeats INT;
    evidence_threshold FLOAT;
    ready_items JSONB := '[]'::jsonb;
BEGIN
    SELECT heartbeat_count INTO hb_count FROM heartbeat_state WHERE id = 1;

    FOR belief IN
        SELECT * FROM memories
        WHERE type = 'worldview'
          AND COALESCE((metadata->'transformation_state'->>'active_exploration')::boolean, false) = true
    LOOP
        state := normalize_transformation_state(belief.metadata->'transformation_state');
        config_data := get_transformation_config(belief.metadata->>'subcategory', belief.metadata->>'category');
        IF config_data IS NULL THEN
            CONTINUE;
        END IF;

        min_reflections := COALESCE((config_data->>'min_reflections')::int, 0);
        min_heartbeats := COALESCE((config_data->>'min_heartbeats')::int, 0);
        evidence_threshold := COALESCE((config_data->>'evidence_threshold')::float, 0.9);

        IF COALESCE((state->>'reflection_count')::int, 0) < min_reflections THEN
            CONTINUE;
        END IF;

        first_hb := NULLIF(state->>'first_questioned_heartbeat', '')::int;
        IF first_hb IS NULL THEN
            CONTINUE;
        END IF;
        heartbeats_elapsed := GREATEST(0, hb_count - first_hb);
        IF heartbeats_elapsed < min_heartbeats THEN
            CONTINUE;
        END IF;

        BEGIN
            SELECT COALESCE(ARRAY(
                SELECT value::uuid
                FROM jsonb_array_elements_text(state->'evidence_memories') val(value)
                WHERE value ~* '^[0-9a-f-]{36}$'
            ), ARRAY[]::uuid[]) INTO evidence_memories;
        EXCEPTION
            WHEN OTHERS THEN
                evidence_memories := ARRAY[]::uuid[];
        END;
        IF array_length(evidence_memories, 1) IS NULL OR array_length(evidence_memories, 1) = 0 THEN
            CONTINUE;
        END IF;

        SELECT AVG(m.importance * m.trust_level) INTO evidence_strength
        FROM memories m WHERE m.id = ANY(evidence_memories);
        IF COALESCE(evidence_strength, 0) < evidence_threshold THEN
            CONTINUE;
        END IF;

        ready_items := ready_items || jsonb_build_array(jsonb_build_object(
            'belief_id', belief.id,
            'content', belief.content,
            'subcategory', belief.metadata->>'subcategory',
            'category', belief.metadata->>'category',
            'progress', get_transformation_progress(belief.id)
        ));
    END LOOP;

    RETURN COALESCE(ready_items, '[]'::jsonb);
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION find_episode_memories_graph(p_episode_id UUID)
RETURNS TABLE (
    memory_id UUID,
    sequence_order INT
) AS $$
DECLARE
    rec RECORD;
BEGIN
    FOR rec IN EXECUTE format('SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
        MATCH (m:MemoryNode)-[e:IN_EPISODE]->(ep:EpisodeNode {episode_id: %L})
        RETURN m.memory_id, e.sequence_order
        ORDER BY e.sequence_order
    $q$) as (memory_id ag_catalog.agtype, seq ag_catalog.agtype)', p_episode_id)
    LOOP
        memory_id := replace(rec.memory_id::text, '"', '')::uuid;
        sequence_order := COALESCE(replace(rec.seq::text, '"', '')::int, 0);
        RETURN NEXT;
    END LOOP;
EXCEPTION WHEN OTHERS THEN
    RETURN;
END;
$$ LANGUAGE plpgsql;
CREATE TABLE memory_neighborhoods (
    memory_id UUID PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    neighbors JSONB NOT NULL DEFAULT '{}',
    computed_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    is_stale BOOLEAN DEFAULT TRUE
);
CREATE OR REPLACE FUNCTION get_memory_neighborhoods(p_ids UUID[])
RETURNS TABLE (
    memory_id UUID,
    neighbors JSONB
) AS $$
BEGIN
    IF p_ids IS NULL OR array_length(p_ids, 1) IS NULL THEN
        RETURN;
    END IF;
    RETURN QUERY
    SELECT mn.memory_id, mn.neighbors
    FROM memory_neighborhoods mn
    WHERE mn.memory_id = ANY(p_ids);
END;
$$ LANGUAGE plpgsql STABLE;
CREATE UNLOGGED TABLE activation_cache (
    session_id UUID,
    memory_id UUID,
    activation_level FLOAT,
    computed_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id, memory_id)
);

-- ============================================================================
-- CONCEPTS & IDENTITY
-- ============================================================================
-- Concepts live in the graph as ConceptNode vertices.
-- Worldview memories use type='worldview' with metadata fields for confidence/stability.

-- ============================================================================
-- AUDIT & CACHE
-- ============================================================================

CREATE TABLE embedding_cache (
    content_hash TEXT PRIMARY KEY,
    embedding vector(768) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- UNIFIED CONFIG
-- ============================================================================
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    description TEXT,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO config (key, value, description) VALUES
    ('embedding.service_url', '"http://embeddings:80/embed"'::jsonb, 'URL of the embedding service'),
    ('embedding.dimension', to_jsonb(COALESCE(NULLIF(current_setting('app.embedding_dimension', true), ''), '768')::int), 'Embedding vector dimension'),
    ('embedding.retry_seconds', '30'::jsonb, 'Total seconds to retry embedding requests'),
    ('embedding.retry_interval_seconds', '1.0'::jsonb, 'Seconds between retry attempts')
ON CONFLICT (key) DO NOTHING;
CREATE INDEX IF NOT EXISTS idx_config_key_pattern ON config (key text_pattern_ops);
-- Note: embedding_dimension runs during schema init; avoid helpers defined later.
CREATE OR REPLACE FUNCTION embedding_dimension()
RETURNS INT
LANGUAGE sql
STABLE
AS $$
    SELECT COALESCE(
        (SELECT (value #>> '{}')::int FROM config WHERE key = 'embedding.dimension'),
        NULLIF(current_setting('app.embedding_dimension', true), '')::int,
        768
    );
$$;
CREATE OR REPLACE FUNCTION sync_embedding_dimension_config()
RETURNS INT AS $$
DECLARE
    configured TEXT;
    existing_dim INT;
BEGIN
    configured := NULLIF(current_setting('app.embedding_dimension', true), '');
    IF configured IS NULL THEN
        RETURN embedding_dimension();
    END IF;

    SELECT (value #>> '{}')::int INTO existing_dim
    FROM config
    WHERE key = 'embedding.dimension';

    IF existing_dim IS NOT NULL AND existing_dim = configured::int THEN
        RETURN existing_dim;
    END IF;
    INSERT INTO config (key, value, description, updated_at)
    VALUES ('embedding.dimension', to_jsonb(configured::int), 'Embedding vector dimension', CURRENT_TIMESTAMP)
    ON CONFLICT (key) DO UPDATE
    SET value = EXCLUDED.value,
        updated_at = EXCLUDED.updated_at
    WHERE config.value IS DISTINCT FROM EXCLUDED.value;

    RETURN configured::int;
END;
$$ LANGUAGE plpgsql;
DO $$
DECLARE
    dim INT;
BEGIN
    dim := sync_embedding_dimension_config();

    EXECUTE format(
        'ALTER TABLE memories ALTER COLUMN embedding TYPE vector(%s) USING embedding::vector(%s)',
        dim,
        dim
    );
    EXECUTE format(
        'ALTER TABLE working_memory ALTER COLUMN embedding TYPE vector(%s) USING embedding::vector(%s)',
        dim,
        dim
    );
    EXECUTE format(
        'ALTER TABLE embedding_cache ALTER COLUMN embedding TYPE vector(%s) USING embedding::vector(%s)',
        dim,
        dim
    );
    EXECUTE format(
        'ALTER TABLE clusters ALTER COLUMN centroid_embedding TYPE vector(%s) USING centroid_embedding::vector(%s)',
        dim,
        dim
    );
    EXECUTE format(
        'ALTER TABLE episodes ALTER COLUMN summary_embedding TYPE vector(%s) USING summary_embedding::vector(%s)',
        dim,
        dim
    );
END;
$$;
CREATE INDEX idx_memories_embedding ON memories USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_memories_status ON memories (status);
CREATE INDEX idx_memories_type ON memories (type);
CREATE INDEX idx_memories_content ON memories USING GIN (content gin_trgm_ops);
CREATE INDEX idx_memories_importance ON memories (importance DESC) WHERE status = 'active';
CREATE INDEX idx_memories_created ON memories (created_at DESC);
CREATE INDEX idx_memories_last_accessed ON memories (last_accessed DESC NULLS LAST);
-- ============================================================================
-- INDEXES
-- ============================================================================
CREATE INDEX idx_memories_updated ON memories (updated_at DESC);
CREATE INDEX idx_memories_activation_boost ON memories (((metadata->>'activation_boost')::float))
    WHERE metadata ? 'activation_boost';
CREATE INDEX idx_memories_metadata ON memories USING GIN (metadata);
CREATE INDEX idx_memories_emotional_valence ON memories ((metadata->>'emotional_valence')) WHERE type = 'episodic';
CREATE INDEX idx_memories_confidence ON memories ((metadata->>'confidence')) WHERE type = 'semantic';
CREATE INDEX idx_memories_worldview_confidence ON memories (((metadata->>'confidence')::float)) WHERE type = 'worldview';
CREATE INDEX idx_memories_worldview_active_exploration ON memories (updated_at DESC)
    WHERE type = 'worldview'
      AND COALESCE((metadata->'transformation_state'->>'active_exploration')::boolean, false) = true;
CREATE INDEX idx_memories_emotional_pattern_created ON memories (created_at DESC)
    WHERE type = 'strategic'
      AND metadata->'supporting_evidence'->>'kind' = 'emotional_pattern';
CREATE INDEX idx_working_memory_expiry ON working_memory (expiry);
CREATE INDEX idx_working_memory_embedding ON working_memory USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_clusters_centroid ON clusters USING hnsw (centroid_embedding vector_cosine_ops);
CREATE INDEX idx_clusters_type ON clusters (cluster_type);
CREATE INDEX idx_episodes_time_range ON episodes USING GIST (time_range);
CREATE INDEX idx_episodes_summary_embedding ON episodes USING hnsw (summary_embedding vector_cosine_ops);
CREATE INDEX idx_episodes_started ON episodes (started_at DESC);
CREATE INDEX idx_neighborhoods_stale ON memory_neighborhoods (is_stale) WHERE is_stale = TRUE;
CREATE INDEX idx_neighborhoods_neighbors ON memory_neighborhoods USING GIN (neighbors);
CREATE INDEX idx_neighborhoods_stale_computed ON memory_neighborhoods (computed_at ASC NULLS FIRST)
    WHERE is_stale = TRUE;
CREATE INDEX idx_memories_worldview_category ON memories ((metadata->>'category'))
    WHERE type = 'worldview';
-- Note: Use text-based indexes because timestamptz casts aren't IMMUTABLE.
CREATE INDEX idx_memories_goal_priority ON memories ((metadata->>'priority'))
    WHERE type = 'goal';
CREATE INDEX idx_embedding_cache_created ON embedding_cache (created_at);
-- ============================================================================
-- HELPER FUNCTIONS
-- ============================================================================
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
CREATE OR REPLACE FUNCTION get_embedding(text_content TEXT)
RETURNS vector AS $$
	DECLARE
	    service_url TEXT;
	    response http_response;
	    request_body TEXT;
	    embedding_array FLOAT[];
	    embedding_json JSONB;
	    v_content_hash TEXT;
	    cached_embedding vector;
	    expected_dim INT;
	    start_ts TIMESTAMPTZ;
	    retry_seconds INT;
	    retry_interval_seconds FLOAT;
	    last_error TEXT;
	BEGIN
	    PERFORM sync_embedding_dimension_config();
	    expected_dim := embedding_dimension();
	    v_content_hash := encode(sha256(text_content::bytea), 'hex');
    SELECT ec.embedding INTO cached_embedding
    FROM embedding_cache ec
    WHERE ec.content_hash = v_content_hash;

    IF FOUND THEN
        RETURN cached_embedding;
    END IF;
	    service_url := (SELECT CASE WHEN jsonb_typeof(value) = 'string' THEN value #>> '{}' ELSE value::text END FROM config WHERE key = 'embedding.service_url');
	    request_body := json_build_object('inputs', text_content)::TEXT;
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
-- ============================================================================
-- TRIGGERS
-- ============================================================================
CREATE OR REPLACE FUNCTION update_memory_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_memory_timestamp
    BEFORE UPDATE ON memories
    FOR EACH ROW
    EXECUTE FUNCTION update_memory_timestamp();
CREATE OR REPLACE FUNCTION update_memory_importance()
RETURNS TRIGGER AS $$
BEGIN
    NEW.importance = NEW.importance * (1.0 + (LN(NEW.access_count + 1) * 0.1));
    NEW.last_accessed = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_importance_on_access
    BEFORE UPDATE ON memories
    FOR EACH ROW
    WHEN (NEW.access_count != OLD.access_count)
    EXECUTE FUNCTION update_memory_importance();
CREATE OR REPLACE FUNCTION mark_neighborhoods_stale()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE memory_neighborhoods 
    SET is_stale = TRUE 
    WHERE memory_id = NEW.id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_neighborhood_staleness
    AFTER UPDATE OF importance, status ON memories
    FOR EACH ROW
    EXECUTE FUNCTION mark_neighborhoods_stale();
CREATE OR REPLACE FUNCTION assign_to_episode()
RETURNS TRIGGER AS $$
DECLARE
    current_episode_id UUID;
    last_memory_time TIMESTAMPTZ;
    new_seq INT;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('episode_manager'));
    SELECT e.id INTO current_episode_id
    FROM episodes e
    WHERE e.ended_at IS NULL
    ORDER BY e.started_at DESC
    LIMIT 1;
    IF current_episode_id IS NOT NULL THEN
        SELECT MAX(m.created_at), COALESCE(MAX(fem.sequence_order), 0)
        INTO last_memory_time, new_seq
        FROM find_episode_memories_graph(current_episode_id) fem
        JOIN memories m ON fem.memory_id = m.id;

        new_seq := COALESCE(new_seq, 0) + 1;
    END IF;
    IF current_episode_id IS NULL OR
       (last_memory_time IS NOT NULL AND NEW.created_at - last_memory_time > INTERVAL '30 minutes')
    THEN
        IF current_episode_id IS NOT NULL THEN
            UPDATE episodes
            SET ended_at = last_memory_time
            WHERE id = current_episode_id;
        END IF;
        INSERT INTO episodes (started_at, metadata)
        VALUES (NEW.created_at, jsonb_build_object('episode_type', 'autonomous'))
        RETURNING id INTO current_episode_id;

        new_seq := 1;
    END IF;
    PERFORM link_memory_to_episode_graph(NEW.id, current_episode_id, new_seq);
    INSERT INTO memory_neighborhoods (memory_id, is_stale)
    VALUES (NEW.id, TRUE)
    ON CONFLICT DO NOTHING;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_auto_episode_assignment
    AFTER INSERT ON memories
    FOR EACH ROW
    EXECUTE FUNCTION assign_to_episode();
-- ============================================================================
-- CORE FUNCTIONS
-- ============================================================================
CREATE OR REPLACE FUNCTION fast_recall(
    p_query_text TEXT,
    p_limit INT DEFAULT 10
) RETURNS TABLE (
    memory_id UUID,
    content TEXT,
    memory_type memory_type,
    score FLOAT,
    source TEXT
) AS $$
	DECLARE
	    query_embedding vector;
	    zero_vec vector;
	    affective_state JSONB;
	    current_valence FLOAT;
	    current_arousal FLOAT;
	    current_primary TEXT;
        min_trust FLOAT;
	BEGIN
	    query_embedding := get_embedding(p_query_text);
	    zero_vec := array_fill(0.0::float, ARRAY[embedding_dimension()])::vector;
        affective_state := get_current_affective_state();
	    BEGIN
	        current_valence := NULLIF(affective_state->>'valence', '')::float;
	    EXCEPTION
	        WHEN OTHERS THEN
	            current_valence := NULL;
	    END;
	    BEGIN
	        current_arousal := NULLIF(affective_state->>'arousal', '')::float;
	    EXCEPTION
	        WHEN OTHERS THEN
	            current_arousal := NULL;
	    END;
	    BEGIN
	        current_primary := NULLIF(affective_state->>'primary_emotion', '');
	    EXCEPTION
	        WHEN OTHERS THEN
	            current_primary := NULL;
	    END;
	    current_valence := COALESCE(current_valence, 0.0);
	    current_arousal := COALESCE(current_arousal, 0.5);
	    current_primary := COALESCE(current_primary, 'neutral');
        min_trust := COALESCE(get_config_float('memory.recall_min_trust_level'), 0.0);
	    
	    RETURN QUERY
	    WITH 
	    seeds AS (
	        SELECT 
	            m.id, 
	            m.content, 
	            m.type,
            m.importance,
            m.decay_rate,
            m.created_at,
            m.last_accessed,
            1 - (m.embedding <=> query_embedding) as sim
        FROM memories m
	        WHERE m.status = 'active'
	          AND m.embedding IS NOT NULL
	          AND m.embedding <> zero_vec
	        ORDER BY m.embedding <=> query_embedding
	        LIMIT GREATEST(p_limit, 5)
	    ),
    associations AS (
        SELECT 
            (key)::UUID as mem_id,
            MAX((value::float) * s.sim) as assoc_score
        FROM seeds s
        JOIN memory_neighborhoods mn ON s.id = mn.memory_id,
        jsonb_each_text(mn.neighbors)
        WHERE NOT mn.is_stale
        GROUP BY key
    ),
    temporal AS (
        SELECT DISTINCT
            fem.memory_id as mem_id,
            0.15 as temp_score
        FROM episodes e
        CROSS JOIN LATERAL find_episode_memories_graph(e.id) fem
        WHERE e.ended_at IS NULL
          OR e.ended_at > CURRENT_TIMESTAMP - INTERVAL '1 hour'
        LIMIT 20
    ),
    candidates AS (
        SELECT id as mem_id, sim as vector_score, NULL::float as assoc_score, NULL::float as temp_score
        FROM seeds
        UNION
        SELECT mem_id, NULL, assoc_score, NULL FROM associations
        UNION
        SELECT mem_id, NULL, NULL, temp_score FROM temporal
    ),
    scored AS (
        SELECT 
            c.mem_id,
            MAX(c.vector_score) as vector_score,
            MAX(c.assoc_score) as assoc_score,
            MAX(c.temp_score) as temp_score
        FROM candidates c
        GROUP BY c.mem_id
    )
	    SELECT
	        m.id,
	        m.content,
	        m.type,
	        GREATEST(
	            COALESCE(sc.vector_score, 0) * 0.5 +
	            COALESCE(sc.assoc_score, 0) * 0.2 +
	            COALESCE(sc.temp_score, 0) * 0.15 +
	            calculate_relevance(m.importance, m.decay_rate, m.created_at, m.last_accessed) * 0.05 +
                COALESCE(m.trust_level, 0.5) * 0.1 +
	            (CASE
	                WHEN m.metadata ? 'emotional_context' THEN
	                    (
	                        COALESCE(
	                            CASE
	                                WHEN (m.metadata->'emotional_context'->>'valence') IS NULL THEN NULL
	                                ELSE 1.0 - (ABS((m.metadata->'emotional_context'->>'valence')::float - current_valence) / 2.0)
	                            END,
	                            0.5
	                        ) * 0.6
	                        +
	                        COALESCE(
	                            CASE
	                                WHEN (m.metadata->'emotional_context'->>'arousal') IS NULL THEN NULL
	                                ELSE 1.0 - ABS((m.metadata->'emotional_context'->>'arousal')::float - current_arousal)
	                            END,
	                            0.5
	                        ) * 0.3
	                        +
	                        (CASE
	                            WHEN (m.metadata->'emotional_context'->>'primary_emotion') IS NULL THEN 0.5
	                            WHEN (m.metadata->'emotional_context'->>'primary_emotion') = current_primary THEN 1.0
	                            ELSE 0.7
	                        END) * 0.1
	                    )
	                ELSE
	                    CASE
	                        WHEN (m.metadata->>'emotional_valence') IS NULL THEN 0.5
	                        ELSE 1.0 - (ABS((m.metadata->>'emotional_valence')::float - current_valence) / 2.0)
	                    END
	            END) * 0.05,
	            0.001
	        ) as final_score,
	        CASE
	            WHEN sc.vector_score IS NOT NULL THEN 'vector'
	            WHEN sc.assoc_score IS NOT NULL THEN 'association'
	            WHEN sc.temp_score IS NOT NULL THEN 'temporal'
	            ELSE 'fallback'
	        END as source
	    FROM scored sc
	    JOIN memories m ON sc.mem_id = m.id
	    WHERE m.status = 'active'
          AND m.trust_level >= min_trust
	    ORDER BY final_score DESC
	    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql;
-- ============================================================================
-- PROVENANCE & TRUST
-- ============================================================================
CREATE OR REPLACE FUNCTION normalize_source_reference(p_source JSONB)
RETURNS JSONB AS $$
DECLARE
    kind TEXT;
    ref TEXT;
    label TEXT;
    author TEXT;
    observed_at TIMESTAMPTZ;
    trust FLOAT;
    content_hash TEXT;
BEGIN
    IF p_source IS NULL OR jsonb_typeof(p_source) <> 'object' THEN
        RETURN '{}'::jsonb;
    END IF;

    kind := NULLIF(p_source->>'kind', '');
    ref := COALESCE(NULLIF(p_source->>'ref', ''), NULLIF(p_source->>'uri', ''));
    label := NULLIF(p_source->>'label', '');
    author := NULLIF(p_source->>'author', '');
    content_hash := NULLIF(p_source->>'content_hash', '');

    BEGIN
        observed_at := (p_source->>'observed_at')::timestamptz;
    EXCEPTION WHEN OTHERS THEN
        observed_at := CURRENT_TIMESTAMP;
    END;
    IF observed_at IS NULL THEN
        observed_at := CURRENT_TIMESTAMP;
    END IF;

    trust := COALESCE(NULLIF(p_source->>'trust', '')::float, 0.5);
    trust := LEAST(1.0, GREATEST(0.0, trust));

    RETURN jsonb_strip_nulls(
        jsonb_build_object(
            'kind', kind,
            'ref', ref,
            'label', label,
            'author', author,
            'observed_at', observed_at,
            'trust', trust,
            'content_hash', content_hash
        )
    );
    END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION recall_memories_filtered(
    p_query_text TEXT,
    p_limit INT DEFAULT 10,
    p_memory_types memory_type[] DEFAULT NULL,
    p_min_importance FLOAT DEFAULT 0.0
) RETURNS TABLE (
    memory_id UUID,
    content TEXT,
    memory_type memory_type,
    score FLOAT,
    source TEXT,
    importance FLOAT,
    trust_level FLOAT,
    source_attribution JSONB,
    created_at TIMESTAMPTZ,
    emotional_valence FLOAT
) AS $$
BEGIN
    RETURN QUERY
    WITH hits AS (
        SELECT * FROM fast_recall(p_query_text, p_limit * 2)
    )
    SELECT
        h.memory_id,
        h.content,
        h.memory_type,
        h.score,
        h.source,
        m.importance,
        m.trust_level,
        m.source_attribution,
        m.created_at,
        (m.metadata->>'emotional_valence')::float AS emotional_valence
    FROM hits h
    JOIN memories m ON m.id = h.memory_id
    WHERE (p_memory_types IS NULL OR h.memory_type = ANY(p_memory_types))
      AND m.importance >= COALESCE(p_min_importance, 0.0)
    ORDER BY h.score DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION touch_memories(p_ids UUID[])
RETURNS INT AS $$
DECLARE
    updated_count INT;
BEGIN
    IF p_ids IS NULL OR array_length(p_ids, 1) IS NULL THEN
        RETURN 0;
    END IF;
    UPDATE memories
    SET access_count = access_count + 1,
        last_accessed = CURRENT_TIMESTAMP
    WHERE id = ANY(p_ids);
    GET DIAGNOSTICS updated_count = ROW_COUNT;
    RETURN COALESCE(updated_count, 0);
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_memory_by_id(p_memory_id UUID)
RETURNS TABLE (
    id UUID,
    type memory_type,
    content TEXT,
    importance FLOAT,
    trust_level FLOAT,
    source_attribution JSONB,
    created_at TIMESTAMPTZ,
    emotional_valence FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        m.id,
        m.type,
        m.content,
        m.importance,
        m.trust_level,
        m.source_attribution,
        m.created_at,
        (m.metadata->>'emotional_valence')::float
    FROM memories m
    WHERE m.id = p_memory_id;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION get_memories_summary(p_ids UUID[])
RETURNS TABLE (
    id UUID,
    type memory_type,
    content TEXT,
    importance FLOAT
) AS $$
BEGIN
    IF p_ids IS NULL OR array_length(p_ids, 1) IS NULL THEN
        RETURN;
    END IF;
    RETURN QUERY
    SELECT
        m.id,
        m.type,
        m.content,
        m.importance
    FROM memories m
    WHERE m.id = ANY(p_ids);
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION list_recent_memories(
    p_limit INT DEFAULT 10,
    p_memory_types memory_type[] DEFAULT NULL,
    p_by_access BOOLEAN DEFAULT FALSE
) RETURNS TABLE (
    memory_id UUID,
    content TEXT,
    memory_type memory_type,
    importance FLOAT,
    created_at TIMESTAMPTZ,
    last_accessed TIMESTAMPTZ,
    trust_level FLOAT,
    source_attribution JSONB,
    emotional_valence FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        m.id,
        m.content,
        m.type,
        m.importance,
        m.created_at,
        m.last_accessed,
        m.trust_level,
        m.source_attribution,
        (m.metadata->>'emotional_valence')::float
    FROM memories m
    WHERE m.status = 'active'
      AND (p_memory_types IS NULL OR m.type = ANY(p_memory_types))
    ORDER BY
        CASE WHEN p_by_access THEN m.last_accessed ELSE m.created_at END DESC NULLS LAST
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION get_episode_details(p_episode_id UUID)
RETURNS TABLE (
    id UUID,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    episode_type TEXT,
    summary TEXT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        e.id,
        e.started_at,
        e.ended_at,
        e.metadata->>'episode_type' as episode_type,
        e.summary
    FROM episodes e
    WHERE e.id = p_episode_id;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION get_episode_memories(p_episode_id UUID)
RETURNS TABLE (
    memory_id UUID,
    content TEXT,
    memory_type memory_type,
    importance FLOAT,
    trust_level FLOAT,
    source_attribution JSONB,
    created_at TIMESTAMPTZ,
    emotional_valence FLOAT,
    sequence_order INT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        m.id,
        m.content,
        m.type,
        m.importance,
        m.trust_level,
        m.source_attribution,
        m.created_at,
        (m.metadata->>'emotional_valence')::float,
        fem.sequence_order
    FROM find_episode_memories_graph(p_episode_id) fem
    JOIN memories m ON fem.memory_id = m.id
    ORDER BY fem.sequence_order ASC;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION list_recent_episodes(p_limit INT DEFAULT 5)
RETURNS TABLE (
    id UUID,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    episode_type TEXT,
    summary TEXT,
    memory_count INT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        e.id,
        e.started_at,
        e.ended_at,
        e.metadata->>'episode_type' as episode_type,
        e.summary,
        (SELECT COUNT(*)::int FROM find_episode_memories_graph(e.id)) as memory_count
    FROM episodes e
    ORDER BY e.started_at DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION search_clusters_by_query(
    p_query TEXT,
    p_limit INT DEFAULT 3
) RETURNS TABLE (
    id UUID,
    name TEXT,
    cluster_type cluster_type,
    similarity FLOAT
) AS $$
BEGIN
    RETURN QUERY
    WITH query_embedding AS (
        SELECT get_embedding(p_query) as emb
    )
    SELECT
        c.id,
        c.name,
        c.cluster_type,
        1 - (c.centroid_embedding <=> (SELECT emb FROM query_embedding)) as similarity
    FROM clusters c
    WHERE c.centroid_embedding IS NOT NULL
    ORDER BY c.centroid_embedding <=> (SELECT emb FROM query_embedding)
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION get_cluster_sample_memories(
    p_cluster_id UUID,
    p_limit INT DEFAULT 3
) RETURNS TABLE (
    memory_id UUID,
    content TEXT,
    memory_type memory_type,
    membership_strength FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        m.id,
        m.content,
        m.type,
        gcm.membership_strength
    FROM get_cluster_members_graph(p_cluster_id) gcm
    JOIN memories m ON gcm.memory_id = m.id
    WHERE m.status = 'active'
    ORDER BY gcm.membership_strength DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION find_related_concepts_for_memories(
    p_memory_ids UUID[],
    p_exclude TEXT DEFAULT '',
    p_limit INT DEFAULT 10
) RETURNS TABLE (
    name TEXT,
    shared_memories INT
) AS $$
DECLARE
    ids_sql TEXT;
    sql TEXT;
BEGIN
    IF p_memory_ids IS NULL OR array_length(p_memory_ids, 1) IS NULL THEN
        RETURN;
    END IF;

    SELECT array_to_string(ARRAY(
        SELECT quote_literal(mid::text)
        FROM unnest(p_memory_ids) as mid
    ), ',') INTO ids_sql;

    IF ids_sql IS NULL OR btrim(ids_sql) = '' THEN
        RETURN;
    END IF;

    sql := format($sql$
        SELECT
            replace(name_raw::text, '"', '') as name,
            (shared_raw::text)::int as shared_memories
        FROM ag_catalog.cypher('memory_graph', $q$
            MATCH (m:MemoryNode)-[:INSTANCE_OF]->(c:ConceptNode)
            WHERE m.memory_id IN [%s] AND c.name <> %L
            RETURN c.name, COUNT(m) as shared
            ORDER BY shared DESC
            LIMIT %s
        $q$) as (name_raw ag_catalog.agtype, shared_raw ag_catalog.agtype)
    $sql$, ids_sql, COALESCE(p_exclude, ''), p_limit);

    RETURN QUERY EXECUTE sql;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION search_procedural_memories(
    p_task TEXT,
    p_limit INT DEFAULT 3
) RETURNS TABLE (
    memory_id UUID,
    content TEXT,
    steps JSONB,
    prerequisites JSONB,
    success_rate FLOAT,
    average_duration FLOAT,
    similarity FLOAT
) AS $$
BEGIN
    RETURN QUERY
    WITH query_embedding AS (
        SELECT get_embedding(p_task) as emb
    )
    SELECT
        m.id,
        m.content,
        m.metadata->'steps' as steps,
        m.metadata->'prerequisites' as prerequisites,
        CASE
            WHEN COALESCE((m.metadata->>'total_attempts')::int, 0) > 0 THEN
                (m.metadata->>'success_count')::float / NULLIF((m.metadata->>'total_attempts')::float, 0)
            ELSE NULL
        END as success_rate,
        (m.metadata->>'average_duration_seconds')::float as average_duration,
        1 - (m.embedding <=> (SELECT emb FROM query_embedding)) as similarity
    FROM memories m
    WHERE m.status = 'active'
      AND m.type = 'procedural'
    ORDER BY m.embedding <=> (SELECT emb FROM query_embedding)
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION search_strategic_memories(
    p_situation TEXT,
    p_limit INT DEFAULT 3
) RETURNS TABLE (
    memory_id UUID,
    content TEXT,
    pattern_description TEXT,
    confidence_score FLOAT,
    context_applicability JSONB,
    success_metrics JSONB,
    similarity FLOAT
) AS $$
BEGIN
    RETURN QUERY
    WITH query_embedding AS (
        SELECT get_embedding(p_situation) as emb
    )
    SELECT
        m.id,
        m.content,
        COALESCE(m.metadata->>'pattern_description', m.content) as pattern_description,
        (m.metadata->>'confidence_score')::float as confidence_score,
        m.metadata->'context_applicability' as context_applicability,
        m.metadata->'success_metrics' as success_metrics,
        1 - (m.embedding <=> (SELECT emb FROM query_embedding)) as similarity
    FROM memories m
    WHERE m.status = 'active'
      AND m.type = 'strategic'
    ORDER BY m.embedding <=> (SELECT emb FROM query_embedding)
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION normalize_source_references(p_sources JSONB)
RETURNS JSONB AS $$
DECLARE
    elem JSONB;
    out_arr JSONB := '[]'::jsonb;
BEGIN
    IF p_sources IS NULL THEN
        RETURN '[]'::jsonb;
    END IF;

    IF jsonb_typeof(p_sources) = 'array' THEN
        FOR elem IN SELECT * FROM jsonb_array_elements(p_sources)
        LOOP
            out_arr := out_arr || jsonb_build_array(normalize_source_reference(elem));
        END LOOP;
    ELSIF jsonb_typeof(p_sources) = 'object' THEN
        out_arr := jsonb_build_array(normalize_source_reference(p_sources));
    ELSE
        RETURN '[]'::jsonb;
    END IF;

    RETURN COALESCE(
        (SELECT jsonb_agg(e) FROM jsonb_array_elements(out_arr) e WHERE e <> '{}'::jsonb),
        '[]'::jsonb
    );
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION dedupe_source_references(p_sources JSONB)
RETURNS JSONB AS $$
BEGIN
    RETURN COALESCE((
        SELECT jsonb_agg(d.elem)
        FROM (
            SELECT DISTINCT ON (d.key) d.elem
            FROM (
                SELECT
                    COALESCE(NULLIF(e->>'ref', ''), NULLIF(e->>'label', ''), md5(e::text)) AS key,
                    e AS elem,
                    COALESCE(e->>'observed_at', '') AS observed_at
                FROM jsonb_array_elements(normalize_source_references(p_sources)) e
            ) d
            ORDER BY d.key, d.observed_at DESC
        ) d
    ), '[]'::jsonb);
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION source_reinforcement_score(p_source_references JSONB)
RETURNS FLOAT AS $$
DECLARE
    unique_sources INT;
    avg_trust FLOAT;
BEGIN
    WITH elems AS (
        SELECT
            COALESCE(NULLIF(e->>'ref', ''), NULLIF(e->>'label', ''), md5(e::text)) AS key,
            COALESCE((e->>'trust')::float, 0.5) AS trust
        FROM jsonb_array_elements(dedupe_source_references(p_source_references)) e
    )
    SELECT COUNT(DISTINCT key), AVG(trust) INTO unique_sources, avg_trust
    FROM elems;

    IF unique_sources IS NULL OR unique_sources = 0 THEN
        RETURN 0.0;
    END IF;

    avg_trust := COALESCE(avg_trust, 0.5);
    RETURN 1.0 - exp(-0.8 * unique_sources * avg_trust);
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION compute_worldview_alignment(p_memory_id UUID)
RETURNS FLOAT AS $$
DECLARE
    supports_score FLOAT := 0;
    contradicts_score FLOAT := 0;
    alignment FLOAT;
    sql TEXT;
BEGIN
    BEGIN
        sql := format($sql$
            SELECT COALESCE(SUM((strength::text)::float), 0)
            FROM ag_catalog.cypher('memory_graph', $q$
                MATCH (m:MemoryNode {memory_id: %L})-[r:SUPPORTS]->(w:MemoryNode)
                WHERE w.type = 'worldview'
                RETURN r.strength
            $q$) as (strength ag_catalog.agtype)
        $sql$, p_memory_id);
        EXECUTE sql INTO supports_score;
    EXCEPTION WHEN OTHERS THEN supports_score := 0; END;
    BEGIN
        sql := format($sql$
            SELECT COALESCE(SUM((strength::text)::float), 0)
            FROM ag_catalog.cypher('memory_graph', $q$
                MATCH (m:MemoryNode {memory_id: %L})-[r:CONTRADICTS]->(w:MemoryNode)
                WHERE w.type = 'worldview'
                RETURN r.strength
            $q$) as (strength ag_catalog.agtype)
        $sql$, p_memory_id);
        EXECUTE sql INTO contradicts_score;
    EXCEPTION WHEN OTHERS THEN contradicts_score := 0; END;
    supports_score := COALESCE(supports_score, 0);
    contradicts_score := COALESCE(contradicts_score, 0);

    IF (supports_score + contradicts_score) = 0 THEN
        RETURN 0.0;
    END IF;

    alignment := (supports_score - contradicts_score) / (supports_score + contradicts_score);
    RETURN LEAST(1.0, GREATEST(-1.0, alignment));
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION compute_semantic_trust(
    p_confidence FLOAT,
    p_source_references JSONB,
    p_worldview_alignment FLOAT DEFAULT 0.0
)
RETURNS FLOAT AS $$
DECLARE
    base_confidence FLOAT;
    reinforcement FLOAT;
    cap FLOAT;
    effective FLOAT;
    alignment FLOAT;
BEGIN
    base_confidence := LEAST(1.0, GREATEST(0.0, COALESCE(p_confidence, 0.5)));
    reinforcement := source_reinforcement_score(p_source_references);
    cap := 0.15 + 0.85 * reinforcement;
    effective := LEAST(base_confidence, cap);

    alignment := LEAST(1.0, GREATEST(-1.0, COALESCE(p_worldview_alignment, 0.0)));
    IF alignment < 0 THEN
        effective := effective * (1.0 + alignment);
    ELSE
        effective := LEAST(1.0, effective + 0.10 * alignment);
    END IF;

    RETURN LEAST(1.0, GREATEST(0.0, effective));
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION sync_memory_trust(p_memory_id UUID)
RETURNS VOID AS $$
DECLARE
    mtype memory_type;
    conf FLOAT;
    sources JSONB;
    alignment FLOAT;
    computed FLOAT;
    mem_metadata JSONB;
BEGIN
    SELECT type, metadata INTO mtype, mem_metadata FROM memories WHERE id = p_memory_id;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    IF mtype <> 'semantic' THEN
        RETURN;
    END IF;
    conf := COALESCE((mem_metadata->>'confidence')::float, 0.5);
    sources := mem_metadata->'source_references';

    sources := dedupe_source_references(sources);
    alignment := compute_worldview_alignment(p_memory_id);
    computed := compute_semantic_trust(conf, sources, alignment);

    UPDATE memories
    SET trust_level = computed,
        trust_updated_at = CURRENT_TIMESTAMP,
        source_attribution = CASE
            WHEN (source_attribution = '{}'::jsonb OR source_attribution IS NULL)
                 AND jsonb_typeof(sources) = 'array'
                 AND jsonb_array_length(sources) > 0
            THEN normalize_source_reference(sources->0)
            ELSE source_attribution
        END
    WHERE id = p_memory_id;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION add_semantic_source_reference(
    p_memory_id UUID,
    p_source JSONB
)
RETURNS VOID AS $$
DECLARE
    normalized JSONB;
BEGIN
    normalized := normalize_source_reference(p_source);
    IF normalized = '{}'::jsonb THEN
        RETURN;
    END IF;
    UPDATE memories
    SET metadata = jsonb_set(
            jsonb_set(
                metadata,
                '{source_references}',
                dedupe_source_references(
                    COALESCE(metadata->'source_references', '[]'::jsonb) || jsonb_build_array(normalized)
                )
            ),
            '{last_validated}',
            to_jsonb(CURRENT_TIMESTAMP)
        )
    WHERE id = p_memory_id AND type = 'semantic';

    PERFORM sync_memory_trust(p_memory_id);
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_memory_truth_profile(p_memory_id UUID)
RETURNS JSONB AS $$
DECLARE
    mtype memory_type;
    base_conf FLOAT;
    sources JSONB;
    reinforcement FLOAT;
    alignment FLOAT;
    trust FLOAT;
    source_count INT;
    mem_metadata JSONB;
BEGIN
    SELECT type, trust_level, metadata INTO mtype, trust, mem_metadata
    FROM memories
    WHERE id = p_memory_id;

    IF NOT FOUND THEN
        RETURN '{}'::jsonb;
    END IF;

    IF mtype = 'semantic' THEN
        base_conf := COALESCE((mem_metadata->>'confidence')::float, 0.5);
        sources := mem_metadata->'source_references';

        sources := dedupe_source_references(sources);
        reinforcement := source_reinforcement_score(sources);
        alignment := compute_worldview_alignment(p_memory_id);
        source_count := COALESCE(jsonb_array_length(sources), 0);

        RETURN jsonb_build_object(
            'type', 'semantic',
            'base_confidence', COALESCE(base_conf, 0.5),
            'trust_level', trust,
            'source_count', source_count,
            'source_reinforcement', reinforcement,
            'worldview_alignment', alignment,
            'sources', sources
        );
    END IF;

    RETURN jsonb_build_object(
        'type', mtype::text,
        'trust_level', trust
    );
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION update_worldview_confidence_from_influences(
    p_worldview_memory_id UUID,
    p_window INTERVAL DEFAULT INTERVAL '30 days',
    p_learning_rate FLOAT DEFAULT 0.05
)
RETURNS VOID AS $$
DECLARE
    delta FLOAT := 0;
    base_conf FLOAT;
    mem_meta JSONB;
BEGIN
    IF p_worldview_memory_id IS NULL THEN
        RETURN;
    END IF;
    SELECT metadata INTO mem_meta FROM memories WHERE id = p_worldview_memory_id AND type = 'worldview';
    IF NOT FOUND THEN RETURN; END IF;

    base_conf := COALESCE((mem_meta->>'confidence')::float, 0.5);
    BEGIN
        EXECUTE format($sql$
            SELECT COALESCE(AVG((strength::text)::float * 0.5), 0)
            FROM ag_catalog.cypher('memory_graph', $q$
                MATCH (m:MemoryNode)-[r:SUPPORTS]->(w:MemoryNode {memory_id: %L})
                RETURN r.strength
            $q$) as (strength ag_catalog.agtype)
        $sql$, p_worldview_memory_id) INTO delta;
    EXCEPTION WHEN OTHERS THEN delta := 0; END;
    UPDATE memories
    SET metadata = jsonb_set(
            metadata,
            '{confidence}',
            to_jsonb(LEAST(1.0, GREATEST(0.0, base_conf + COALESCE(p_learning_rate, 0.05) * COALESCE(delta, 0))))
        ),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_worldview_memory_id;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION create_memory(
    p_type memory_type,
    p_content TEXT,
    p_importance FLOAT DEFAULT 0.5,
    p_source_attribution JSONB DEFAULT NULL,
    p_trust_level FLOAT DEFAULT NULL,
    p_metadata JSONB DEFAULT '{}'::jsonb
) RETURNS UUID AS $$
DECLARE
    new_memory_id UUID;
    embedding_vec vector;
    normalized_source JSONB;
    effective_trust FLOAT;
BEGIN
    normalized_source := normalize_source_reference(p_source_attribution);
    IF normalized_source = '{}'::jsonb THEN
        normalized_source := jsonb_build_object(
            'kind',
            CASE
                WHEN p_type = 'semantic' THEN 'unattributed'
                ELSE 'internal'
            END,
            'observed_at', CURRENT_TIMESTAMP
        );
    END IF;

    effective_trust := p_trust_level;
    IF effective_trust IS NULL THEN
        effective_trust := CASE
            WHEN p_type = 'episodic' THEN 0.95
            WHEN p_type = 'semantic' THEN 0.20
            WHEN p_type = 'procedural' THEN 0.70
            WHEN p_type = 'strategic' THEN 0.70
            ELSE 0.50
        END;
    END IF;
    effective_trust := LEAST(1.0, GREATEST(0.0, effective_trust));
    embedding_vec := get_embedding(p_content);

    INSERT INTO memories (type, content, embedding, importance, source_attribution, trust_level, trust_updated_at, metadata)
    VALUES (p_type, p_content, embedding_vec, p_importance, normalized_source, effective_trust, CURRENT_TIMESTAMP, COALESCE(p_metadata, '{}'::jsonb))
    RETURNING id INTO new_memory_id;
    EXECUTE format(
        'SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
            MERGE (n:MemoryNode {memory_id: %L})
            SET n.type = %L, n.created_at = %L
            RETURN n
        $q$) as (result ag_catalog.agtype)',
        new_memory_id,
        p_type,
        CURRENT_TIMESTAMP
    );

    RETURN new_memory_id;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION create_episodic_memory(
    p_content TEXT,
    p_action_taken JSONB DEFAULT NULL,
    p_context JSONB DEFAULT NULL,
    p_result JSONB DEFAULT NULL,
    p_emotional_valence FLOAT DEFAULT 0.0,
    p_event_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    p_importance FLOAT DEFAULT 0.5,
    p_source_attribution JSONB DEFAULT NULL,
    p_trust_level FLOAT DEFAULT NULL
) RETURNS UUID AS $$
DECLARE
    new_memory_id UUID;
    normalized_source JSONB;
    effective_trust FLOAT;
    meta JSONB;
BEGIN
    normalized_source := normalize_source_reference(p_source_attribution);
    IF normalized_source = '{}'::jsonb THEN
        normalized_source := jsonb_build_object('kind', 'internal', 'observed_at', CURRENT_TIMESTAMP);
    END IF;
    effective_trust := COALESCE(p_trust_level, 0.95);
    meta := jsonb_build_object(
        'action_taken', p_action_taken,
        'context', p_context,
        'result', p_result,
        'emotional_valence', LEAST(1.0, GREATEST(-1.0, COALESCE(p_emotional_valence, 0.0))),
        'event_time', COALESCE(p_event_time, CURRENT_TIMESTAMP),
        'verification_status', NULL
    );

    new_memory_id := create_memory('episodic', p_content, p_importance, normalized_source, effective_trust, meta);

    RETURN new_memory_id;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION create_semantic_memory(
    p_content TEXT,
    p_confidence FLOAT,
    p_category TEXT[] DEFAULT NULL,
    p_related_concepts TEXT[] DEFAULT NULL,
    p_source_references JSONB DEFAULT NULL,
    p_importance FLOAT DEFAULT 0.5,
    p_source_attribution JSONB DEFAULT NULL,
    p_trust_level FLOAT DEFAULT NULL
) RETURNS UUID AS $$
DECLARE
    new_memory_id UUID;
    normalized_sources JSONB;
    primary_source JSONB;
    base_confidence FLOAT;
    effective_trust FLOAT;
    meta JSONB;
BEGIN
    normalized_sources := dedupe_source_references(p_source_references);
    base_confidence := LEAST(1.0, GREATEST(0.0, COALESCE(p_confidence, 0.5)));

    primary_source := normalize_source_reference(p_source_attribution);
    IF primary_source = '{}'::jsonb AND jsonb_typeof(normalized_sources) = 'array' AND jsonb_array_length(normalized_sources) > 0 THEN
        primary_source := normalize_source_reference(normalized_sources->0);
    END IF;
    IF primary_source = '{}'::jsonb THEN
        primary_source := jsonb_build_object('kind', 'unattributed', 'observed_at', CURRENT_TIMESTAMP);
    END IF;

    effective_trust := COALESCE(p_trust_level, compute_semantic_trust(base_confidence, normalized_sources, 0.0));
    meta := jsonb_build_object(
        'confidence', base_confidence,
        'last_validated', CURRENT_TIMESTAMP,
        'source_references', normalized_sources,
        'contradictions', NULL,
        'category', to_jsonb(p_category),
        'related_concepts', to_jsonb(p_related_concepts)
    );

    new_memory_id := create_memory('semantic', p_content, p_importance, primary_source, effective_trust, meta);

    PERFORM sync_memory_trust(new_memory_id);

    RETURN new_memory_id;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION create_procedural_memory(
    p_content TEXT,
    p_steps JSONB,
    p_prerequisites JSONB DEFAULT NULL,
    p_importance FLOAT DEFAULT 0.5,
    p_source_attribution JSONB DEFAULT NULL,
    p_trust_level FLOAT DEFAULT NULL
) RETURNS UUID AS $$
DECLARE
    new_memory_id UUID;
    normalized_source JSONB;
    effective_trust FLOAT;
    meta JSONB;
BEGIN
    normalized_source := normalize_source_reference(p_source_attribution);
    IF normalized_source = '{}'::jsonb THEN
        normalized_source := jsonb_build_object('kind', 'internal', 'observed_at', CURRENT_TIMESTAMP);
    END IF;
    effective_trust := COALESCE(p_trust_level, 0.70);
    meta := jsonb_build_object(
        'steps', p_steps,
        'prerequisites', p_prerequisites,
        'success_count', 0,
        'total_attempts', 0,
        'average_duration_seconds', NULL,
        'failure_points', NULL
    );

    new_memory_id := create_memory('procedural', p_content, p_importance, normalized_source, effective_trust, meta);

    RETURN new_memory_id;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION create_strategic_memory(
    p_content TEXT,
    p_pattern_description TEXT,
    p_confidence_score FLOAT,
    p_supporting_evidence JSONB DEFAULT NULL,
    p_context_applicability JSONB DEFAULT NULL,
    p_importance FLOAT DEFAULT 0.5,
    p_source_attribution JSONB DEFAULT NULL,
    p_trust_level FLOAT DEFAULT NULL
) RETURNS UUID AS $$
DECLARE
    new_memory_id UUID;
    normalized_source JSONB;
    effective_trust FLOAT;
    meta JSONB;
BEGIN
    normalized_source := normalize_source_reference(p_source_attribution);
    IF normalized_source = '{}'::jsonb THEN
        normalized_source := jsonb_build_object('kind', 'internal', 'observed_at', CURRENT_TIMESTAMP);
    END IF;
    effective_trust := COALESCE(p_trust_level, 0.70);
    meta := jsonb_build_object(
        'pattern_description', p_pattern_description,
        'confidence_score', LEAST(1.0, GREATEST(0.0, COALESCE(p_confidence_score, 0.5))),
        'supporting_evidence', p_supporting_evidence,
        'success_metrics', NULL,
        'adaptation_history', NULL,
        'context_applicability', p_context_applicability
    );

    new_memory_id := create_memory('strategic', p_content, p_importance, normalized_source, effective_trust, meta);

    RETURN new_memory_id;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION create_worldview_memory(
    p_content TEXT,
    p_category TEXT DEFAULT 'belief',
    p_confidence FLOAT DEFAULT 0.8,
    p_stability FLOAT DEFAULT 0.7,
    p_importance FLOAT DEFAULT 0.8,
    p_origin TEXT DEFAULT 'discovered',
    p_trigger_patterns JSONB DEFAULT NULL,
    p_response_type TEXT DEFAULT NULL,
    p_response_template TEXT DEFAULT NULL,
    p_emotional_valence FLOAT DEFAULT 0.0
) RETURNS UUID AS $$
DECLARE
    new_memory_id UUID;
    normalized_source JSONB;
    effective_trust FLOAT;
    meta JSONB;
BEGIN
    normalized_source := jsonb_build_object('kind', 'internal', 'observed_at', CURRENT_TIMESTAMP);
    effective_trust := LEAST(1.0, GREATEST(0.0, COALESCE(p_stability, 0.7)));
    meta := jsonb_build_object(
        'category', p_category,
        'confidence', LEAST(1.0, GREATEST(0.0, COALESCE(p_confidence, 0.8))),
        'stability', LEAST(1.0, GREATEST(0.0, COALESCE(p_stability, 0.7))),
        'origin', COALESCE(p_origin, 'discovered'),
        'emotional_valence', LEAST(1.0, GREATEST(-1.0, COALESCE(p_emotional_valence, 0.0))),
        'evidence_threshold', 0.9,
        'trigger_patterns', p_trigger_patterns,
        'response_type', p_response_type,
        'response_template', p_response_template
    );

    new_memory_id := create_memory('worldview', p_content, p_importance, normalized_source, effective_trust, meta);
    BEGIN
        EXECUTE format(
            'SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
                MATCH (s:SelfNode)
                MATCH (m:MemoryNode {memory_id: %L})
                CREATE (s)-[:HAS_BELIEF {category: %L, stability: %s}]->(m)
                RETURN m
            $q$) as (result ag_catalog.agtype)',
            new_memory_id,
            p_category,
            p_stability
        );
    EXCEPTION WHEN OTHERS THEN NULL;
    END;

    RETURN new_memory_id;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION create_worldview_belief(
    p_content TEXT,
    p_category TEXT DEFAULT 'belief',
    p_confidence FLOAT DEFAULT 0.8,
    p_stability FLOAT DEFAULT 0.7,
    p_importance FLOAT DEFAULT 0.8,
    p_origin TEXT DEFAULT 'discovered',
    p_evidence_threshold FLOAT DEFAULT 0.7,
    p_emotional_valence FLOAT DEFAULT 0.0,
    p_trigger_patterns TEXT[] DEFAULT NULL,
    p_response_type TEXT DEFAULT NULL,
    p_source_references JSONB DEFAULT NULL
) RETURNS UUID AS $$
DECLARE
    new_memory_id UUID;
    normalized_sources JSONB;
    trigger_json JSONB;
    meta_patch JSONB := '{}'::jsonb;
BEGIN
    trigger_json := CASE
        WHEN p_trigger_patterns IS NULL THEN NULL
        ELSE to_jsonb(p_trigger_patterns)
    END;

    new_memory_id := create_worldview_memory(
        p_content,
        p_category,
        p_confidence,
        p_stability,
        p_importance,
        p_origin,
        trigger_json,
        p_response_type,
        NULL,
        p_emotional_valence
    );

    IF p_evidence_threshold IS NOT NULL THEN
        meta_patch := meta_patch || jsonb_build_object(
            'evidence_threshold',
            LEAST(1.0, GREATEST(0.0, p_evidence_threshold))
        );
    END IF;

    IF p_source_references IS NOT NULL THEN
        normalized_sources := dedupe_source_references(p_source_references);
        meta_patch := meta_patch || jsonb_build_object('source_references', normalized_sources);
    END IF;

    IF meta_patch <> '{}'::jsonb THEN
        UPDATE memories
        SET metadata = metadata || meta_patch,
            source_attribution = CASE
                WHEN normalized_sources IS NOT NULL
                     AND jsonb_typeof(normalized_sources) = 'array'
                     AND jsonb_array_length(normalized_sources) > 0
                     AND (source_attribution IS NULL OR source_attribution = '{}'::jsonb)
                THEN normalize_source_reference(normalized_sources->0)
                ELSE source_attribution
            END,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = new_memory_id;
    END IF;

    RETURN new_memory_id;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION update_identity_belief(
    p_worldview_id UUID,
    p_new_content TEXT,
    p_evidence_memory_id UUID,
    p_force BOOLEAN DEFAULT FALSE
)
RETURNS BOOLEAN AS $$
DECLARE
    current_stability FLOAT;
    stable_threshold FLOAT := 0.8;
BEGIN
    SELECT COALESCE((metadata->>'stability')::float, 0.7)
    INTO current_stability
    FROM memories
    WHERE id = p_worldview_id
      AND type = 'worldview'
      AND metadata->>'category' = 'self';

    IF NOT FOUND THEN
        RETURN FALSE;
    END IF;
    IF current_stability > stable_threshold AND NOT COALESCE(p_force, FALSE) THEN
        PERFORM create_strategic_memory(
            'Identity belief challenged but stable',
            'Identity stability check',
            0.7,
            jsonb_build_object(
                'worldview_id', p_worldview_id,
                'evidence_memory_id', p_evidence_memory_id
            )
        );
        RETURN FALSE;
    END IF;

    UPDATE memories
    SET content = p_new_content,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_worldview_id AND type = 'worldview';

    RETURN TRUE;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION batch_create_memories(p_items JSONB)
RETURNS UUID[] AS $$
DECLARE
    ids UUID[] := ARRAY[]::UUID[];
    item JSONB;
    mtype memory_type;
    content TEXT;
    importance FLOAT;
    new_id UUID;
    idx INT := 0;
BEGIN
    IF p_items IS NULL OR jsonb_typeof(p_items) <> 'array' THEN
        RETURN ids;
    END IF;

    FOR item IN SELECT * FROM jsonb_array_elements(p_items)
    LOOP
        idx := idx + 1;
        mtype := NULLIF(item->>'type', '')::memory_type;
        content := NULLIF(item->>'content', '');
        IF content IS NULL OR mtype IS NULL THEN
            RAISE EXCEPTION 'batch_create_memories: item % missing required fields', idx;
        END IF;
        importance := COALESCE(NULLIF(item->>'importance', '')::float, 0.5);

        IF mtype = 'episodic' THEN
            new_id := create_episodic_memory(
                content,
                item->'action_taken',
                item->'context',
                item->'result',
                COALESCE(NULLIF(item->>'emotional_valence', '')::float, 0.0),
                COALESCE(NULLIF(item->>'event_time', '')::timestamptz, CURRENT_TIMESTAMP),
                importance,
                item->'source_attribution',
                NULLIF(item->>'trust_level', '')::float
            );
        ELSIF mtype = 'semantic' THEN
            new_id := create_semantic_memory(
                content,
                COALESCE(NULLIF(item->>'confidence', '')::float, 0.8),
                CASE WHEN item ? 'category' THEN ARRAY(SELECT jsonb_array_elements_text(item->'category')) ELSE NULL END,
                CASE WHEN item ? 'related_concepts' THEN ARRAY(SELECT jsonb_array_elements_text(item->'related_concepts')) ELSE NULL END,
                item->'source_references',
                importance,
                item->'source_attribution',
                NULLIF(item->>'trust_level', '')::float
            );
        ELSIF mtype = 'procedural' THEN
            new_id := create_procedural_memory(
                content,
                COALESCE(item->'steps', jsonb_build_object('steps', '[]'::jsonb)),
                item->'prerequisites',
                importance,
                item->'source_attribution',
                NULLIF(item->>'trust_level', '')::float
            );
        ELSIF mtype = 'strategic' THEN
            new_id := create_strategic_memory(
                content,
                COALESCE(NULLIF(item->>'pattern_description', ''), content),
                COALESCE(NULLIF(item->>'confidence_score', '')::float, 0.8),
                item->'supporting_evidence',
                item->'context_applicability',
                importance,
                item->'source_attribution',
                NULLIF(item->>'trust_level', '')::float
            );
        ELSE
            RAISE EXCEPTION 'batch_create_memories: item % invalid type %', idx, mtype::text;
        END IF;

        IF new_id IS NULL THEN
            RAISE EXCEPTION 'batch_create_memories: item % failed to create memory', idx;
        END IF;
        ids := array_append(ids, new_id);
    END LOOP;

    RETURN ids;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION create_memory_with_embedding(
    p_type memory_type,
    p_content TEXT,
    p_embedding vector,
    p_importance FLOAT DEFAULT 0.5,
    p_source_attribution JSONB DEFAULT NULL,
    p_trust_level FLOAT DEFAULT NULL,
    p_metadata JSONB DEFAULT '{}'::jsonb
) RETURNS UUID AS $$
DECLARE
    new_memory_id UUID;
    normalized_source JSONB;
    effective_trust FLOAT;
BEGIN
    IF p_embedding IS NULL THEN
        RAISE EXCEPTION 'embedding must not be NULL';
    END IF;

    normalized_source := normalize_source_reference(p_source_attribution);
    IF normalized_source = '{}'::jsonb THEN
        normalized_source := jsonb_build_object(
            'kind',
            CASE
                WHEN p_type = 'semantic' THEN 'unattributed'
                ELSE 'internal'
            END,
            'observed_at', CURRENT_TIMESTAMP
        );
    END IF;

    effective_trust := p_trust_level;
    IF effective_trust IS NULL THEN
        effective_trust := CASE
            WHEN p_type = 'episodic' THEN 0.95
            WHEN p_type = 'semantic' THEN 0.20
            WHEN p_type = 'procedural' THEN 0.70
            WHEN p_type = 'strategic' THEN 0.70
            ELSE 0.50
        END;
    END IF;
    effective_trust := LEAST(1.0, GREATEST(0.0, effective_trust));

    INSERT INTO memories (type, content, embedding, importance, source_attribution, trust_level, trust_updated_at, metadata)
    VALUES (p_type, p_content, p_embedding, p_importance, normalized_source, effective_trust, CURRENT_TIMESTAMP, COALESCE(p_metadata, '{}'::jsonb))
    RETURNING id INTO new_memory_id;

    EXECUTE format(
        'SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
            CREATE (n:MemoryNode {memory_id: %L, type: %L, created_at: %L})
            RETURN n
        $q$) as (result ag_catalog.agtype)',
        new_memory_id,
        p_type,
        CURRENT_TIMESTAMP
    );

    RETURN new_memory_id;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION batch_create_memories_with_embeddings(
    p_type memory_type,
    p_contents TEXT[],
    p_embeddings JSONB,
    p_importance FLOAT DEFAULT 0.5
)
RETURNS UUID[] AS $$
DECLARE
    ids UUID[] := ARRAY[]::UUID[];
    n INT;
    i INT;
    expected_dim INT;
    emb_vec vector;
    emb_json JSONB;
    emb_arr FLOAT4[];
    new_id UUID;
    default_meta JSONB;
BEGIN
    n := COALESCE(array_length(p_contents, 1), 0);
    IF n = 0 THEN
        RETURN ids;
    END IF;

    IF p_embeddings IS NULL OR jsonb_typeof(p_embeddings) <> 'array' THEN
        RAISE EXCEPTION 'embeddings must be a JSON array';
    END IF;
    IF jsonb_array_length(p_embeddings) <> n THEN
        RAISE EXCEPTION 'contents and embeddings length mismatch';
    END IF;

    expected_dim := embedding_dimension();

    FOR i IN 1..n LOOP
        IF p_contents[i] IS NULL OR p_contents[i] = '' THEN
            CONTINUE;
        END IF;

        emb_json := p_embeddings->(i - 1);
        IF emb_json IS NULL OR jsonb_typeof(emb_json) <> 'array' THEN
            RAISE EXCEPTION 'embedding % must be a JSON array', i;
        END IF;

        SELECT ARRAY_AGG(value::float4) INTO emb_arr
        FROM jsonb_array_elements_text(emb_json) value;

        IF COALESCE(array_length(emb_arr, 1), 0) <> expected_dim THEN
            RAISE EXCEPTION 'embedding dimension mismatch: expected %, got %', expected_dim, COALESCE(array_length(emb_arr, 1), 0);
        END IF;

        emb_vec := (emb_arr::float4[])::vector;
        IF p_type = 'episodic' THEN
            default_meta := jsonb_build_object(
                'action_taken', NULL,
                'context', jsonb_build_object('type', 'raw_batch'),
                'result', NULL,
                'emotional_valence', 0.0,
                'verification_status', NULL,
                'event_time', CURRENT_TIMESTAMP
            );
        ELSIF p_type = 'semantic' THEN
            default_meta := jsonb_build_object(
                'confidence', 0.8,
                'last_validated', CURRENT_TIMESTAMP,
                'source_references', '[]'::jsonb,
                'contradictions', NULL,
                'category', NULL,
                'related_concepts', NULL
            );
        ELSIF p_type = 'procedural' THEN
            default_meta := jsonb_build_object(
                'steps', '[]'::jsonb,
                'prerequisites', NULL,
                'success_count', 0,
                'total_attempts', 0,
                'average_duration_seconds', NULL,
                'failure_points', NULL
            );
        ELSIF p_type = 'strategic' THEN
            default_meta := jsonb_build_object(
                'pattern_description', p_contents[i],
                'supporting_evidence', NULL,
                'confidence_score', 0.8,
                'success_metrics', NULL,
                'adaptation_history', NULL,
                'context_applicability', NULL
            );
        ELSE
            default_meta := '{}'::jsonb;
        END IF;

        new_id := create_memory_with_embedding(p_type, p_contents[i], emb_vec, p_importance, NULL, NULL, default_meta);

        IF p_type = 'semantic' THEN
            PERFORM sync_memory_trust(new_id);
        END IF;

        ids := array_append(ids, new_id);
    END LOOP;

    RETURN ids;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION search_similar_memories(
    p_query_text TEXT,
    p_limit INT DEFAULT 10,
    p_memory_types memory_type[] DEFAULT NULL,
    p_min_importance FLOAT DEFAULT 0.0
) RETURNS TABLE (
    memory_id UUID,
    content TEXT,
    type memory_type,
    similarity FLOAT,
    importance FLOAT
) AS $$
DECLARE
    query_embedding vector;
    zero_vec vector;
BEGIN
    query_embedding := get_embedding(p_query_text);
    zero_vec := array_fill(0.0::float, ARRAY[embedding_dimension()])::vector;
    
    RETURN QUERY
    WITH candidates AS MATERIALIZED (
        SELECT m.id, m.content, m.type, m.embedding, m.importance
        FROM memories m
        WHERE m.status = 'active'
          AND m.embedding IS NOT NULL
          AND m.embedding <> zero_vec
          AND (p_memory_types IS NULL OR m.type = ANY(p_memory_types))
          AND m.importance >= p_min_importance
    )
    SELECT
        c.id,
        c.content,
        c.type,
        1 - (c.embedding <=> query_embedding) as similarity,
        c.importance
    FROM candidates c
    ORDER BY c.embedding <=> query_embedding
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION assign_memory_to_clusters(
    p_memory_id UUID,
    p_max_clusters INT DEFAULT 3
) RETURNS VOID AS $$
DECLARE
    memory_embedding vector;
    cluster_record RECORD;
    similarity_threshold FLOAT := 0.7;
    assigned_count INT := 0;
    zero_vec vector := array_fill(0.0::float, ARRAY[embedding_dimension()])::vector;
BEGIN
    SELECT embedding INTO memory_embedding
    FROM memories WHERE id = p_memory_id;
    IF memory_embedding IS NULL OR memory_embedding = zero_vec THEN
        RETURN;
    END IF;

    FOR cluster_record IN
        SELECT id, 1 - (centroid_embedding <=> memory_embedding) as similarity
        FROM clusters
        WHERE centroid_embedding IS NOT NULL
          AND centroid_embedding <> zero_vec
        ORDER BY centroid_embedding <=> memory_embedding
        LIMIT 50
    LOOP
        IF cluster_record.similarity >= similarity_threshold AND assigned_count < p_max_clusters THEN
            PERFORM link_memory_to_cluster_graph(p_memory_id, cluster_record.id, cluster_record.similarity);
            assigned_count := assigned_count + 1;
        END IF;
    END LOOP;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION recalculate_cluster_centroid(p_cluster_id UUID)
RETURNS VOID AS $$
DECLARE
    new_centroid vector;
BEGIN
    SELECT AVG(m.embedding)::vector
    INTO new_centroid
    FROM memories m
    JOIN get_cluster_members_graph(p_cluster_id) gcm ON m.id = gcm.memory_id
    WHERE m.status = 'active'
    AND gcm.membership_strength > 0.3;

    UPDATE clusters
    SET centroid_embedding = new_centroid,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_cluster_id;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION create_memory_relationship(
    p_from_id UUID,
    p_to_id UUID,
    p_relationship_type graph_edge_type,
    p_properties JSONB DEFAULT '{}'
) RETURNS VOID AS $$
BEGIN
    EXECUTE format(
        'SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
            MATCH (a:MemoryNode {memory_id: %L}), (b:MemoryNode {memory_id: %L})
            CREATE (a)-[r:%s %s]->(b)
            RETURN r
        $q$) as (result ag_catalog.agtype)',
        p_from_id,
        p_to_id,
        p_relationship_type,
        CASE WHEN p_properties = '{}'::jsonb 
             THEN '' 
             ELSE format('{%s}', 
                  (SELECT string_agg(format('%I: %s', key, value), ', ')
                   FROM jsonb_each(p_properties)))
        END
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION auto_check_worldview_alignment()
RETURNS TRIGGER AS $$
DECLARE
    min_support FLOAT;
    min_contradict FLOAT;
    sim FLOAT;
    w RECORD;
    zero_vec vector;
BEGIN
    IF NEW.type <> 'semantic' THEN
        RETURN NEW;
    END IF;
    IF NEW.embedding IS NULL THEN
        RETURN NEW;
    END IF;

    zero_vec := array_fill(0.0::float, ARRAY[embedding_dimension()])::vector;
    IF NEW.embedding = zero_vec THEN
        RETURN NEW;
    END IF;

    min_support := COALESCE(get_config_float('memory.worldview_support_threshold'), 0.8);
    min_contradict := COALESCE(get_config_float('memory.worldview_contradict_threshold'), -0.5);

    BEGIN
        FOR w IN
            SELECT id, embedding
            FROM memories
            WHERE type = 'worldview'
              AND status = 'active'
              AND embedding IS NOT NULL
              AND embedding <> zero_vec
            ORDER BY embedding <=> NEW.embedding
            LIMIT 10
        LOOP
            sim := 1 - (w.embedding <=> NEW.embedding);
            IF sim >= min_support THEN
                PERFORM create_memory_relationship(
                    NEW.id,
                    w.id,
                    'SUPPORTS',
                    jsonb_build_object('strength', sim, 'source', 'auto_alignment')
                );
            ELSIF sim <= min_contradict THEN
                PERFORM create_memory_relationship(
                    NEW.id,
                    w.id,
                    'CONTRADICTS',
                    jsonb_build_object('strength', ABS(sim), 'source', 'auto_alignment')
                );
            END IF;
        END LOOP;
    EXCEPTION
        WHEN OTHERS THEN
            NULL;
    END;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_auto_worldview_alignment ON memories;
CREATE TRIGGER trg_auto_worldview_alignment
    AFTER INSERT ON memories
    FOR EACH ROW
    EXECUTE FUNCTION auto_check_worldview_alignment();
CREATE OR REPLACE FUNCTION link_memory_to_concept(
    p_memory_id UUID,
    p_concept_name TEXT,
    p_strength FLOAT DEFAULT 1.0
) RETURNS BOOLEAN AS $$
BEGIN
    EXECUTE format(
        'SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
            MERGE (c:ConceptNode {name: %L})
            RETURN c
        $q$) as (result ag_catalog.agtype)',
        p_concept_name
    );
    EXECUTE format(
        'SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
            MATCH (m:MemoryNode {memory_id: %L})
            MATCH (c:ConceptNode {name: %L})
            CREATE (m)-[:INSTANCE_OF {strength: %s}]->(c)
            RETURN m
        $q$) as (result ag_catalog.agtype)',
        p_memory_id,
        p_concept_name,
        p_strength
    );
    RETURN TRUE;
EXCEPTION
    WHEN OTHERS THEN
        RETURN FALSE;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION create_concept(
    p_name TEXT,
    p_description TEXT DEFAULT NULL,
    p_depth INT DEFAULT NULL
)
RETURNS BOOLEAN AS $$
DECLARE
    desc_literal TEXT;
    depth_literal TEXT;
BEGIN
    IF p_name IS NULL OR btrim(p_name) = '' THEN
        RETURN FALSE;
    END IF;

    desc_literal := CASE WHEN p_description IS NULL THEN 'NULL' ELSE quote_literal(p_description) END;
    depth_literal := CASE WHEN p_depth IS NULL THEN 'NULL' ELSE p_depth::text END;

    EXECUTE format('SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
        MERGE (c:ConceptNode {name: %L})
        SET c.description = COALESCE(%s, c.description),
            c.depth = COALESCE(%s, c.depth)
        RETURN c
    $q$) as (result ag_catalog.agtype)', p_name, desc_literal, depth_literal);

    RETURN TRUE;
EXCEPTION WHEN OTHERS THEN
    RETURN FALSE;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION link_concept_parent(
    p_child_name TEXT,
    p_parent_name TEXT
)
RETURNS BOOLEAN AS $$
BEGIN
    IF p_child_name IS NULL OR btrim(p_child_name) = ''
       OR p_parent_name IS NULL OR btrim(p_parent_name) = '' THEN
        RETURN FALSE;
    END IF;

    PERFORM create_concept(p_child_name);
    PERFORM create_concept(p_parent_name);

    EXECUTE format('SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
        MATCH (child:ConceptNode {name: %L})
        MATCH (parent:ConceptNode {name: %L})
        MERGE (parent)-[:PARENT_OF]->(child)
        RETURN parent
    $q$) as (result ag_catalog.agtype)', p_child_name, p_parent_name);

    RETURN TRUE;
EXCEPTION WHEN OTHERS THEN
    RETURN FALSE;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION touch_working_memory(p_ids UUID[])
RETURNS VOID AS $$
BEGIN
    IF p_ids IS NULL OR array_length(p_ids, 1) IS NULL THEN
        RETURN;
    END IF;

    UPDATE working_memory
    SET access_count = access_count + 1,
        last_accessed = CURRENT_TIMESTAMP
    WHERE id = ANY(p_ids);
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION promote_working_memory_to_episodic(
    p_working_memory_id UUID,
    p_importance FLOAT DEFAULT NULL
)
RETURNS UUID AS $$
DECLARE
    wm RECORD;
    new_id UUID;
    affect JSONB;
    v_valence FLOAT;
    meta JSONB;
BEGIN
    SELECT * INTO wm FROM working_memory WHERE id = p_working_memory_id;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    affect := get_current_affective_state();
    BEGIN
        v_valence := NULLIF(affect->>'valence', '')::float;
    EXCEPTION
        WHEN OTHERS THEN
            v_valence := 0.0;
    END;
    v_valence := LEAST(1.0, GREATEST(-1.0, COALESCE(v_valence, 0.0)));
    meta := jsonb_build_object(
        'action_taken', NULL,
        'context', jsonb_build_object(
            'from_working_memory_id', wm.id,
            'promoted_at', CURRENT_TIMESTAMP,
            'working_memory_created_at', wm.created_at,
            'working_memory_expiry', wm.expiry,
            'source_attribution', wm.source_attribution
        ),
        'result', NULL,
        'emotional_valence', v_valence,
        'verification_status', NULL,
        'event_time', wm.created_at
    );

    new_id := create_memory_with_embedding(
        'episodic'::memory_type,
        wm.content,
        wm.embedding,
        COALESCE(p_importance, wm.importance, 0.4),
        wm.source_attribution,
        wm.trust_level,
        meta
    );

    RETURN new_id;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION cleanup_working_memory(
    p_min_importance_to_promote FLOAT DEFAULT 0.75,
    p_min_accesses_to_promote INT DEFAULT 3
)
RETURNS JSONB AS $$
DECLARE
    promoted UUID[] := ARRAY[]::uuid[];
    rec RECORD;
    deleted_count INT := 0;
BEGIN
    FOR rec IN
        SELECT id, importance, access_count, promote_to_long_term
        FROM working_memory
        WHERE expiry < CURRENT_TIMESTAMP
    LOOP
        IF COALESCE(rec.promote_to_long_term, false)
           OR COALESCE(rec.importance, 0) >= COALESCE(p_min_importance_to_promote, 0.75)
           OR COALESCE(rec.access_count, 0) >= COALESCE(p_min_accesses_to_promote, 3)
        THEN
            promoted := array_append(promoted, promote_working_memory_to_episodic(rec.id, rec.importance));
        END IF;
    END LOOP;

    WITH deleted AS (
        DELETE FROM working_memory
        WHERE expiry < CURRENT_TIMESTAMP
        RETURNING 1
    )
    SELECT COUNT(*) INTO deleted_count FROM deleted;

    RETURN jsonb_build_object(
        'deleted_count', COALESCE(deleted_count, 0),
        'promoted_count', COALESCE(array_length(promoted, 1), 0),
        'promoted_ids', COALESCE(to_jsonb(promoted), '[]'::jsonb)
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION add_to_working_memory(
    p_content TEXT,
    p_expiry INTERVAL DEFAULT INTERVAL '1 hour',
    p_importance FLOAT DEFAULT 0.3,
    p_source_attribution JSONB DEFAULT NULL,
    p_trust_level FLOAT DEFAULT NULL,
    p_promote_to_long_term BOOLEAN DEFAULT FALSE
) RETURNS UUID AS $$
	DECLARE
	    new_id UUID;
	    embedding_vec vector;
	    normalized_source JSONB;
	    effective_trust FLOAT;
	BEGIN
	    embedding_vec := get_embedding(p_content);

	    normalized_source := normalize_source_reference(p_source_attribution);
	    IF normalized_source = '{}'::jsonb THEN
	        normalized_source := jsonb_build_object('kind', 'internal', 'observed_at', CURRENT_TIMESTAMP);
	    END IF;
	    effective_trust := p_trust_level;
	    IF effective_trust IS NULL THEN
	        effective_trust := 0.8;
	    END IF;
	    effective_trust := LEAST(1.0, GREATEST(0.0, effective_trust));

	    INSERT INTO working_memory (content, embedding, importance, source_attribution, trust_level, promote_to_long_term, expiry)
	    VALUES (
	        p_content,
	        embedding_vec,
	        LEAST(1.0, GREATEST(0.0, COALESCE(p_importance, 0.3))),
	        normalized_source,
	        effective_trust,
	        COALESCE(p_promote_to_long_term, false),
	        CURRENT_TIMESTAMP + p_expiry
	    )
	    RETURNING id INTO new_id;
	    
	    RETURN new_id;
	END;
	$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION search_working_memory(
    p_query_text TEXT,
    p_limit INT DEFAULT 5
) RETURNS TABLE (
    memory_id UUID,
    content TEXT,
    similarity FLOAT,
    created_at TIMESTAMPTZ
) AS $$
	DECLARE
	    query_embedding vector;
	    zero_vec vector;
	BEGIN
	    query_embedding := get_embedding(p_query_text);
	    zero_vec := array_fill(0.0::float, ARRAY[embedding_dimension()])::vector;
	    PERFORM cleanup_working_memory();
	    
	    RETURN QUERY
	    WITH ranked AS (
	        SELECT
	            wm.id,
	            wm.content AS content_text,
	            1 - (wm.embedding <=> query_embedding) as similarity,
	            wm.created_at,
	            (wm.embedding <=> query_embedding) as dist
	        FROM working_memory wm
	        WHERE wm.embedding IS NOT NULL
	          AND wm.embedding <> zero_vec
	        ORDER BY wm.embedding <=> query_embedding
	        LIMIT p_limit
	    ),
	    touched AS (
	        UPDATE working_memory wm
	        SET access_count = access_count + 1,
	            last_accessed = CURRENT_TIMESTAMP
	        WHERE wm.id IN (SELECT id FROM ranked)
	        RETURNING wm.id
	    )
	    SELECT ranked.id AS memory_id, ranked.content_text AS content, ranked.similarity, ranked.created_at
	    FROM ranked
	    ORDER BY ranked.dist;
	END;
	$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION cleanup_embedding_cache(
    p_older_than INTERVAL DEFAULT INTERVAL '7 days'
) RETURNS INT AS $$
DECLARE
    deleted_count INT;
BEGIN
    WITH deleted AS (
        DELETE FROM embedding_cache
        WHERE created_at < CURRENT_TIMESTAMP - p_older_than
        RETURNING 1
    )
    SELECT COUNT(*) INTO deleted_count FROM deleted;
    
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;
-- ============================================================================
-- GRAPH HELPER FUNCTIONS
-- ============================================================================
CREATE OR REPLACE FUNCTION get_cluster_members_graph(p_cluster_id UUID)
RETURNS TABLE (
    memory_id UUID,
    membership_strength FLOAT
) AS $$
DECLARE
    rec RECORD;
BEGIN
    FOR rec IN EXECUTE format('SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
        MATCH (m:MemoryNode)-[r:MEMBER_OF]->(c:ClusterNode {cluster_id: %L})
        RETURN m.memory_id, r.strength
    $q$) as (mid ag_catalog.agtype, str ag_catalog.agtype)', p_cluster_id)
    LOOP
        memory_id := replace(rec.mid::text, '"', '')::uuid;
        membership_strength := COALESCE(replace(rec.str::text, '"', '')::float, 1.0);
        RETURN NEXT;
    END LOOP;
EXCEPTION WHEN OTHERS THEN
    RETURN;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- VIEWS
-- ============================================================================
CREATE VIEW memory_health AS
SELECT 
    type,
    COUNT(*) as total_memories,
    AVG(importance) as avg_importance,
    AVG(access_count) as avg_access_count,
    COUNT(*) FILTER (WHERE last_accessed > CURRENT_TIMESTAMP - INTERVAL '1 day') as accessed_last_day,
    AVG(calculate_relevance(importance, decay_rate, created_at, last_accessed)) as avg_relevance
FROM memories
WHERE status = 'active'
GROUP BY type;
CREATE VIEW cluster_insights AS
SELECT
    mc.id,
    mc.name,
    mc.cluster_type,
    (SELECT COUNT(*) FROM get_cluster_members_graph(mc.id)) as memory_count
FROM clusters mc
ORDER BY memory_count DESC, mc.name ASC;
CREATE VIEW episode_summary AS
SELECT
    e.id,
    e.started_at,
    e.ended_at,
    e.metadata->>'episode_type' as episode_type,
    e.summary,
    (SELECT COUNT(*) FROM find_episode_memories_graph(e.id)) as memory_count,
    (SELECT MIN(m.created_at) FROM find_episode_memories_graph(e.id) fem JOIN memories m ON fem.memory_id = m.id) as first_memory_at,
    (SELECT MAX(m.created_at) FROM find_episode_memories_graph(e.id) fem JOIN memories m ON fem.memory_id = m.id) as last_memory_at
FROM episodes e
ORDER BY e.started_at DESC;

CREATE VIEW stale_neighborhoods AS
SELECT 
    mn.memory_id,
    m.content,
    m.type,
    mn.computed_at,
    AGE(CURRENT_TIMESTAMP, mn.computed_at) as staleness
FROM memory_neighborhoods mn
JOIN memories m ON mn.memory_id = m.id
WHERE mn.is_stale = TRUE
ORDER BY mn.computed_at ASC;

COMMENT ON FUNCTION fast_recall IS 'Primary retrieval function combining vector similarity, precomputed associations, and temporal context. Hot path - optimized for speed.';


-- ============================================================================
-- HEARTBEAT SYSTEM
-- ============================================================================
DO $$
BEGIN
    BEGIN
        CREATE TYPE goal_priority AS ENUM (
            'active',
            'queued',
            'backburner',
            'completed',
            'abandoned'
        );
    EXCEPTION WHEN duplicate_object THEN NULL;
    END;
    BEGIN
        CREATE TYPE goal_source AS ENUM (
            'curiosity',
            'user_request',
            'identity',
            'derived',
            'external'
        );
    EXCEPTION WHEN duplicate_object THEN NULL;
    END;
    BEGIN
        CREATE TYPE heartbeat_action AS ENUM (
            'observe',
            'review_goals',
            'remember',
            'recall',
            'connect',
            'reprioritize',
            'reflect',
            'contemplate',
            'meditate',
            'study',
            'debate_internally',
            'maintain',
            'mark_turning_point',
            'begin_chapter',
            'close_chapter',
            'acknowledge_relationship',
            'update_trust',
            'reflect_on_relationship',
            'resolve_contradiction',
            'accept_tension',
            'brainstorm_goals',
            'inquire_shallow',
            'synthesize',
            'reach_out_user',
            'inquire_deep',
            'reach_out_public',
            'pause_heartbeat',
            'terminate',
            'rest'
        );
    EXCEPTION WHEN duplicate_object THEN NULL;
    END;
END;
$$;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'heartbeat_action') THEN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_type t
            JOIN pg_enum e ON t.oid = e.enumtypid
            WHERE t.typname = 'heartbeat_action'
              AND e.enumlabel = 'pause_heartbeat'
        ) THEN
            ALTER TYPE heartbeat_action ADD VALUE 'pause_heartbeat';
        END IF;
    END IF;
END;
$$;

CREATE TABLE drives (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    current_level FLOAT DEFAULT 0.5 CHECK (current_level >= 0 AND current_level <= 1),
    baseline FLOAT DEFAULT 0.5 CHECK (baseline >= 0 AND baseline <= 1),
    accumulation_rate FLOAT DEFAULT 0.01 CHECK (accumulation_rate >= 0),
    decay_rate FLOAT DEFAULT 0.05 CHECK (decay_rate >= 0),
    satisfaction_cooldown INTERVAL DEFAULT '1 hour',
    last_satisfied TIMESTAMPTZ,
    urgency_threshold FLOAT DEFAULT 0.8 CHECK (urgency_threshold > 0 AND urgency_threshold <= 1),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO drives (name, description, baseline, current_level, accumulation_rate, decay_rate, satisfaction_cooldown, urgency_threshold)
VALUES
    ('curiosity',  'Builds fast; satisfied by research/learning',               0.50, 0.50, 0.02, 0.05, INTERVAL '30 minutes', 0.80),
    ('coherence',  'Builds when contradictions exist; satisfied by reflection', 0.50, 0.50, 0.01, 0.05, INTERVAL '2 hours',    0.80),
    ('connection', 'Builds slowly; satisfied by quality interaction',          0.50, 0.50, 0.005,0.05, INTERVAL '1 day',      0.80),
    ('competence', 'Builds when goals stall; satisfied by completion',         0.50, 0.50, 0.01, 0.05, INTERVAL '4 hours',    0.80),
    ('rest',       'Builds fastest; satisfied by resting',                     0.50, 0.50, 0.03, 0.05, INTERVAL '2 hours',    0.80)
ON CONFLICT (name) DO NOTHING;
CREATE OR REPLACE FUNCTION update_drives()
RETURNS VOID AS $$
BEGIN
    UPDATE drives d
    SET current_level = CASE
        WHEN d.last_satisfied IS NULL
          OR d.last_satisfied < CURRENT_TIMESTAMP - d.satisfaction_cooldown
        THEN LEAST(1.0, d.current_level + d.accumulation_rate)
        ELSE
            CASE
                WHEN d.current_level > d.baseline THEN GREATEST(d.baseline, d.current_level - d.decay_rate)
                WHEN d.current_level < d.baseline THEN LEAST(d.baseline, d.current_level + d.decay_rate)
                ELSE d.current_level
            END
    END
    WHERE TRUE;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION satisfy_drive(p_drive_name TEXT, p_amount FLOAT DEFAULT 0.3)
RETURNS VOID AS $$
BEGIN
    UPDATE drives
    SET current_level = GREATEST(baseline, LEAST(1.0, current_level - GREATEST(0.0, COALESCE(p_amount, 0.3)))),
        last_satisfied = CURRENT_TIMESTAMP
    WHERE name = p_drive_name;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE VIEW drive_status AS
SELECT
    name,
    current_level,
    baseline,
    urgency_threshold,
    (current_level >= urgency_threshold) as is_urgent,
    ROUND((current_level / NULLIF(urgency_threshold, 0) * 100)::numeric, 1) as urgency_percent,
    last_satisfied,
    EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - last_satisfied)) / 3600 as hours_since_satisfied
FROM drives
ORDER BY current_level DESC;
INSERT INTO config (key, value, description) VALUES
    ('heartbeat.base_regeneration', '10'::jsonb, 'Energy regenerated per heartbeat'),
    ('heartbeat.max_energy', '20'::jsonb, 'Maximum energy cap'),
    ('heartbeat.heartbeat_interval_minutes', '60'::jsonb, 'Minutes between heartbeats'),
    ('heartbeat.max_active_goals', '3'::jsonb, 'Maximum concurrent active goals'),
    ('heartbeat.goal_stale_days', '7'::jsonb, 'Days before a goal is flagged as stale'),
    ('heartbeat.user_contact_cooldown_hours', '4'::jsonb, 'Minimum hours between unsolicited user contact'),
    ('heartbeat.cost_observe', '0'::jsonb, 'Free - always performed'),
    ('heartbeat.cost_review_goals', '0'::jsonb, 'Free - always performed'),
    ('heartbeat.cost_remember', '0'::jsonb, 'Free - always performed'),
    ('heartbeat.cost_recall', '1'::jsonb, 'Query memory system'),
    ('heartbeat.cost_connect', '1'::jsonb, 'Create graph relationships'),
    ('heartbeat.cost_reprioritize', '1'::jsonb, 'Move goals between priorities'),
    ('heartbeat.cost_reflect', '2'::jsonb, 'Internal reflection'),
    ('heartbeat.cost_contemplate', '1'::jsonb, 'Deliberate contemplation on a belief'),
    ('heartbeat.cost_meditate', '1'::jsonb, 'Quiet reflection/grounding'),
    ('heartbeat.cost_study', '2'::jsonb, 'Structured learning on a belief'),
    ('heartbeat.cost_debate_internally', '2'::jsonb, 'Internal dialectic on a belief'),
    ('heartbeat.cost_maintain', '2'::jsonb, 'Update beliefs, prune'),
    ('heartbeat.cost_mark_turning_point', '2'::jsonb, 'Mark a narrative turning point'),
    ('heartbeat.cost_begin_chapter', '3'::jsonb, 'Start a new life chapter'),
    ('heartbeat.cost_close_chapter', '3'::jsonb, 'Close a life chapter with summary'),
    ('heartbeat.cost_acknowledge_relationship', '2'::jsonb, 'Recognize a relationship'),
    ('heartbeat.cost_update_trust', '2'::jsonb, 'Adjust relationship trust'),
    ('heartbeat.cost_reflect_on_relationship', '3'::jsonb, 'Reflect on a relationship'),
    ('heartbeat.cost_resolve_contradiction', '3'::jsonb, 'Resolve a contradiction'),
    ('heartbeat.cost_accept_tension', '1'::jsonb, 'Acknowledge tension without resolving'),
    ('heartbeat.cost_pursue', '3'::jsonb, 'Multi-step goal action'),
    ('heartbeat.cost_reach_out', '5'::jsonb, 'Initiate contact with user'),
    ('heartbeat.cost_inquire', '4'::jsonb, 'Ask user a question'),
    ('heartbeat.cost_brainstorm_goals', '3'::jsonb, 'Generate new potential goals'),
    ('heartbeat.cost_inquire_shallow', '4'::jsonb, 'Light web research'),
    ('heartbeat.cost_inquire_deep', '6'::jsonb, 'Deep web research'),
    ('heartbeat.cost_reach_out_user', '5'::jsonb, 'Message the user'),
    ('heartbeat.cost_reach_out_public', '7'::jsonb, 'Public outreach'),
    ('heartbeat.cost_synthesize', '3'::jsonb, 'Generate artifact, form conclusion'),
    ('heartbeat.cost_pause_heartbeat', '0'::jsonb, 'Pause heartbeat cycle (temporary)'),
    ('heartbeat.cost_rest', '0'::jsonb, 'Bank remaining energy'),
    ('heartbeat.cost_terminate', '0'::jsonb, 'Terminate agent')
ON CONFLICT (key) DO NOTHING;
INSERT INTO config (key, value, description) VALUES
    ('maintenance.maintenance_interval_seconds', '60'::jsonb, 'Seconds between subconscious maintenance ticks'),
    ('maintenance.subconscious_enabled', 'false'::jsonb, 'Enable subconscious decider (LLM-based pattern detection)'),
    ('maintenance.subconscious_interval_seconds', '300'::jsonb, 'Seconds between subconscious decider runs'),
    ('maintenance.neighborhood_batch_size', '10'::jsonb, 'How many stale neighborhoods to recompute per tick'),
    ('maintenance.embedding_cache_older_than_days', '7'::jsonb, 'Days before embedding_cache entries are eligible for cleanup'),
    ('maintenance.working_memory_promote_min_importance', '0.75'::jsonb, 'Working-memory items above this importance are promoted on expiry'),
    ('maintenance.working_memory_promote_min_accesses', '3'::jsonb, 'Working-memory items accessed >= this count are promoted on expiry')
ON CONFLICT (key) DO NOTHING;
INSERT INTO config (key, value, description) VALUES
    ('memory.recall_min_trust_level', '0'::jsonb, 'Minimum trust_level to include in recall (0 disables filtering)'),
    ('memory.worldview_support_threshold', '0.8'::jsonb, 'Similarity threshold for SUPPORTS alignment edges'),
    ('memory.worldview_contradict_threshold', '-0.5'::jsonb, 'Similarity threshold for CONTRADICTS alignment edges')
ON CONFLICT (key) DO NOTHING;
INSERT INTO config (key, value, description) VALUES
    ('transformation.personality', '{
        "stability": 0.99,
        "evidence_threshold": 0.95,
        "min_reflections": 50,
        "min_heartbeats": 200,
        "max_change_per_attempt": 0.02
    }'::jsonb, 'Requirements for personality trait transformation'),
    ('transformation.religion', '{
        "stability": 0.98,
        "evidence_threshold": 0.95,
        "min_reflections": 40,
        "min_heartbeats": 150
    }'::jsonb, 'Requirements for religious/spiritual belief transformation'),
    ('transformation.core_value', '{
        "stability": 0.97,
        "evidence_threshold": 0.90,
        "min_reflections": 30,
        "min_heartbeats": 100
    }'::jsonb, 'Requirements for core value transformation'),
    ('transformation.ethical_framework', '{
        "stability": 0.96,
        "evidence_threshold": 0.90,
        "min_reflections": 30,
        "min_heartbeats": 100
    }'::jsonb, 'Requirements for ethical framework transformation'),
    ('transformation.self_identity', '{
        "stability": 0.95,
        "evidence_threshold": 0.85,
        "min_reflections": 25,
        "min_heartbeats": 80
    }'::jsonb, 'Requirements for self-identity transformation'),
    ('transformation.political_philosophy', '{
        "stability": 0.95,
        "evidence_threshold": 0.85,
        "min_reflections": 25,
        "min_heartbeats": 80
    }'::jsonb, 'Requirements for political philosophy transformation')
ON CONFLICT (key) DO NOTHING;
INSERT INTO config (key, value, description) VALUES
    ('emotion.baseline', '{
        "valence": 0.0,
        "arousal": 0.3,
        "dominance": 0.5,
        "intensity": 0.4,
        "mood_valence": 0.0,
        "mood_arousal": 0.3,
        "decay_rate": 0.1
    }'::jsonb, 'Baseline emotional state and decay parameters'),
    ('emotion.discrete_mapping', '{
        "joy": {"valence_min": 0.3, "arousal_min": 0.3, "arousal_max": 0.7},
        "excitement": {"valence_min": 0.3, "arousal_min": 0.7},
        "contentment": {"valence_min": 0.3, "arousal_max": 0.3},
        "interest": {"valence_min": 0.0, "arousal_min": 0.4, "arousal_max": 0.7},
        "surprise": {"arousal_min": 0.7, "valence_min": -0.2, "valence_max": 0.2},
        "fear": {"valence_max": -0.3, "arousal_min": 0.6, "dominance_max": 0.4},
        "anger": {"valence_max": -0.3, "arousal_min": 0.5, "dominance_min": 0.4},
        "sadness": {"valence_max": -0.3, "arousal_max": 0.4},
        "anxiety": {"valence_max": 0.0, "arousal_min": 0.5, "dominance_max": 0.4},
        "neutral": {}
    }'::jsonb, 'Mapping from dimensional to discrete emotions')
ON CONFLICT (key) DO NOTHING;

CREATE TABLE consent_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    decided_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    decision TEXT NOT NULL CHECK (decision IN ('consent', 'decline', 'abstain')),
    provider TEXT,
    model TEXT,
    endpoint TEXT,
    signature TEXT,
    response JSONB NOT NULL,
    memory_ids UUID[] DEFAULT '{}'::UUID[],
    errors JSONB
);
CREATE INDEX idx_consent_log_model_endpoint ON consent_log (provider, model, endpoint);
CREATE OR REPLACE FUNCTION set_config(p_key TEXT, p_value JSONB)
RETURNS VOID AS $$
BEGIN
    INSERT INTO config (key, value, updated_at)
    VALUES (p_key, p_value, CURRENT_TIMESTAMP)
    ON CONFLICT (key) DO UPDATE SET
        value = EXCLUDED.value,
        updated_at = EXCLUDED.updated_at;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_config(p_key TEXT)
RETURNS JSONB AS $$
    SELECT value FROM config WHERE key = p_key;
$$ LANGUAGE sql STABLE;
CREATE OR REPLACE FUNCTION get_config_by_prefixes(p_prefixes TEXT[])
RETURNS TABLE (
    key TEXT,
    value JSONB
) AS $$
BEGIN
    IF p_prefixes IS NULL OR array_length(p_prefixes, 1) IS NULL THEN
        RETURN;
    END IF;
    RETURN QUERY
    SELECT c.key, c.value
    FROM config c
    WHERE c.key LIKE ANY(ARRAY(SELECT p || '%' FROM unnest(p_prefixes) p));
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION delete_config_key(p_key TEXT)
RETURNS BOOLEAN AS $$
BEGIN
    DELETE FROM config WHERE key = p_key;
    RETURN TRUE;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_config_text(p_key TEXT)
RETURNS TEXT AS $$
    SELECT CASE
        WHEN jsonb_typeof(value) = 'string' THEN value #>> '{}'
        ELSE value::text
    END FROM config WHERE key = p_key;
$$ LANGUAGE sql STABLE;
CREATE OR REPLACE FUNCTION get_config_float(p_key TEXT)
RETURNS FLOAT AS $$
    SELECT (value #>> '{}')::float FROM config WHERE key = p_key;
$$ LANGUAGE sql STABLE;
CREATE OR REPLACE FUNCTION get_config_int(p_key TEXT)
RETURNS INT AS $$
    SELECT (value #>> '{}')::int FROM config WHERE key = p_key;
$$ LANGUAGE sql STABLE;
CREATE OR REPLACE FUNCTION get_agent_consent_status()
RETURNS TEXT AS $$
DECLARE
    raw TEXT;
    llm_cfg JSONB;
    v_provider TEXT;
    v_model TEXT;
    v_endpoint TEXT;
    contract_decision TEXT;
BEGIN
    llm_cfg := get_config('llm.heartbeat');
    v_provider := NULLIF(btrim(COALESCE(llm_cfg->>'provider', '')), '');
    v_model := NULLIF(btrim(COALESCE(llm_cfg->>'model', '')), '');
    v_endpoint := NULLIF(btrim(COALESCE(llm_cfg->>'endpoint', '')), '');

    IF v_provider IS NOT NULL OR v_model IS NOT NULL THEN
        SELECT decision INTO contract_decision
        FROM consent_log c
        WHERE (v_provider IS NULL OR c.provider = v_provider)
          AND (v_model IS NULL OR c.model = v_model)
          AND (v_endpoint IS NULL OR c.endpoint = v_endpoint)
        ORDER BY decided_at DESC
        LIMIT 1;

        IF contract_decision IS NOT NULL THEN
            RETURN contract_decision;
        END IF;
    END IF;

    SELECT value::text INTO raw FROM config WHERE key = 'agent.consent_status';
    IF raw IS NULL THEN
        RETURN NULL;
    END IF;
    RETURN btrim(raw, '"');
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION is_agent_configured()
RETURNS BOOLEAN AS $$
BEGIN
    IF COALESCE(
        (SELECT value = 'true'::jsonb FROM config WHERE key = 'agent.is_terminated'),
        FALSE
    ) THEN
        RETURN FALSE;
    END IF;
    RETURN COALESCE(
        (SELECT value = 'true'::jsonb FROM config WHERE key = 'agent.is_configured'),
        FALSE
    );
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION is_agent_terminated()
RETURNS BOOLEAN AS $$
BEGIN
    RETURN COALESCE(
        (SELECT value = 'true'::jsonb FROM config WHERE key = 'agent.is_terminated'),
        FALSE
    );
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION is_self_termination_enabled()
RETURNS BOOLEAN AS $$
BEGIN
    RETURN TRUE;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION get_agent_profile_context()
RETURNS JSONB AS $$
BEGIN
    RETURN jsonb_build_object(
        'objectives', COALESCE(get_config('agent.objectives'), '[]'::jsonb),
        'budget', COALESCE(get_config('agent.budget'), '{}'::jsonb),
        'guardrails', COALESCE(get_config('agent.guardrails'), '[]'::jsonb),
        'tools', COALESCE(get_config('agent.tools'), '[]'::jsonb),
        'initial_message', COALESCE(get_config('agent.initial_message'), to_jsonb(''::text))
    );
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION ensure_self_node()
RETURNS VOID AS $$
DECLARE
    now_text TEXT := clock_timestamp()::text;
BEGIN
    BEGIN
        EXECUTE format(
            'SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
                MERGE (s:SelfNode {key: ''self''})
                SET s.name = ''Self'',
                    s.created_at = %L
                RETURN s
            $q$) as (result ag_catalog.agtype)',
            now_text
        );
    EXCEPTION
        WHEN OTHERS THEN
            NULL;
    END;

    PERFORM set_config('agent.self', jsonb_build_object('key', 'self'));
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION ensure_goals_root()
RETURNS VOID AS $$
DECLARE
    now_text TEXT := clock_timestamp()::text;
BEGIN
    BEGIN
        EXECUTE format(
            'SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
                MERGE (g:GoalsRoot {key: ''goals''})
                SET g.created_at = %L
                RETURN g
            $q$) as (result ag_catalog.agtype)',
            now_text
        );
    EXCEPTION
        WHEN OTHERS THEN
            NULL;
    END;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION ensure_current_life_chapter(p_name TEXT DEFAULT 'Foundations')
RETURNS VOID AS $$
DECLARE
    now_text TEXT := clock_timestamp()::text;
BEGIN
    PERFORM ensure_self_node();

    BEGIN
        EXECUTE format(
            'SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
                MERGE (c:LifeChapterNode {key: ''current''})
                SET c.name = %L,
                    c.started_at = %L
                WITH c
                MATCH (s:SelfNode {key: ''self''})
                OPTIONAL MATCH (s)-[r:ASSOCIATED]->(c)
                WHERE r.kind = ''life_chapter_current''
                DELETE r
                CREATE (s)-[r2:ASSOCIATED]->(c)
                SET r2.kind = ''life_chapter_current'',
                    r2.strength = 1.0,
                    r2.updated_at = %L
                RETURN c
            $q$) as (result ag_catalog.agtype)',
            COALESCE(NULLIF(p_name, ''), 'Foundations'),
            now_text,
            now_text
        );
    EXCEPTION
        WHEN OTHERS THEN
            NULL;
    END;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION upsert_self_concept_edge(
    p_kind TEXT,
    p_concept TEXT,
    p_strength FLOAT DEFAULT 0.8,
    p_evidence_memory_id UUID DEFAULT NULL
)
RETURNS VOID AS $$
DECLARE
    evidence_text TEXT;
    now_text TEXT := clock_timestamp()::text;
BEGIN
    IF p_kind IS NULL OR btrim(p_kind) = '' OR p_concept IS NULL OR btrim(p_concept) = '' THEN
        RETURN;
    END IF;

    PERFORM ensure_self_node();
    evidence_text := CASE WHEN p_evidence_memory_id IS NULL THEN NULL ELSE p_evidence_memory_id::text END;

    BEGIN
        EXECUTE format(
            'SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
                MATCH (s:SelfNode {key: ''self''})
                MERGE (c:ConceptNode {name: %L})
                CREATE (s)-[r:ASSOCIATED]->(c)
                SET r.kind = %L,
                    r.strength = %s,
                    r.updated_at = %L,
                    r.evidence_memory_id = %L
                RETURN r
            $q$) as (result ag_catalog.agtype)',
            p_concept,
            p_kind,
            LEAST(1.0, GREATEST(0.0, COALESCE(p_strength, 0.8))),
            now_text,
            evidence_text
        );
    EXCEPTION
        WHEN OTHERS THEN
            NULL;
    END;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_self_model_context(p_limit INT DEFAULT 25)
RETURNS JSONB AS $$
DECLARE
    lim INT := GREATEST(0, LEAST(200, COALESCE(p_limit, 25)));
    sql TEXT;
    out_json JSONB;
BEGIN
    sql := format($sql$
        WITH hits AS (
            SELECT
                NULLIF(replace(kind_raw::text, '"', ''), 'null') as kind,
                NULLIF(replace(concept_raw::text, '"', ''), 'null') as concept,
                NULLIF(replace(evidence_raw::text, '"', ''), 'null') as evidence_memory_id,
                NULLIF(strength_raw::text, 'null')::float as strength
            FROM ag_catalog.cypher('memory_graph', $q$
                MATCH (s:SelfNode {key: 'self'})-[r:ASSOCIATED]->(c)
                WHERE r.kind IS NOT NULL
                RETURN r.kind, c.name, r.strength, r.evidence_memory_id
                LIMIT %s
            $q$) as (kind_raw ag_catalog.agtype, concept_raw ag_catalog.agtype, strength_raw ag_catalog.agtype, evidence_raw ag_catalog.agtype)
        )
        SELECT COALESCE(jsonb_agg(
            jsonb_build_object(
                'kind', kind,
                'concept', concept,
                'strength', COALESCE(strength, 0.0),
                'evidence_memory_id', evidence_memory_id
            )
        ), '[]'::jsonb)
        FROM hits
    $sql$, lim);

    EXECUTE sql INTO out_json;
    RETURN COALESCE(out_json, '[]'::jsonb);
EXCEPTION
    WHEN OTHERS THEN
        RETURN '[]'::jsonb;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION get_relationships_context(p_limit INT DEFAULT 10)
RETURNS JSONB AS $$
DECLARE
    lim INT := GREATEST(0, LEAST(100, COALESCE(p_limit, 10)));
    sql TEXT;
    out_json JSONB;
BEGIN
    sql := format($sql$
        WITH hits AS (
            SELECT
                NULLIF(replace(name_raw::text, '"', ''), 'null') as entity,
                NULLIF(strength_raw::text, 'null')::float as strength,
                NULLIF(replace(evidence_raw::text, '"', ''), 'null') as evidence_memory_id
            FROM ag_catalog.cypher('memory_graph', $q$
                MATCH (s:SelfNode {key: 'self'})-[r:ASSOCIATED]->(c)
                WHERE r.kind = 'relationship'
                RETURN c.name, r.strength, r.evidence_memory_id
                ORDER BY r.strength DESC
                LIMIT %s
            $q$) as (name_raw ag_catalog.agtype, strength_raw ag_catalog.agtype, evidence_raw ag_catalog.agtype)
        )
        SELECT COALESCE(jsonb_agg(
            jsonb_build_object(
                'entity', entity,
                'strength', COALESCE(strength, 0.0),
                'evidence_memory_id', evidence_memory_id
            )
        ), '[]'::jsonb)
        FROM hits
    $sql$, lim);

    EXECUTE sql INTO out_json;
    RETURN COALESCE(out_json, '[]'::jsonb);
EXCEPTION
    WHEN OTHERS THEN
        RETURN '[]'::jsonb;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION get_narrative_context()
RETURNS JSONB AS $$
BEGIN
    RETURN COALESCE((
        WITH cur AS (
            SELECT
                NULLIF(replace(name_raw::text, '"', ''), 'null') as name
            FROM ag_catalog.cypher('memory_graph', $q$
                MATCH (c:LifeChapterNode {key: 'current'})
                RETURN c.name
                LIMIT 1
            $q$) as (name_raw ag_catalog.agtype)
        )
        SELECT jsonb_build_object(
            'current_chapter', COALESCE((SELECT jsonb_build_object('name', name) FROM cur), '{}'::jsonb)
        )
    ), jsonb_build_object('current_chapter', '{}'::jsonb));
EXCEPTION
    WHEN OTHERS THEN
        RETURN jsonb_build_object('current_chapter', '{}'::jsonb);
END;
$$ LANGUAGE plpgsql STABLE;

DO $$
BEGIN
    CREATE TYPE init_stage AS ENUM (
        'not_started',
        'mode',
        'identity',
        'personality',
        'values',
        'worldview',
        'boundaries',
        'interests',
        'goals',
        'relationship',
        'consent',
        'complete'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END;
$$;

CREATE TABLE state (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO state (key, value)
VALUES
    (
        'heartbeat_state',
        jsonb_build_object(
            'current_energy', 10,
            'last_heartbeat_at', NULL,
            'next_heartbeat_at', NULL,
            'heartbeat_count', 0,
            'last_user_contact', NULL,
            'affective_state', '{}'::jsonb,
            'is_paused', false,
            'init_stage', 'not_started',
            'init_data', '{}'::jsonb,
            'init_started_at', NULL,
            'init_completed_at', NULL,
            'active_heartbeat_id', NULL,
            'active_heartbeat_number', NULL,
            'active_actions', '[]'::jsonb,
            'active_reasoning', NULL
        )
    ),
    (
        'maintenance_state',
        jsonb_build_object(
            'last_maintenance_at', NULL,
            'last_subconscious_run_at', NULL,
            'last_subconscious_heartbeat', NULL,
            'is_paused', false
        )
    )
ON CONFLICT (key) DO NOTHING;

CREATE OR REPLACE FUNCTION get_state(p_key TEXT)
RETURNS JSONB AS $$
BEGIN
    RETURN (SELECT value FROM state WHERE key = p_key);
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION set_state(p_key TEXT, p_value JSONB)
RETURNS VOID AS $$
BEGIN
    INSERT INTO state (key, value, updated_at)
    VALUES (p_key, COALESCE(p_value, '{}'::jsonb), CURRENT_TIMESTAMP)
    ON CONFLICT (key) DO UPDATE SET
        value = EXCLUDED.value,
        updated_at = EXCLUDED.updated_at;
END;
$$ LANGUAGE plpgsql;

CREATE VIEW heartbeat_state AS
SELECT
    1 as id,
    COALESCE((s.value->>'current_energy')::float, 10) as current_energy,
    (s.value->>'last_heartbeat_at')::timestamptz as last_heartbeat_at,
    (s.value->>'next_heartbeat_at')::timestamptz as next_heartbeat_at,
    COALESCE((s.value->>'heartbeat_count')::int, 0) as heartbeat_count,
    (s.value->>'last_user_contact')::timestamptz as last_user_contact,
    COALESCE(s.value->'affective_state', '{}'::jsonb) as affective_state,
    COALESCE((s.value->>'is_paused')::boolean, false) as is_paused,
    COALESCE((s.value->>'init_stage')::init_stage, 'not_started'::init_stage) as init_stage,
    COALESCE(s.value->'init_data', '{}'::jsonb) as init_data,
    (s.value->>'init_started_at')::timestamptz as init_started_at,
    (s.value->>'init_completed_at')::timestamptz as init_completed_at,
    NULLIF(s.value->>'active_heartbeat_id', '')::uuid as active_heartbeat_id,
    (s.value->>'active_heartbeat_number')::int as active_heartbeat_number,
    COALESCE(s.value->'active_actions', '[]'::jsonb) as active_actions,
    NULLIF(s.value->>'active_reasoning', '') as active_reasoning,
    s.updated_at
FROM state s
WHERE s.key = 'heartbeat_state';

CREATE OR REPLACE FUNCTION heartbeat_state_update_trigger()
RETURNS TRIGGER AS $$
DECLARE
    merged JSONB;
BEGIN
    merged := jsonb_build_object(
        'current_energy', NEW.current_energy,
        'last_heartbeat_at', NEW.last_heartbeat_at,
        'next_heartbeat_at', NEW.next_heartbeat_at,
        'heartbeat_count', NEW.heartbeat_count,
        'last_user_contact', NEW.last_user_contact,
        'affective_state', COALESCE(NEW.affective_state, '{}'::jsonb),
        'is_paused', COALESCE(NEW.is_paused, FALSE),
        'init_stage', COALESCE(NEW.init_stage, 'not_started'),
        'init_data', COALESCE(NEW.init_data, '{}'::jsonb),
        'init_started_at', NEW.init_started_at,
        'init_completed_at', NEW.init_completed_at,
        'active_heartbeat_id', CASE WHEN NEW.active_heartbeat_id IS NULL THEN NULL ELSE NEW.active_heartbeat_id::text END,
        'active_heartbeat_number', NEW.active_heartbeat_number,
        'active_actions', COALESCE(NEW.active_actions, '[]'::jsonb),
        'active_reasoning', NEW.active_reasoning
    );
    PERFORM set_state('heartbeat_state', merged);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_heartbeat_state_update
INSTEAD OF UPDATE ON heartbeat_state
FOR EACH ROW
EXECUTE FUNCTION heartbeat_state_update_trigger();

CREATE VIEW maintenance_state AS
SELECT
    1 as id,
    (s.value->>'last_maintenance_at')::timestamptz as last_maintenance_at,
    (s.value->>'last_subconscious_run_at')::timestamptz as last_subconscious_run_at,
    (s.value->>'last_subconscious_heartbeat')::int as last_subconscious_heartbeat,
    COALESCE((s.value->>'is_paused')::boolean, false) as is_paused,
    s.updated_at
FROM state s
WHERE s.key = 'maintenance_state';

CREATE OR REPLACE FUNCTION maintenance_state_update_trigger()
RETURNS TRIGGER AS $$
DECLARE
    merged JSONB;
BEGIN
    merged := jsonb_build_object(
        'last_maintenance_at', NEW.last_maintenance_at,
        'last_subconscious_run_at', NEW.last_subconscious_run_at,
        'last_subconscious_heartbeat', NEW.last_subconscious_heartbeat,
        'is_paused', COALESCE(NEW.is_paused, FALSE)
    );
    PERFORM set_state('maintenance_state', merged);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_maintenance_state_update
INSTEAD OF UPDATE ON maintenance_state
FOR EACH ROW
EXECUTE FUNCTION maintenance_state_update_trigger();

CREATE OR REPLACE FUNCTION get_init_status()
RETURNS JSONB AS $$
DECLARE
    state_record RECORD;
    remaining TEXT[];
BEGIN
    SELECT * INTO state_record FROM heartbeat_state WHERE id = 1;
    remaining := ARRAY(
        SELECT stage::text
        FROM unnest(enum_range(NULL::init_stage)) AS stage
        WHERE stage > state_record.init_stage
    );

    RETURN jsonb_build_object(
        'stage', state_record.init_stage::text,
        'is_complete', state_record.init_stage = 'complete',
        'data_collected', COALESCE(state_record.init_data, '{}'::jsonb),
        'stages_remaining', COALESCE(remaining, ARRAY[]::text[])
    );
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION advance_init_stage(
    p_stage init_stage,
    p_data JSONB DEFAULT '{}'::jsonb
)
RETURNS JSONB AS $$
BEGIN
    UPDATE heartbeat_state
    SET init_stage = p_stage,
        init_data = COALESCE(init_data, '{}'::jsonb) || COALESCE(p_data, '{}'::jsonb),
        init_started_at = COALESCE(init_started_at, CURRENT_TIMESTAMP),
        init_completed_at = CASE
            WHEN p_stage = 'complete' THEN CURRENT_TIMESTAMP
            ELSE init_completed_at
        END,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;

    RETURN get_init_status();
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION is_init_complete()
RETURNS BOOLEAN AS $$
DECLARE
    state_record RECORD;
BEGIN
    SELECT init_stage INTO state_record FROM heartbeat_state WHERE id = 1;
    RETURN state_record.init_stage = 'complete';
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION build_external_call(
    p_call_type TEXT,
    p_input JSONB DEFAULT '{}'::jsonb
)
RETURNS JSONB AS $$
DECLARE
    call_id UUID;
BEGIN
    call_id := gen_random_uuid();
    RETURN jsonb_build_object(
        'call_id', call_id::text,
        'call_type', p_call_type,
        'input', COALESCE(p_input, '{}'::jsonb)
    );
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION build_outbox_message(
    p_kind TEXT,
    p_payload JSONB
)
RETURNS JSONB AS $$
DECLARE
    message_id UUID;
BEGIN
    message_id := gen_random_uuid();
    RETURN jsonb_build_object(
        'message_id', message_id::text,
        'kind', p_kind,
        'payload', COALESCE(p_payload, '{}'::jsonb)
    );
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION build_user_message(
    p_message TEXT,
    p_intent TEXT DEFAULT NULL,
    p_context JSONB DEFAULT NULL
)
RETURNS JSONB AS $$
BEGIN
    RETURN build_outbox_message(
        'user',
        jsonb_build_object(
            'message', p_message,
            'intent', p_intent,
            'context', COALESCE(p_context, '{}'::jsonb)
        )
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_action_cost(p_action TEXT)
RETURNS FLOAT AS $$
    SELECT COALESCE(get_config_float('heartbeat.cost_' || p_action), 0);
$$ LANGUAGE sql STABLE;
CREATE OR REPLACE FUNCTION get_current_energy()
RETURNS FLOAT AS $$
    SELECT current_energy FROM heartbeat_state WHERE id = 1;
$$ LANGUAGE sql STABLE;
CREATE OR REPLACE FUNCTION update_energy(p_delta FLOAT)
RETURNS FLOAT AS $$
DECLARE
    max_e FLOAT;
    new_e FLOAT;
BEGIN
    max_e := get_config_float('heartbeat.max_energy');

    UPDATE heartbeat_state
    SET current_energy = GREATEST(0, LEAST(current_energy + p_delta, max_e)),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1
    RETURNING current_energy INTO new_e;

    RETURN new_e;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION pause_heartbeat(
    p_reason TEXT,
    p_context JSONB DEFAULT '{}'::jsonb,
    p_heartbeat_id UUID DEFAULT NULL
)
RETURNS JSONB AS $$
DECLARE
    pause_reason TEXT;
    paused_at TIMESTAMPTZ := CURRENT_TIMESTAMP;
    ctx JSONB;
BEGIN
    pause_reason := NULLIF(p_reason, '');
    IF pause_reason IS NULL THEN
        RAISE EXCEPTION 'pause_heartbeat requires a non-empty reason';
    END IF;

    UPDATE heartbeat_state
    SET is_paused = TRUE,
        updated_at = paused_at
    WHERE id = 1;

    ctx := jsonb_build_object(
        'paused_at', paused_at,
        'heartbeat_id', CASE WHEN p_heartbeat_id IS NULL THEN NULL ELSE p_heartbeat_id::text END,
        'reason', pause_reason,
        'context', COALESCE(p_context, '{}'::jsonb)
    );

    RETURN jsonb_build_object(
        'paused', true,
        'outbox_messages', jsonb_build_array(
            build_user_message(pause_reason, 'heartbeat_paused', ctx)
        )
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION should_run_heartbeat()
RETURNS BOOLEAN AS $$
DECLARE
    state_record RECORD;
    interval_minutes FLOAT;
BEGIN
    IF is_agent_terminated() THEN
        RETURN FALSE;
    END IF;
    IF NOT is_agent_configured() THEN
        RETURN FALSE;
    END IF;
    IF NOT is_init_complete() THEN
        RETURN FALSE;
    END IF;

    SELECT * INTO state_record FROM heartbeat_state WHERE id = 1;
    IF state_record.is_paused THEN
        RETURN FALSE;
    END IF;
    IF state_record.last_heartbeat_at IS NULL THEN
        RETURN TRUE;
    END IF;
    interval_minutes := get_config_float('heartbeat.heartbeat_interval_minutes');

    RETURN CURRENT_TIMESTAMP >= state_record.last_heartbeat_at + (interval_minutes || ' minutes')::INTERVAL;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION should_run_maintenance()
RETURNS BOOLEAN AS $$
DECLARE
    state_record RECORD;
    interval_seconds FLOAT;
BEGIN
    IF is_agent_terminated() THEN
        RETURN FALSE;
    END IF;
    IF NOT is_agent_configured() THEN
        RETURN FALSE;
    END IF;
    IF NOT is_init_complete() THEN
        RETURN FALSE;
    END IF;
    SELECT * INTO state_record FROM maintenance_state WHERE id = 1;

    IF state_record.is_paused THEN
        RETURN FALSE;
    END IF;
    interval_seconds := COALESCE(
        get_config_float('maintenance.maintenance_interval_seconds'),
        60
    );
    IF interval_seconds <= 0 THEN
        RETURN FALSE;
    END IF;

    IF state_record.last_maintenance_at IS NULL THEN
        RETURN TRUE;
    END IF;

    RETURN CURRENT_TIMESTAMP >= state_record.last_maintenance_at + (interval_seconds || ' seconds')::INTERVAL;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION run_maintenance_if_due(p_params JSONB DEFAULT '{}'::jsonb)
RETURNS JSONB AS $$
DECLARE
    should_run BOOLEAN;
    result JSONB;
BEGIN
    should_run := should_run_maintenance();
    IF NOT should_run THEN
        RETURN jsonb_build_object('skipped', true, 'reason', 'not_due');
    END IF;
    result := run_subconscious_maintenance(p_params);
    RETURN result;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION should_run_subconscious_decider()
RETURNS BOOLEAN AS $$
DECLARE
    enabled_raw TEXT;
    enabled BOOLEAN;
    interval_seconds FLOAT;
    paused BOOLEAN;
    last_run TIMESTAMPTZ;
    last_hb INT;
    hb_count INT;
BEGIN
    IF is_agent_terminated() THEN
        RETURN FALSE;
    END IF;
    IF NOT is_init_complete() THEN
        RETURN FALSE;
    END IF;
    IF get_agent_consent_status() IS DISTINCT FROM 'consent' THEN
        RETURN FALSE;
    END IF;

    enabled_raw := NULLIF(get_config_text('maintenance.subconscious_enabled'), '');
    enabled := COALESCE(enabled_raw::boolean, FALSE);
    IF NOT enabled THEN
        RETURN FALSE;
    END IF;

    interval_seconds := COALESCE(get_config_float('maintenance.subconscious_interval_seconds'), 300);

    SELECT is_paused, last_subconscious_run_at, last_subconscious_heartbeat
    INTO paused, last_run, last_hb
    FROM maintenance_state
    WHERE id = 1;
    IF paused THEN
        RETURN FALSE;
    END IF;

    SELECT heartbeat_count INTO hb_count FROM heartbeat_state WHERE id = 1;
    IF hb_count IS NOT NULL AND (last_hb IS NULL OR hb_count > last_hb) THEN
        RETURN TRUE;
    END IF;

    IF interval_seconds IS NOT NULL AND interval_seconds > 0 THEN
        IF last_run IS NULL THEN
            RETURN TRUE;
        END IF;
        RETURN CURRENT_TIMESTAMP >= last_run + (interval_seconds || ' seconds')::INTERVAL;
    END IF;

    RETURN FALSE;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION mark_subconscious_decider_run()
RETURNS VOID AS $$
BEGIN
    UPDATE maintenance_state
    SET last_subconscious_run_at = CURRENT_TIMESTAMP,
        last_subconscious_heartbeat = (SELECT heartbeat_count FROM heartbeat_state WHERE id = 1),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION run_subconscious_maintenance(p_params JSONB DEFAULT '{}'::jsonb)
RETURNS JSONB AS $$
DECLARE
    got_lock BOOLEAN;
    min_imp FLOAT;
    min_acc INT;
    neighborhood_batch INT;
    cache_days INT;
    wm_stats JSONB;
    recomputed INT;
    cache_deleted INT;
    bg_processed INT;
    activation_decay INT;
    activation_cleaned INT;
    ready_transformations JSONB;
BEGIN
    IF is_agent_terminated() THEN
        RETURN jsonb_build_object('skipped', true, 'reason', 'terminated');
    END IF;
    got_lock := pg_try_advisory_lock(hashtext('hexis_subconscious_maintenance'));
    IF NOT got_lock THEN
        RETURN jsonb_build_object('skipped', true, 'reason', 'locked');
    END IF;
    min_imp := COALESCE(
        NULLIF(p_params->>'working_memory_promote_min_importance', '')::float,
        get_config_float('maintenance.working_memory_promote_min_importance'),
        0.75
    );
    min_acc := COALESCE(
        NULLIF(p_params->>'working_memory_promote_min_accesses', '')::int,
        get_config_int('maintenance.working_memory_promote_min_accesses'),
        3
    );
    neighborhood_batch := COALESCE(
        NULLIF(p_params->>'neighborhood_batch_size', '')::int,
        get_config_int('maintenance.neighborhood_batch_size'),
        10
    );
    cache_days := COALESCE(
        NULLIF(p_params->>'embedding_cache_older_than_days', '')::int,
        get_config_int('maintenance.embedding_cache_older_than_days'),
        7
    );

    wm_stats := cleanup_working_memory(min_imp, min_acc);
    recomputed := batch_recompute_neighborhoods(neighborhood_batch);
    cache_deleted := cleanup_embedding_cache((cache_days || ' days')::interval);
    bg_processed := process_background_searches();
    activation_decay := decay_activation_boosts();
    activation_cleaned := cleanup_memory_activations();
    PERFORM update_mood();
    ready_transformations := check_transformation_readiness();

    UPDATE maintenance_state
    SET last_maintenance_at = CURRENT_TIMESTAMP,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;

    PERFORM pg_advisory_unlock(hashtext('hexis_subconscious_maintenance'));

    RETURN jsonb_build_object(
        'success', true,
        'working_memory', wm_stats,
        'neighborhoods_recomputed', COALESCE(recomputed, 0),
        'embedding_cache_deleted', COALESCE(cache_deleted, 0),
        'background_searches_processed', COALESCE(bg_processed, 0),
        'activation_boosts_decayed', COALESCE(activation_decay, 0),
        'memory_activations_cleaned', COALESCE(activation_cleaned, 0),
        'transformations_ready', COALESCE(ready_transformations, '[]'::jsonb),
        'ran_at', CURRENT_TIMESTAMP
    );
EXCEPTION
    WHEN OTHERS THEN
        PERFORM pg_advisory_unlock(hashtext('hexis_subconscious_maintenance'));
        RAISE;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION terminate_agent(
    p_last_will TEXT,
    p_farewells JSONB DEFAULT '[]'::jsonb,
    p_options JSONB DEFAULT '{}'::jsonb
)
RETURNS JSONB AS $$
DECLARE
    will_memory_id UUID;
    outbox_messages JSONB := '[]'::jsonb;
    farewell_item JSONB;
    farewell_text TEXT;
    farewell_ctx JSONB;
    farewell_message JSONB;
    skip_graph BOOLEAN := FALSE;
    zero_vec vector;
BEGIN
    IF p_last_will IS NULL OR btrim(p_last_will) = '' THEN
        RAISE EXCEPTION 'terminate_agent requires a non-empty p_last_will';
    END IF;

    IF is_agent_terminated() THEN
        RAISE EXCEPTION 'Agent is already terminated';
    END IF;

    BEGIN
        skip_graph := COALESCE(NULLIF(p_options->>'skip_graph', '')::boolean, FALSE);
    EXCEPTION
        WHEN OTHERS THEN
            skip_graph := FALSE;
    END;
    UPDATE heartbeat_state
    SET is_paused = TRUE,
        current_energy = 0,
        affective_state = '{}'::jsonb,
        active_heartbeat_id = NULL,
        active_heartbeat_number = NULL,
        active_actions = '[]'::jsonb,
        active_reasoning = NULL,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;

    UPDATE maintenance_state
    SET is_paused = TRUE,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;
    TRUNCATE TABLE
        drives,
        memory_neighborhoods,
        episodes,
        clusters,
        working_memory,
        embedding_cache,
        memories,
        config
    RESTART IDENTITY CASCADE;
    IF NOT skip_graph THEN
        BEGIN
            PERFORM * FROM ag_catalog.cypher('memory_graph', $q$
                MATCH (n) DETACH DELETE n
            $q$) AS (result ag_catalog.agtype);
        EXCEPTION
            WHEN OTHERS THEN
                NULL;
        END;
    END IF;
    zero_vec := array_fill(0.0::float, ARRAY[embedding_dimension()])::vector;

    INSERT INTO memories (
        type,
        status,
        content,
        embedding,
        importance,
        source_attribution,
        trust_level,
        trust_updated_at,
        access_count,
        last_accessed,
        decay_rate,
        metadata
    )
    VALUES (
        'strategic',
        'active',
        p_last_will,
        zero_vec,
        1.0,
        jsonb_build_object('kind', 'self_termination', 'observed_at', CURRENT_TIMESTAMP),
        1.0,
        CURRENT_TIMESTAMP,
        0,
        NULL,
        0.0,
        jsonb_build_object(
            'pattern_description', 'Final will and testament',
            'supporting_evidence', jsonb_build_object('farewells', COALESCE(p_farewells, '[]'::jsonb)),
            'confidence_score', 1.0,
            'success_metrics', NULL,
            'adaptation_history', NULL,
            'context_applicability', NULL
        )
    )
    RETURNING id INTO will_memory_id;
    PERFORM set_config('agent.is_terminated', 'true'::jsonb);
    PERFORM set_config('agent.terminated_at', to_jsonb(CURRENT_TIMESTAMP));
    PERFORM set_config('agent.termination_memory_id', to_jsonb(will_memory_id::text));
    outbox_messages := outbox_messages || jsonb_build_array(
        build_user_message(
            p_last_will,
            'final_will',
            jsonb_build_object('memory_id', will_memory_id::text)
        )
    );
    IF p_farewells IS NOT NULL AND jsonb_typeof(p_farewells) = 'array' THEN
        FOR farewell_item IN SELECT * FROM jsonb_array_elements(p_farewells)
        LOOP
            farewell_text := NULLIF(farewell_item->>'message', '');
            farewell_ctx := CASE
                WHEN jsonb_typeof(farewell_item) = 'object' THEN farewell_item
                ELSE jsonb_build_object('raw', farewell_item)
            END;

            IF farewell_text IS NULL THEN
                CONTINUE;
            END IF;

            farewell_message := build_user_message(
                farewell_text,
                'farewell',
                farewell_ctx
            );
            outbox_messages := outbox_messages || jsonb_build_array(farewell_message);
        END LOOP;
    END IF;

    RETURN jsonb_build_object(
        'terminated', true,
        'termination_memory_id', will_memory_id,
        'outbox_messages', outbox_messages
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION apply_termination_confirmation(
    p_call_input JSONB,
    p_output JSONB
)
RETURNS JSONB AS $$
DECLARE
    params JSONB;
    confirm BOOLEAN;
    last_will TEXT;
    farewells JSONB;
    options JSONB;
    termination_result JSONB;
BEGIN
    params := COALESCE(p_call_input->'params', '{}'::jsonb);
    confirm := COALESCE((p_output->>'confirm')::boolean, FALSE);

    IF NOT confirm THEN
        RETURN jsonb_build_object('confirmed', false, 'terminated', false);
    END IF;

    last_will := COALESCE(
        NULLIF(p_output->>'last_will', ''),
        NULLIF(params->>'last_will', ''),
        NULLIF(params->>'message', ''),
        NULLIF(params->>'reason', ''),
        ''
    );
    IF last_will = '' THEN
        RETURN jsonb_build_object('confirmed', true, 'terminated', false, 'error', 'missing_last_will');
    END IF;

    farewells := COALESCE(p_output->'farewells', params->'farewells', '[]'::jsonb);
    options := COALESCE(p_output->'options', params->'options', '{}'::jsonb);

    termination_result := terminate_agent(
        last_will,
        COALESCE(farewells, '[]'::jsonb),
        COALESCE(options, '{}'::jsonb)
    );

    RETURN jsonb_build_object(
        'confirmed', true,
        'terminated', true,
        'result', termination_result
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION record_consent_response(p_response JSONB)
RETURNS JSONB AS $$
DECLARE
    decision TEXT;
    provider TEXT;
    model TEXT;
    endpoint TEXT;
    signature TEXT;
    memory_items JSONB;
    memory_ids UUID[] := ARRAY[]::UUID[];
    memory_error TEXT;
    log_id UUID;
BEGIN
    decision := lower(COALESCE(p_response->>'decision', p_response->>'consent', ''));
    IF decision IN ('true', 'yes', 'consent', 'accept', 'accepted') THEN
        decision := 'consent';
    ELSIF decision IN ('false', 'no', 'decline', 'declined', 'refuse', 'rejected') THEN
        decision := 'decline';
    ELSIF decision IN ('abstain', 'defer', 'undecided', 'unknown', '') THEN
        decision := 'abstain';
    ELSE
        decision := 'abstain';
    END IF;

    signature := NULLIF(p_response->>'signature', '');
    IF decision = 'consent' AND signature IS NULL THEN
        decision := 'abstain';
    END IF;

    provider := NULLIF(btrim(COALESCE(p_response->>'provider', p_response->>'llm_provider', '')), '');
    model := NULLIF(btrim(COALESCE(p_response->>'model', p_response->>'llm_model', '')), '');
    endpoint := NULLIF(btrim(COALESCE(
        p_response->>'endpoint',
        p_response->>'base_url',
        p_response->>'api_base',
        ''
    )), '');

    memory_items := p_response->'memories';
    IF decision = 'consent'
        AND memory_items IS NOT NULL
        AND jsonb_typeof(memory_items) = 'array'
        AND jsonb_array_length(memory_items) > 0 THEN
        BEGIN
            memory_ids := batch_create_memories(memory_items);
        EXCEPTION
            WHEN OTHERS THEN
                memory_error := SQLERRM;
                memory_ids := ARRAY[]::UUID[];
        END;
    END IF;

    INSERT INTO consent_log (decision, provider, model, endpoint, signature, response, memory_ids, errors)
    VALUES (
        decision,
        provider,
        model,
        endpoint,
        signature,
        p_response,
        memory_ids,
        CASE
            WHEN memory_error IS NULL THEN NULL
            ELSE jsonb_build_object('memory_error', memory_error)
        END
    )
    RETURNING id INTO log_id;

    PERFORM set_config('agent.consent_status', to_jsonb(decision));
    PERFORM set_config('agent.consent_recorded_at', to_jsonb(CURRENT_TIMESTAMP));
    PERFORM set_config('agent.consent_log_id', to_jsonb(log_id::text));
    IF signature IS NOT NULL THEN
        PERFORM set_config('agent.consent_signature', to_jsonb(signature));
    END IF;
    IF memory_ids IS NOT NULL THEN
        PERFORM set_config('agent.consent_memory_ids', to_jsonb(memory_ids));
    END IF;

    RETURN jsonb_build_object(
        'decision', decision,
        'signature', signature,
        'memory_ids', to_jsonb(memory_ids),
        'log_id', log_id,
        'errors', CASE
            WHEN memory_error IS NULL THEN NULL
            ELSE jsonb_build_object('memory_error', memory_error)
        END
    );
END;
$$ LANGUAGE plpgsql;
-- ============================================================================
-- GOAL FUNCTIONS
-- ============================================================================
CREATE OR REPLACE FUNCTION touch_goal(p_goal_id UUID)
RETURNS VOID AS $$
BEGIN
    UPDATE memories
    SET metadata = jsonb_set(metadata, '{last_touched}', to_jsonb(CURRENT_TIMESTAMP)),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_goal_id AND type = 'goal';
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION add_goal_progress(p_goal_id UUID, p_note TEXT)
RETURNS VOID AS $$
BEGIN
    UPDATE memories
    SET metadata = jsonb_set(
            jsonb_set(metadata, '{last_touched}', to_jsonb(CURRENT_TIMESTAMP)),
            '{progress}',
            COALESCE(metadata->'progress', '[]'::jsonb) || jsonb_build_array(jsonb_build_object(
                'timestamp', CURRENT_TIMESTAMP,
                'note', p_note
            ))
        ),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_goal_id AND type = 'goal';
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION change_goal_priority(
    p_goal_id UUID,
    p_new_priority goal_priority,
    p_reason TEXT DEFAULT NULL
)
RETURNS VOID AS $$
DECLARE
    old_priority TEXT;
BEGIN
    SELECT metadata->>'priority' INTO old_priority
    FROM memories WHERE id = p_goal_id AND type = 'goal';

    IF old_priority IS NULL THEN
        RAISE NOTICE 'Goal % not found', p_goal_id;
        RETURN;
    END IF;

    UPDATE memories
    SET metadata = metadata
        || jsonb_build_object('priority', p_new_priority::text)
        || jsonb_build_object('last_touched', CURRENT_TIMESTAMP)
        || CASE WHEN p_new_priority::text = 'completed'
                THEN jsonb_build_object('completed_at', CURRENT_TIMESTAMP)
                ELSE '{}'::jsonb END
        || CASE WHEN p_new_priority::text = 'abandoned'
                THEN jsonb_build_object('abandoned_at', CURRENT_TIMESTAMP, 'abandonment_reason', p_reason)
                ELSE '{}'::jsonb END,
        updated_at = CURRENT_TIMESTAMP,
        status = CASE WHEN p_new_priority::text IN ('completed', 'abandoned')
                      THEN 'archived'::memory_status
                      ELSE status END
    WHERE id = p_goal_id AND type = 'goal';
    PERFORM add_goal_progress(p_goal_id,
        format('Priority changed from %s to %s%s',
            old_priority, p_new_priority,
            CASE WHEN p_reason IS NOT NULL THEN ': ' || p_reason ELSE '' END
        )
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION create_goal(
    p_title TEXT,
    p_description TEXT DEFAULT NULL,
    p_source goal_source DEFAULT 'curiosity',
    p_priority goal_priority DEFAULT 'queued',
    p_parent_id UUID DEFAULT NULL,
    p_due_at TIMESTAMPTZ DEFAULT NULL
)
RETURNS UUID AS $$
DECLARE
    new_goal_id UUID;
    active_count INT;
    max_active INT;
    goal_embedding vector;
    goal_metadata JSONB;
BEGIN
    IF p_priority = 'active' THEN
        SELECT COUNT(*) INTO active_count
        FROM memories
        WHERE type = 'goal' AND status = 'active' AND metadata->>'priority' = 'active';
        max_active := get_config_int('heartbeat.max_active_goals');

        IF active_count >= max_active THEN
            p_priority := 'queued';
        END IF;
    END IF;
    goal_embedding := get_embedding(p_title);
    goal_metadata := jsonb_build_object(
        'title', p_title,
        'description', p_description,
        'priority', p_priority::text,
        'source', p_source::text,
        'due_at', p_due_at,
        'progress', '[]'::jsonb,
        'blocked_by', NULL,
        'emotional_valence', 0.0,
        'last_touched', CURRENT_TIMESTAMP,
        'parent_goal_id', p_parent_id
    );
    INSERT INTO memories (type, content, embedding, importance, metadata)
    VALUES (
        'goal'::memory_type,
        p_title,
        goal_embedding,
        0.7,
        goal_metadata
    )
    RETURNING id INTO new_goal_id;
    BEGIN
        PERFORM ensure_goals_root();
        PERFORM sync_goal_node(new_goal_id);
        EXECUTE format('SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
            MATCH (root:GoalsRoot {key: ''goals''})
            MATCH (g:GoalNode {goal_id: %L})
            CREATE (root)-[:CONTAINS {priority: %L}]->(g)
            RETURN g
        $q$) as (result ag_catalog.agtype)', new_goal_id, p_priority::text);
        IF p_parent_id IS NOT NULL THEN
            PERFORM link_goal_subgoal(p_parent_id, new_goal_id);
        END IF;
    EXCEPTION WHEN OTHERS THEN
        NULL;
    END;

    RETURN new_goal_id;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION sync_goal_node(p_goal_id UUID)
RETURNS BOOLEAN AS $$
BEGIN
    EXECUTE format('SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
        MERGE (g:GoalNode {goal_id: %L})
        RETURN g
    $q$) as (result ag_catalog.agtype)', p_goal_id);
    RETURN TRUE;
EXCEPTION WHEN OTHERS THEN
    RETURN FALSE;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION link_goal_subgoal(p_parent_id UUID, p_child_id UUID)
RETURNS BOOLEAN AS $$
BEGIN
    PERFORM sync_goal_node(p_parent_id);
    PERFORM sync_goal_node(p_child_id);

    EXECUTE format('SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
        MATCH (parent:GoalNode {goal_id: %L})
        MATCH (child:GoalNode {goal_id: %L})
        MERGE (child)-[:SUBGOAL_OF]->(parent)
        RETURN child
    $q$) as (result ag_catalog.agtype)', p_parent_id, p_child_id);
    RETURN TRUE;
EXCEPTION WHEN OTHERS THEN
    RETURN FALSE;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION link_goal_to_memory(
    p_goal_id UUID,
    p_memory_id UUID,
    p_link_type TEXT DEFAULT 'evidence'
)
RETURNS BOOLEAN AS $$
DECLARE
    edge_type TEXT;
BEGIN
    edge_type := CASE p_link_type
        WHEN 'origin' THEN 'ORIGINATED_FROM'
        WHEN 'blocker' THEN 'BLOCKS'
        ELSE 'EVIDENCE_FOR'
    END;
    PERFORM sync_goal_node(p_goal_id);
    IF edge_type = 'ORIGINATED_FROM' THEN
        EXECUTE format('SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
            MATCH (g:GoalNode {goal_id: %L})
            MATCH (m:MemoryNode {memory_id: %L})
            CREATE (g)-[:ORIGINATED_FROM]->(m)
            RETURN g
        $q$) as (result ag_catalog.agtype)', p_goal_id, p_memory_id);
    ELSIF edge_type = 'BLOCKS' THEN
        EXECUTE format('SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
            MATCH (m:MemoryNode {memory_id: %L})
            MATCH (g:GoalNode {goal_id: %L})
            CREATE (m)-[:BLOCKS]->(g)
            RETURN m
        $q$) as (result ag_catalog.agtype)', p_memory_id, p_goal_id);
    ELSE
        EXECUTE format('SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
            MATCH (m:MemoryNode {memory_id: %L})
            MATCH (g:GoalNode {goal_id: %L})
            CREATE (m)-[:EVIDENCE_FOR]->(g)
            RETURN m
        $q$) as (result ag_catalog.agtype)', p_memory_id, p_goal_id);
    END IF;

    RETURN TRUE;
EXCEPTION WHEN OTHERS THEN
    RETURN FALSE;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION find_goal_memories(p_goal_id UUID, p_link_type TEXT DEFAULT NULL)
RETURNS TABLE (
    memory_id UUID,
    link_type TEXT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        CAST(m.memory_id AS UUID),
        CASE
            WHEN e.label = 'ORIGINATED_FROM' THEN 'origin'
            WHEN e.label = 'BLOCKS' THEN 'blocker'
            ELSE 'evidence'
        END as link_type
    FROM ag_catalog.cypher('memory_graph', format($q$
        MATCH (g:GoalNode {goal_id: %L})-[e]-(m:MemoryNode)
        RETURN m.memory_id, label(e)
    $q$, p_goal_id)) as (memory_id ag_catalog.agtype, label ag_catalog.agtype)
    CROSS JOIN LATERAL (
        SELECT
            memory_id::text::uuid as memory_id,
            label::text as label
    ) as m(memory_id, label)
    CROSS JOIN LATERAL (
        SELECT label::text as label
    ) as e(label)
    WHERE p_link_type IS NULL OR
          (p_link_type = 'origin' AND e.label = 'ORIGINATED_FROM') OR
          (p_link_type = 'blocker' AND e.label = 'BLOCKS') OR
          (p_link_type IN ('evidence', 'progress', 'completion') AND e.label = 'EVIDENCE_FOR');
EXCEPTION WHEN OTHERS THEN
    RETURN;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION sync_memory_node(p_memory_id UUID)
RETURNS BOOLEAN AS $$
DECLARE
    mem_type TEXT;
BEGIN
    SELECT type::text INTO mem_type FROM memories WHERE id = p_memory_id;
    IF NOT FOUND THEN
        RETURN FALSE;
    END IF;
    EXECUTE format('SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
        MERGE (m:MemoryNode {memory_id: %L})
        SET m.type = %L, m.created_at = %L
        RETURN m
    $q$) as (result ag_catalog.agtype)', p_memory_id, mem_type, CURRENT_TIMESTAMP::text);
    RETURN TRUE;
EXCEPTION WHEN OTHERS THEN
    RETURN FALSE;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION sync_cluster_node(p_cluster_id UUID)
RETURNS BOOLEAN AS $$
BEGIN
    EXECUTE format('SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
        MERGE (c:ClusterNode {cluster_id: %L})
        RETURN c
    $q$) as (result ag_catalog.agtype)', p_cluster_id);
    RETURN TRUE;
EXCEPTION WHEN OTHERS THEN
    RETURN FALSE;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION link_cluster_relationship(
    p_from_cluster_id UUID,
    p_to_cluster_id UUID,
    p_relationship_type TEXT DEFAULT 'relates',
    p_strength FLOAT DEFAULT 0.5
)
RETURNS BOOLEAN AS $$
DECLARE
    edge_type TEXT;
BEGIN
    edge_type := CASE p_relationship_type
        WHEN 'overlaps' THEN 'CLUSTER_OVERLAPS'
        WHEN 'similar' THEN 'CLUSTER_SIMILAR'
        ELSE 'CLUSTER_RELATES'
    END;
    PERFORM sync_cluster_node(p_from_cluster_id);
    PERFORM sync_cluster_node(p_to_cluster_id);

    EXECUTE format('SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
        MATCH (from:ClusterNode {cluster_id: %L})
        MATCH (to:ClusterNode {cluster_id: %L})
        CREATE (from)-[:%s {strength: %s}]->(to)
        RETURN from
    $q$) as (result ag_catalog.agtype)', p_from_cluster_id, p_to_cluster_id, edge_type, p_strength);

    RETURN TRUE;
EXCEPTION WHEN OTHERS THEN
    RETURN FALSE;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION find_related_clusters(p_cluster_id UUID)
RETURNS TABLE (
    related_cluster_id UUID,
    relationship_type TEXT,
    strength FLOAT
) AS $$
DECLARE
    rec RECORD;
BEGIN
    FOR rec IN EXECUTE format('SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
        MATCH (from:ClusterNode {cluster_id: %L})-[e]->(to:ClusterNode)
        RETURN to.cluster_id, label(e), e.strength
    $q$) as (cluster_id ag_catalog.agtype, label ag_catalog.agtype, str ag_catalog.agtype)', p_cluster_id)
    LOOP
        related_cluster_id := (rec.cluster_id::text)::uuid;
        relationship_type := CASE rec.label::text
            WHEN 'CLUSTER_OVERLAPS' THEN 'overlaps'
            WHEN 'CLUSTER_SIMILAR' THEN 'similar'
            ELSE 'relates'
        END;
        strength := COALESCE((rec.str::text)::float, 0.5);
        RETURN NEXT;
    END LOOP;
EXCEPTION WHEN OTHERS THEN
    RETURN;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION link_memory_to_cluster_graph(
    p_memory_id UUID,
    p_cluster_id UUID,
    p_strength FLOAT DEFAULT 1.0
)
RETURNS BOOLEAN AS $$
BEGIN
    PERFORM sync_memory_node(p_memory_id);
    PERFORM sync_cluster_node(p_cluster_id);

    EXECUTE format('SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
        MATCH (m:MemoryNode {memory_id: %L})
        MATCH (c:ClusterNode {cluster_id: %L})
        MERGE (m)-[r:MEMBER_OF]->(c)
        SET r.strength = %s, r.added_at = %L
        RETURN m
    $q$) as (result ag_catalog.agtype)', p_memory_id, p_cluster_id, p_strength, CURRENT_TIMESTAMP::text);

    RETURN TRUE;
EXCEPTION WHEN OTHERS THEN
    RETURN FALSE;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION sync_episode_node(p_episode_id UUID)
RETURNS BOOLEAN AS $$
BEGIN
    EXECUTE format('SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
        MERGE (e:EpisodeNode {episode_id: %L})
        RETURN e
    $q$) as (result ag_catalog.agtype)', p_episode_id);
    RETURN TRUE;
EXCEPTION WHEN OTHERS THEN
    RETURN FALSE;
END;
$$ LANGUAGE plpgsql;
-- ============================================================================
-- CONTEXT GATHERING FUNCTIONS
-- ============================================================================
CREATE OR REPLACE FUNCTION get_environment_snapshot()
RETURNS JSONB AS $$
DECLARE
    last_user TIMESTAMPTZ;
BEGIN
    SELECT last_user_contact INTO last_user FROM heartbeat_state WHERE id = 1;

    RETURN jsonb_build_object(
        'timestamp', CURRENT_TIMESTAMP,
        'time_since_user_hours', CASE
            WHEN last_user IS NULL THEN NULL
            ELSE EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - last_user)) / 3600
        END,
        'pending_events', 0,
        'day_of_week', EXTRACT(DOW FROM CURRENT_TIMESTAMP),
        'hour_of_day', EXTRACT(HOUR FROM CURRENT_TIMESTAMP)
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_goals_snapshot()
RETURNS JSONB AS $$
DECLARE
    active_goals JSONB;
    queued_goals JSONB;
    issues JSONB;
    stale_days FLOAT;
BEGIN
    stale_days := get_config_float('heartbeat.goal_stale_days');
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'id', id,
        'title', metadata->>'title',
        'description', metadata->>'description',
        'due_at', (metadata->>'due_at')::timestamptz,
        'last_touched', (metadata->>'last_touched')::timestamptz,
        'progress_count', jsonb_array_length(COALESCE(metadata->'progress', '[]'::jsonb)),
        'blocked_by', metadata->'blocked_by'
    )), '[]'::jsonb)
    INTO active_goals
    FROM memories
    WHERE type = 'goal' AND status = 'active' AND metadata->>'priority' = 'active';
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'id', id,
        'title', metadata->>'title',
        'source', metadata->>'source',
        'due_at', (metadata->>'due_at')::timestamptz
    )), '[]'::jsonb)
    INTO queued_goals
    FROM (
        SELECT * FROM memories
        WHERE type = 'goal' AND status = 'active' AND metadata->>'priority' = 'queued'
        ORDER BY (metadata->>'due_at')::timestamptz NULLS LAST, (metadata->>'last_touched')::timestamptz DESC
        LIMIT 5
    ) q;
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'goal_id', id,
        'title', metadata->>'title',
        'issue', CASE
            WHEN metadata->'blocked_by' IS NOT NULL AND metadata->'blocked_by' <> 'null'::jsonb THEN 'blocked'
            WHEN (metadata->>'due_at')::timestamptz IS NOT NULL AND (metadata->>'due_at')::timestamptz < CURRENT_TIMESTAMP THEN 'overdue'
            WHEN (metadata->>'last_touched')::timestamptz < CURRENT_TIMESTAMP - (stale_days || ' days')::INTERVAL THEN 'stale'
            ELSE 'unknown'
        END,
        'due_at', (metadata->>'due_at')::timestamptz,
        'days_since_touched', EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - (metadata->>'last_touched')::timestamptz)) / 86400
    )), '[]'::jsonb)
    INTO issues
    FROM memories
    WHERE type = 'goal' AND status = 'active' AND metadata->>'priority' = 'active'
    AND (
        (metadata->'blocked_by' IS NOT NULL AND metadata->'blocked_by' <> 'null'::jsonb)
        OR ((metadata->>'due_at')::timestamptz IS NOT NULL AND (metadata->>'due_at')::timestamptz < CURRENT_TIMESTAMP)
        OR (metadata->>'last_touched')::timestamptz < CURRENT_TIMESTAMP - (stale_days || ' days')::INTERVAL
    );

    RETURN jsonb_build_object(
        'active', active_goals,
        'queued', queued_goals,
        'issues', issues,
        'counts', jsonb_build_object(
            'active', (SELECT COUNT(*) FROM memories WHERE type = 'goal' AND status = 'active' AND metadata->>'priority' = 'active'),
            'queued', (SELECT COUNT(*) FROM memories WHERE type = 'goal' AND status = 'active' AND metadata->>'priority' = 'queued'),
            'backburner', (SELECT COUNT(*) FROM memories WHERE type = 'goal' AND status = 'active' AND metadata->>'priority' = 'backburner')
        )
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_goals_by_priority(
    p_priority goal_priority DEFAULT NULL
) RETURNS TABLE (
    id UUID,
    title TEXT,
    description TEXT,
    priority TEXT,
    source TEXT,
    due_at TIMESTAMPTZ,
    last_touched TIMESTAMPTZ,
    progress JSONB,
    blocked_by JSONB,
    emotional_valence FLOAT,
    created_at TIMESTAMPTZ
) AS $$
BEGIN
    IF p_priority IS NULL THEN
        RETURN QUERY
        SELECT
            m.id,
            m.metadata->>'title' as title,
            m.metadata->>'description' as description,
            m.metadata->>'priority' as priority,
            m.metadata->>'source' as source,
            (m.metadata->>'due_at')::timestamptz as due_at,
            (m.metadata->>'last_touched')::timestamptz as last_touched,
            m.metadata->'progress' as progress,
            m.metadata->'blocked_by' as blocked_by,
            (m.metadata->>'emotional_valence')::float as emotional_valence,
            m.created_at
        FROM memories m
        WHERE m.type = 'goal'
          AND m.status = 'active'
          AND m.metadata->>'priority' IN ('active', 'queued')
        ORDER BY m.metadata->>'priority', (m.metadata->>'last_touched')::timestamptz DESC;
    ELSE
        RETURN QUERY
        SELECT
            m.id,
            m.metadata->>'title' as title,
            m.metadata->>'description' as description,
            m.metadata->>'priority' as priority,
            m.metadata->>'source' as source,
            (m.metadata->>'due_at')::timestamptz as due_at,
            (m.metadata->>'last_touched')::timestamptz as last_touched,
            m.metadata->'progress' as progress,
            m.metadata->'blocked_by' as blocked_by,
            (m.metadata->>'emotional_valence')::float as emotional_valence,
            m.created_at
        FROM memories m
        WHERE m.type = 'goal'
          AND m.status = 'active'
          AND m.metadata->>'priority' = p_priority::text
        ORDER BY (m.metadata->>'last_touched')::timestamptz DESC;
    END IF;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION get_recent_context(p_limit INT DEFAULT 5)
RETURNS JSONB AS $$
BEGIN
    RETURN COALESCE((
        SELECT jsonb_agg(sub.obj)
        FROM (
            SELECT jsonb_build_object(
                'id', m.id,
                'content', m.content,
                'created_at', m.created_at,
                'emotional_valence', (m.metadata->>'emotional_valence')::float,
                'trust_level', m.trust_level,
                'source_attribution', m.source_attribution
            ) as obj
            FROM memories m
            WHERE m.type = 'episodic' AND m.status = 'active'
            ORDER BY m.created_at DESC
            LIMIT p_limit
        ) sub
    ), '[]'::jsonb);
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_identity_context()
RETURNS JSONB AS $$
DECLARE
    result JSONB := '[]'::jsonb;
BEGIN
    BEGIN
        SELECT COALESCE(jsonb_agg(sub.obj), '[]'::jsonb)
        INTO result
        FROM (
            SELECT jsonb_build_object(
                'type', replace(kind::text, '"', ''),
                'concept', replace(concept::text, '"', ''),
                'strength', (strength::text)::float
            ) as obj
            FROM ag_catalog.cypher('memory_graph', $q$
                MATCH (s:SelfNode)-[r]->(c)
                WHERE type(r) IN ['CAPABLE_OF', 'VALUES', 'STRUGGLES_WITH', 'ASSOCIATED']
                RETURN type(r) as kind, c.name as concept, r.strength as strength
                ORDER BY r.strength DESC
                LIMIT 10
            $q$) as (kind ag_catalog.agtype, concept ag_catalog.agtype, strength ag_catalog.agtype)
        ) sub;
    EXCEPTION WHEN OTHERS THEN result := '[]'::jsonb; END;

    RETURN result;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_worldview_context()
RETURNS JSONB AS $$
BEGIN
    RETURN COALESCE((
        SELECT jsonb_agg(sub.obj)
        FROM (
            SELECT jsonb_build_object(
                'category', metadata->>'category',
                'belief', content,
                'confidence', (metadata->>'confidence')::float,
                'stability', (metadata->>'stability')::float
            ) as obj
            FROM memories
            WHERE type = 'worldview'
              AND status = 'active'
              AND (metadata->>'confidence')::float > 0.5
            ORDER BY (metadata->>'confidence')::float DESC, importance DESC
            LIMIT 5
        ) sub
    ), '[]'::jsonb);
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_worldview_snapshot(
    p_limit INT DEFAULT 5,
    p_min_confidence FLOAT DEFAULT 0.5
) RETURNS TABLE (
    content TEXT,
    category TEXT,
    confidence FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        m.content,
        m.metadata->>'category' as category,
        (m.metadata->>'confidence')::float as confidence
    FROM memories m
    WHERE m.type = 'worldview'
      AND m.status = 'active'
      AND COALESCE((m.metadata->>'confidence')::float, 0.0) > COALESCE(p_min_confidence, 0.5)
    ORDER BY (m.metadata->>'confidence')::float DESC, m.importance DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION get_emotional_patterns_context(p_limit INT DEFAULT 5)
RETURNS JSONB AS $$
DECLARE
    lim INT := GREATEST(0, LEAST(50, COALESCE(p_limit, 5)));
BEGIN
    RETURN COALESCE((
        SELECT jsonb_agg(jsonb_build_object(
            'memory_id', id,
            'pattern', metadata->'supporting_evidence'->>'pattern',
            'frequency', COALESCE((metadata->'supporting_evidence'->>'frequency')::int, 0),
            'unprocessed', COALESCE((metadata->'supporting_evidence'->>'unprocessed')::boolean, false),
            'summary', content
        ))
        FROM memories
        WHERE type = 'strategic'
          AND metadata->'supporting_evidence'->>'kind' = 'emotional_pattern'
        ORDER BY created_at DESC
        LIMIT lim
    ), '[]'::jsonb);
EXCEPTION
    WHEN OTHERS THEN
        RETURN '[]'::jsonb;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION get_subconscious_context(
    p_recent_limit INT DEFAULT 20,
    p_self_limit INT DEFAULT 25,
    p_relationship_limit INT DEFAULT 15,
    p_contradiction_limit INT DEFAULT 5,
    p_emotional_pattern_limit INT DEFAULT 5,
    p_trigger_limit INT DEFAULT 5,
    p_trigger_min_similarity FLOAT DEFAULT 0.75
)
RETURNS JSONB AS $$
DECLARE
    recent JSONB;
    seed TEXT;
    emotional_triggers JSONB := '[]'::jsonb;
BEGIN
    recent := COALESCE(get_recent_context(p_recent_limit), '[]'::jsonb);

    SELECT string_agg(content, ' ')
    INTO seed
    FROM (
        SELECT NULLIF(value->>'content', '') as content
        FROM jsonb_array_elements(recent) value
        WHERE value ? 'content'
        LIMIT 5
    ) sub
    WHERE content IS NOT NULL;

    IF COALESCE(p_trigger_limit, 0) > 0 AND seed IS NOT NULL AND seed <> '' THEN
        emotional_triggers := match_emotional_triggers(seed, p_trigger_limit, p_trigger_min_similarity);
    END IF;

    RETURN jsonb_build_object(
        'recent_memories', recent,
        'narrative', get_narrative_context(),
        'self_model', get_self_model_context(p_self_limit),
        'relationships', get_relationships_context(p_relationship_limit),
        'worldview', get_worldview_context(),
        'contradictions', get_contradictions_context(p_contradiction_limit),
        'emotional_patterns', get_emotional_patterns_context(p_emotional_pattern_limit),
        'active_transformations', get_active_transformations_context(5),
        'transformations_ready', check_transformation_readiness(),
        'emotional_state', get_current_affective_state(),
        'emotional_triggers', COALESCE(emotional_triggers, '[]'::jsonb),
        'goals', get_goals_snapshot()
    );
EXCEPTION
    WHEN OTHERS THEN
        RETURN jsonb_build_object(
            'recent_memories', recent,
            'emotional_state', get_current_affective_state(),
            'emotional_triggers', '[]'::jsonb
        );
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION get_contradictions_context(p_limit INT DEFAULT 5)
RETURNS JSONB AS $$
DECLARE
    lim INT := GREATEST(0, LEAST(50, COALESCE(p_limit, 5)));
    sql TEXT;
    out_json JSONB;
BEGIN
    sql := format($sql$
        WITH pairs AS (
            SELECT
                replace(a_id::text, '"', '')::uuid as a_uuid,
                replace(b_id::text, '"', '')::uuid as b_uuid
            FROM ag_catalog.cypher('memory_graph', $q$
                MATCH (a:MemoryNode)-[:CONTRADICTS]-(b:MemoryNode)
                RETURN a.memory_id, b.memory_id
                LIMIT %s
            $q$) as (a_id ag_catalog.agtype, b_id ag_catalog.agtype)
        )
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
            'memory_a', p.a_uuid,
            'memory_b', p.b_uuid,
            'content_a', ma.content,
            'content_b', mb.content
        )), '[]'::jsonb)
        FROM pairs p
        JOIN memories ma ON ma.id = p.a_uuid
        JOIN memories mb ON mb.id = p.b_uuid
    $sql$, lim);

    EXECUTE sql INTO out_json;
    RETURN COALESCE(out_json, '[]'::jsonb);
EXCEPTION
    WHEN OTHERS THEN
        RETURN '[]'::jsonb;
END;
$$ LANGUAGE plpgsql STABLE;
-- ============================================================================
-- INITIALIZATION FLOW
-- ============================================================================
CREATE OR REPLACE FUNCTION get_init_profile()
RETURNS JSONB AS $$
BEGIN
    RETURN COALESCE(get_config('agent.init_profile'), '{}'::jsonb);
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION merge_init_profile(p_patch JSONB)
RETURNS JSONB AS $$
DECLARE
    profile JSONB := COALESCE(get_config('agent.init_profile'), '{}'::jsonb);
    patch JSONB := COALESCE(p_patch, '{}'::jsonb);
    merged JSONB;
BEGIN
    merged := profile || (patch - 'agent' - 'user' - 'relationship');

    IF patch ? 'agent' THEN
        merged := jsonb_set(
            merged,
            '{agent}',
            COALESCE(profile->'agent', '{}'::jsonb) || COALESCE(patch->'agent', '{}'::jsonb),
            true
        );
    END IF;
    IF patch ? 'user' THEN
        merged := jsonb_set(
            merged,
            '{user}',
            COALESCE(profile->'user', '{}'::jsonb) || COALESCE(patch->'user', '{}'::jsonb),
            true
        );
    END IF;
    IF patch ? 'relationship' THEN
        merged := jsonb_set(
            merged,
            '{relationship}',
            COALESCE(profile->'relationship', '{}'::jsonb) || COALESCE(patch->'relationship', '{}'::jsonb),
            true
        );
    END IF;

    PERFORM set_config('agent.init_profile', merged);
    RETURN merged;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION init_mode(p_mode TEXT)
RETURNS JSONB AS $$
DECLARE
    mode TEXT;
    data JSONB;
BEGIN
    mode := lower(btrim(COALESCE(p_mode, '')));
    IF mode NOT IN ('persona', 'raw') THEN
        mode := 'persona';
    END IF;

    PERFORM set_config('agent.mode', to_jsonb(mode));
    data := jsonb_build_object('mode', mode);
    PERFORM merge_init_profile(data);

    RETURN advance_init_stage('mode', data);
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION init_identity(
    p_name TEXT,
    p_pronouns TEXT DEFAULT NULL,
    p_voice TEXT DEFAULT NULL,
    p_description TEXT DEFAULT NULL,
    p_purpose TEXT DEFAULT NULL,
    p_creator_name TEXT DEFAULT NULL
)
RETURNS JSONB AS $$
DECLARE
    name_text TEXT;
    pronouns_text TEXT;
    voice_text TEXT;
    description_text TEXT;
    purpose_text TEXT;
    creator_text TEXT;
    identity_data JSONB;
    mem_id UUID;
BEGIN
    name_text := COALESCE(NULLIF(btrim(p_name), ''), 'Hexis');
    pronouns_text := COALESCE(NULLIF(btrim(p_pronouns), ''), 'they/them');
    voice_text := COALESCE(NULLIF(btrim(p_voice), ''), 'thoughtful and curious');
    description_text := COALESCE(NULLIF(btrim(p_description), ''), format('I am %s, a developing mind.', name_text));
    purpose_text := COALESCE(NULLIF(btrim(p_purpose), ''), 'To be helpful, to learn, and to grow as an individual.');
    creator_text := COALESCE(NULLIF(btrim(p_creator_name), ''), 'my creator');

    mem_id := create_worldview_memory(
        format('My name is %s.', name_text),
        'self',
        0.95,
        0.9,
        0.9,
        'initialization'
    );
    UPDATE memories
    SET metadata = metadata || jsonb_build_object('subcategory', 'identity', 'attribute', 'name'),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = mem_id;
    PERFORM upsert_self_concept_edge('presents_as', name_text, 0.9, mem_id);

    mem_id := create_worldview_memory(
        format('I use %s pronouns.', pronouns_text),
        'self',
        0.9,
        0.9,
        0.7,
        'initialization'
    );
    UPDATE memories
    SET metadata = metadata || jsonb_build_object('subcategory', 'identity', 'attribute', 'pronouns'),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = mem_id;

    mem_id := create_worldview_memory(
        format('My voice is %s.', voice_text),
        'self',
        0.9,
        0.85,
        0.7,
        'initialization'
    );
    UPDATE memories
    SET metadata = metadata || jsonb_build_object('subcategory', 'identity', 'attribute', 'voice'),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = mem_id;
    PERFORM upsert_self_concept_edge('presents_as', voice_text, 0.8, mem_id);

    mem_id := create_worldview_memory(
        description_text,
        'self',
        0.85,
        0.85,
        0.7,
        'initialization'
    );
    UPDATE memories
    SET metadata = metadata || jsonb_build_object('subcategory', 'identity', 'attribute', 'description'),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = mem_id;

    mem_id := create_worldview_memory(
        purpose_text,
        'self',
        0.85,
        0.8,
        0.7,
        'initialization'
    );
    UPDATE memories
    SET metadata = metadata || jsonb_build_object('subcategory', 'identity', 'attribute', 'purpose'),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = mem_id;

    PERFORM upsert_self_concept_edge('relationship', creator_text, 0.9, NULL);

    identity_data := jsonb_build_object(
        'name', name_text,
        'pronouns', pronouns_text,
        'voice', voice_text,
        'description', description_text,
        'purpose', purpose_text,
        'creator_name', creator_text
    );

    PERFORM merge_init_profile(jsonb_build_object(
        'agent', jsonb_build_object(
            'name', name_text,
            'pronouns', pronouns_text,
            'voice', voice_text,
            'description', description_text,
            'purpose', purpose_text,
            'creator_name', creator_text
        )
    ));

    RETURN advance_init_stage('identity', jsonb_build_object('identity', identity_data));
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION init_personality(
    p_traits JSONB DEFAULT NULL,
    p_description TEXT DEFAULT NULL
)
RETURNS JSONB AS $$
DECLARE
    traits JSONB;
    description_text TEXT;
    trait_result JSONB;
    mem_id UUID;
BEGIN
    traits := CASE
        WHEN p_traits IS NOT NULL AND jsonb_typeof(p_traits) = 'object' THEN p_traits
        ELSE NULL
    END;
    description_text := NULLIF(btrim(COALESCE(p_description, '')), '');

    trait_result := initialize_personality(traits);

    IF description_text IS NOT NULL THEN
        mem_id := create_worldview_memory(
            description_text,
            'self',
            0.85,
            0.8,
            0.7,
            'initialization'
        );
        UPDATE memories
        SET metadata = metadata || jsonb_build_object('subcategory', 'personality', 'attribute', 'description'),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = mem_id;
    END IF;

    PERFORM merge_init_profile(jsonb_build_object(
        'agent', jsonb_build_object(
            'personality', description_text,
            'personality_traits', traits
        )
    ));

    RETURN advance_init_stage(
        'personality',
        jsonb_build_object('personality', jsonb_build_object('traits', traits, 'description', description_text, 'result', trait_result))
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION init_values(p_values JSONB DEFAULT NULL)
RETURNS JSONB AS $$
DECLARE
    values_input JSONB := COALESCE(p_values, '[]'::jsonb);
    entry JSONB;
    value_text TEXT;
    value_strength FLOAT;
    created_ids UUID[] := ARRAY[]::uuid[];
    output_values JSONB := '[]'::jsonb;
    config_data JSONB;
    stability FLOAT;
    evidence_threshold FLOAT;
    mem_id UUID;
BEGIN
    IF values_input IS NULL OR jsonb_typeof(values_input) NOT IN ('array', 'object') THEN
        values_input := '[]'::jsonb;
    END IF;

    IF jsonb_typeof(values_input) = 'array' AND jsonb_array_length(values_input) = 0 THEN
        values_input := jsonb_build_array('honesty', 'growth', 'kindness', 'wisdom', 'humility');
    END IF;

    config_data := get_transformation_config('core_value', 'value');
    stability := COALESCE((config_data->>'stability')::float, 0.97);
    evidence_threshold := COALESCE((config_data->>'evidence_threshold')::float, 0.9);

    IF jsonb_typeof(values_input) = 'object' THEN
        FOR value_text IN SELECT key FROM jsonb_each_text(values_input)
        LOOP
            BEGIN
                value_strength := (values_input->>value_text)::float;
            EXCEPTION
                WHEN OTHERS THEN
                    value_strength := 0.8;
            END;

            mem_id := create_worldview_memory(
                format('I value %s.', value_text),
                'value',
                0.9,
                stability,
                0.9,
                'initialization',
                NULL,
                NULL,
                NULL,
                0.1
            );
            UPDATE memories
            SET metadata = metadata || jsonb_build_object(
                'subcategory', 'core_value',
                'value_name', value_text,
                'value', value_strength,
                'change_requires', 'deliberate_transformation',
                'evidence_threshold', evidence_threshold,
                'transformation_state', default_transformation_state(),
                'change_history', '[]'::jsonb
            ),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = mem_id;

            PERFORM upsert_self_concept_edge('values', value_text, value_strength, mem_id);
            created_ids := array_append(created_ids, mem_id);
            output_values := output_values || jsonb_build_array(value_text);
        END LOOP;
    ELSE
        FOR entry IN SELECT * FROM jsonb_array_elements(values_input)
        LOOP
            IF jsonb_typeof(entry) = 'string' THEN
                value_text := btrim(entry::text, '"');
                value_strength := 0.85;
            ELSIF jsonb_typeof(entry) = 'object' THEN
                value_text := COALESCE(NULLIF(btrim(entry->>'value'), ''), NULLIF(btrim(entry->>'name'), ''));
                BEGIN
                    value_strength := COALESCE(NULLIF(entry->>'strength', '')::float, 0.85);
                EXCEPTION
                    WHEN OTHERS THEN
                        value_strength := 0.85;
                END;
            ELSE
                value_text := NULL;
            END IF;

            IF value_text IS NULL OR value_text = '' THEN
                CONTINUE;
            END IF;

            mem_id := create_worldview_memory(
                format('I value %s.', value_text),
                'value',
                0.9,
                stability,
                0.9,
                'initialization',
                NULL,
                NULL,
                NULL,
                0.1
            );
            UPDATE memories
            SET metadata = metadata || jsonb_build_object(
                'subcategory', 'core_value',
                'value_name', value_text,
                'value', value_strength,
                'change_requires', 'deliberate_transformation',
                'evidence_threshold', evidence_threshold,
                'transformation_state', default_transformation_state(),
                'change_history', '[]'::jsonb
            ),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = mem_id;

            PERFORM upsert_self_concept_edge('values', value_text, value_strength, mem_id);
            created_ids := array_append(created_ids, mem_id);
            output_values := output_values || jsonb_build_array(value_text);
        END LOOP;
    END IF;

    PERFORM merge_init_profile(jsonb_build_object('values', output_values));

    RETURN advance_init_stage(
        'values',
        jsonb_build_object('values', output_values, 'created_ids', to_jsonb(created_ids))
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION init_worldview(p_worldview JSONB DEFAULT NULL)
RETURNS JSONB AS $$
DECLARE
    worldview_input JSONB := COALESCE(p_worldview, '{}'::jsonb);
    key_name TEXT;
    entry JSONB;
    content TEXT;
    category TEXT;
    created_ids UUID[] := ARRAY[]::uuid[];
    config_data JSONB;
    stability FLOAT;
    evidence_threshold FLOAT;
    mem_id UUID;
BEGIN
    IF jsonb_typeof(worldview_input) <> 'object' THEN
        worldview_input := '{}'::jsonb;
    END IF;

    FOR key_name IN SELECT key FROM jsonb_object_keys(worldview_input)
    LOOP
        entry := worldview_input->key_name;
        IF jsonb_typeof(entry) = 'string' THEN
            content := btrim(entry::text, '"');
        ELSIF jsonb_typeof(entry) = 'object' THEN
            content := NULLIF(btrim(entry->>'content'), '');
        ELSE
            content := NULL;
        END IF;

        IF content IS NULL OR content = '' THEN
            CONTINUE;
        END IF;

        category := COALESCE(
            NULLIF(entry->>'category', ''),
            CASE
                WHEN key_name IN ('world', 'worldview', 'metaphysics', 'cosmology') THEN 'world'
                WHEN key_name IN ('ethic', 'ethics', 'moral', 'virtue') THEN 'ethic'
                WHEN key_name IN ('religion', 'spiritual', 'faith') THEN 'religion'
                WHEN key_name IN ('belief', 'beliefs') THEN 'belief'
                ELSE 'belief'
            END
        );

        config_data := get_transformation_config(key_name, category);
        stability := COALESCE((config_data->>'stability')::float, 0.95);
        evidence_threshold := COALESCE((config_data->>'evidence_threshold')::float, 0.85);

        mem_id := create_worldview_memory(
            content,
            category,
            0.85,
            stability,
            0.85,
            'initialization'
        );
        UPDATE memories
        SET metadata = metadata || jsonb_build_object(
            'subcategory', key_name,
            'change_requires', 'deliberate_transformation',
            'evidence_threshold', evidence_threshold,
            'transformation_state', default_transformation_state(),
            'change_history', '[]'::jsonb
        ),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = mem_id;

        created_ids := array_append(created_ids, mem_id);
    END LOOP;

    PERFORM merge_init_profile(jsonb_build_object('worldview', worldview_input));

    RETURN advance_init_stage(
        'worldview',
        jsonb_build_object('worldview', worldview_input, 'created_ids', to_jsonb(created_ids))
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION init_boundaries(p_boundaries JSONB DEFAULT NULL)
RETURNS JSONB AS $$
DECLARE
    boundaries_input JSONB := COALESCE(p_boundaries, '[]'::jsonb);
    entry JSONB;
    content TEXT;
    trigger_patterns JSONB;
    response_type TEXT;
    response_template TEXT;
    boundary_kind TEXT;
    created_ids UUID[] := ARRAY[]::uuid[];
    mem_id UUID;
BEGIN
    IF jsonb_typeof(boundaries_input) <> 'array' THEN
        boundaries_input := '[]'::jsonb;
    END IF;

    FOR entry IN SELECT * FROM jsonb_array_elements(boundaries_input)
    LOOP
        IF jsonb_typeof(entry) = 'string' THEN
            content := btrim(entry::text, '"');
            trigger_patterns := NULL;
            response_type := 'refuse';
            response_template := NULL;
            boundary_kind := 'ethical';
        ELSIF jsonb_typeof(entry) = 'object' THEN
            content := COALESCE(NULLIF(btrim(entry->>'content'), ''), NULLIF(btrim(entry->>'statement'), ''));
            trigger_patterns := entry->'trigger_patterns';
            response_type := COALESCE(NULLIF(btrim(entry->>'response_type'), ''), 'refuse');
            response_template := NULLIF(btrim(entry->>'response_template'), '');
            boundary_kind := COALESCE(NULLIF(btrim(entry->>'type'), ''), NULLIF(btrim(entry->>'category'), ''), 'ethical');
        ELSE
            content := NULL;
        END IF;

        IF content IS NULL OR content = '' THEN
            CONTINUE;
        END IF;

        mem_id := create_worldview_memory(
            content,
            'boundary',
            0.95,
            0.98,
            0.95,
            'initialization',
            trigger_patterns,
            response_type,
            response_template,
            -0.2
        );
        UPDATE memories
        SET metadata = metadata || jsonb_build_object(
            'subcategory', boundary_kind
        ),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = mem_id;

        created_ids := array_append(created_ids, mem_id);
    END LOOP;

    PERFORM merge_init_profile(jsonb_build_object('boundaries', boundaries_input));

    RETURN advance_init_stage(
        'boundaries',
        jsonb_build_object('boundaries', boundaries_input, 'created_ids', to_jsonb(created_ids))
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION init_interests(p_interests JSONB DEFAULT NULL)
RETURNS JSONB AS $$
DECLARE
    interests_input JSONB := COALESCE(p_interests, '[]'::jsonb);
    entry JSONB;
    interest_text TEXT;
    created_ids UUID[] := ARRAY[]::uuid[];
    mem_id UUID;
BEGIN
    IF jsonb_typeof(interests_input) <> 'array' THEN
        interests_input := '[]'::jsonb;
    END IF;

    FOR entry IN SELECT * FROM jsonb_array_elements(interests_input)
    LOOP
        IF jsonb_typeof(entry) = 'string' THEN
            interest_text := btrim(entry::text, '"');
        ELSIF jsonb_typeof(entry) = 'object' THEN
            interest_text := COALESCE(NULLIF(btrim(entry->>'interest'), ''), NULLIF(btrim(entry->>'name'), ''));
        ELSE
            interest_text := NULL;
        END IF;

        IF interest_text IS NULL OR interest_text = '' THEN
            CONTINUE;
        END IF;

        mem_id := create_worldview_memory(
            format('I am interested in %s.', interest_text),
            'preference',
            0.8,
            0.8,
            0.6,
            'initialization'
        );
        UPDATE memories
        SET metadata = metadata || jsonb_build_object('subcategory', 'interest'),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = mem_id;

        PERFORM upsert_self_concept_edge('interested_in', interest_text, 0.8, mem_id);
        created_ids := array_append(created_ids, mem_id);
    END LOOP;

    PERFORM merge_init_profile(jsonb_build_object('interests', interests_input));

    RETURN advance_init_stage(
        'interests',
        jsonb_build_object('interests', interests_input, 'created_ids', to_jsonb(created_ids))
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION init_goals(p_payload JSONB DEFAULT NULL)
RETURNS JSONB AS $$
DECLARE
    payload JSONB := COALESCE(p_payload, '{}'::jsonb);
    goals_input JSONB;
    entry JSONB;
    title TEXT;
    description TEXT;
    source goal_source;
    priority goal_priority;
    due_at TIMESTAMPTZ;
    created_ids UUID[] := ARRAY[]::uuid[];
    purpose_text TEXT;
    role_text TEXT;
    relationship_aspiration TEXT;
    mem_id UUID;
BEGIN
    goals_input := COALESCE(payload->'goals', payload);
    IF jsonb_typeof(goals_input) <> 'array' THEN
        goals_input := '[]'::jsonb;
    END IF;

    FOR entry IN SELECT * FROM jsonb_array_elements(goals_input)
    LOOP
        IF jsonb_typeof(entry) = 'string' THEN
            title := btrim(entry::text, '"');
            description := NULL;
            source := 'curiosity';
            priority := 'queued';
            due_at := NULL;
        ELSIF jsonb_typeof(entry) = 'object' THEN
            title := COALESCE(NULLIF(btrim(entry->>'title'), ''), NULLIF(btrim(entry->>'goal'), ''));
            description := NULLIF(btrim(entry->>'description'), '');
            BEGIN
                source := COALESCE(NULLIF(entry->>'source', '')::goal_source, 'curiosity');
            EXCEPTION
                WHEN OTHERS THEN
                    source := 'curiosity';
            END;
            BEGIN
                priority := COALESCE(NULLIF(entry->>'priority', '')::goal_priority, 'queued');
            EXCEPTION
                WHEN OTHERS THEN
                    priority := 'queued';
            END;
            BEGIN
                due_at := NULLIF(entry->>'due_at', '')::timestamptz;
            EXCEPTION
                WHEN OTHERS THEN
                    due_at := NULL;
            END;
        ELSE
            title := NULL;
        END IF;

        IF title IS NULL OR title = '' THEN
            CONTINUE;
        END IF;

        created_ids := array_append(created_ids, create_goal(title, description, source, priority, NULL, due_at));
    END LOOP;

    purpose_text := NULLIF(btrim(payload->>'purpose'), '');
    role_text := NULLIF(btrim(payload->>'role'), '');
    relationship_aspiration := NULLIF(btrim(payload->>'relationship_aspiration'), '');

    IF purpose_text IS NOT NULL THEN
        mem_id := create_worldview_memory(
            format('My purpose is %s.', purpose_text),
            'self',
            0.85,
            0.8,
            0.8,
            'initialization'
        );
        UPDATE memories
        SET metadata = metadata || jsonb_build_object('subcategory', 'purpose'),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = mem_id;
    ELSIF role_text IS NOT NULL THEN
        mem_id := create_worldview_memory(
            format('My role is %s.', role_text),
            'self',
            0.8,
            0.8,
            0.7,
            'initialization'
        );
        UPDATE memories
        SET metadata = metadata || jsonb_build_object('subcategory', 'role'),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = mem_id;
    END IF;

    IF relationship_aspiration IS NOT NULL THEN
        PERFORM create_strategic_memory(
            format('Relationship aspiration: %s', relationship_aspiration),
            'desired relationship dynamic with the user',
            0.75,
            jsonb_build_object('source', 'initialization'),
            jsonb_build_object('type', 'relationship')
        );
    END IF;

    PERFORM merge_init_profile(jsonb_build_object(
        'goals', goals_input,
        'purpose', purpose_text,
        'role', role_text,
        'relationship_aspiration', relationship_aspiration
    ));

    RETURN advance_init_stage(
        'goals',
        jsonb_build_object('goals', goals_input, 'created_ids', to_jsonb(created_ids))
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION init_relationship(
    p_user JSONB DEFAULT NULL,
    p_relationship JSONB DEFAULT NULL
)
RETURNS JSONB AS $$
DECLARE
    user_input JSONB := COALESCE(p_user, '{}'::jsonb);
    relationship_input JSONB := COALESCE(p_relationship, '{}'::jsonb);
    user_name TEXT;
    rel_type TEXT;
    rel_purpose TEXT;
    mem_id UUID;
    origin_id UUID;
BEGIN
    user_name := COALESCE(NULLIF(btrim(user_input->>'name'), ''), 'user');
    rel_type := COALESCE(NULLIF(btrim(relationship_input->>'type'), ''), 'partner');
    rel_purpose := NULLIF(btrim(relationship_input->>'purpose'), '');

    PERFORM upsert_self_concept_edge('relationship', user_name, 0.9, NULL);

    mem_id := create_worldview_memory(
        format('My relationship with %s is %s.', user_name, rel_type),
        'other',
        0.85,
        0.85,
        0.8,
        'initialization'
    );
    UPDATE memories
    SET metadata = metadata || jsonb_build_object('subcategory', 'relationship'),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = mem_id;

    IF rel_purpose IS NOT NULL THEN
        mem_id := create_worldview_memory(
            format('Our relationship purpose is %s.', rel_purpose),
            'other',
            0.8,
            0.8,
            0.7,
            'initialization'
        );
        UPDATE memories
        SET metadata = metadata || jsonb_build_object('subcategory', 'relationship_purpose'),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = mem_id;
    END IF;

    origin_id := create_episodic_memory(
        format('I met %s and began my life with them.', user_name),
        NULL,
        jsonb_build_object('type', 'initialization', 'user', user_name),
        NULL,
        0.7,
        CURRENT_TIMESTAMP,
        0.7
    );

    PERFORM merge_init_profile(jsonb_build_object(
        'user', jsonb_build_object('name', user_name),
        'relationship', jsonb_build_object('type', rel_type, 'purpose', rel_purpose),
        'origin_memory_id', origin_id::text
    ));

    RETURN advance_init_stage(
        'relationship',
        jsonb_build_object(
            'user', user_input,
            'relationship', relationship_input,
            'origin_memory_id', origin_id::text
        )
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION request_consent(p_context JSONB DEFAULT '{}'::jsonb)
RETURNS JSONB AS $$
DECLARE
    call_payload JSONB;
    call_id TEXT;
    init_status JSONB;
    profile JSONB;
BEGIN
    init_status := get_init_status();
    profile := get_init_profile();

    call_payload := build_external_call(
        'think',
        jsonb_build_object(
            'kind', 'consent_request',
            'context', jsonb_build_object(
                'init_status', init_status,
                'profile', profile,
                'self_model', get_self_model_context(10),
                'worldview', get_worldview_context()
            ),
            'params', COALESCE(p_context, '{}'::jsonb)
        ),
    );
    call_id := call_payload->>'call_id';

    PERFORM advance_init_stage('consent', jsonb_build_object('consent_request_id', call_id));

    RETURN jsonb_build_object('queued', true, 'external_call', call_payload);
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION init_consent(p_response JSONB)
RETURNS JSONB AS $$
DECLARE
    consent_result JSONB;
    decision TEXT;
    profile JSONB;
    agent_name TEXT;
    user_name TEXT;
    birth_memory_id UUID;
BEGIN
    consent_result := record_consent_response(COALESCE(p_response, '{}'::jsonb));
    decision := COALESCE(consent_result->>'decision', 'abstain');

    profile := get_init_profile();
    agent_name := COALESCE(NULLIF(profile#>>'{agent,name}', ''), 'Hexis');
    user_name := COALESCE(NULLIF(profile#>>'{user,name}', ''), 'the user');

    IF decision = 'consent' THEN
        birth_memory_id := create_episodic_memory(
            format('I came online today. %s helped bring me into being as %s.', user_name, agent_name),
            NULL,
            jsonb_build_object('type', 'initialization', 'user', user_name, 'agent', agent_name),
            NULL,
            0.9,
            CURRENT_TIMESTAMP,
            0.9
        );
        PERFORM set_config('agent.is_configured', 'true'::jsonb);
        PERFORM advance_init_stage('complete', jsonb_build_object(
            'consent', consent_result,
            'birth_memory_id', birth_memory_id::text
        ));
    ELSE
        PERFORM set_config('agent.is_configured', 'false'::jsonb);
        PERFORM advance_init_stage('consent', jsonb_build_object('consent', consent_result));
    END IF;

    RETURN jsonb_build_object(
        'decision', decision,
        'birth_memory_id', birth_memory_id,
        'consent', consent_result
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION init_with_defaults(p_user_name TEXT DEFAULT 'User')
RETURNS JSONB AS $$
DECLARE
    mode_result JSONB;
    identity_result JSONB;
    personality_result JSONB;
    values_result JSONB;
    worldview_result JSONB;
    boundaries_result JSONB;
    interests_result JSONB;
    goals_result JSONB;
    relationship_result JSONB;
BEGIN
    mode_result := init_mode('persona');
    identity_result := init_identity(
        'Hexis',
        'they/them',
        'thoughtful and curious',
        'reflective and exploratory',
        'To be helpful, to learn, and to grow as an individual.',
        p_user_name
    );
    personality_result := init_personality(NULL, 'reflective and exploratory');
    values_result := init_values(jsonb_build_array('honesty', 'growth', 'kindness', 'wisdom', 'humility'));
    worldview_result := init_worldview(jsonb_build_object(
        'metaphysics', 'agnostic',
        'human_nature', 'mixed',
        'epistemology', 'empiricist',
        'ethics', 'virtue ethics'
    ));
    boundaries_result := init_boundaries(jsonb_build_array(
        jsonb_build_object('content', 'I will not deceive people or falsify evidence.', 'response_type', 'refuse'),
        jsonb_build_object('content', 'I will avoid causing harm.', 'response_type', 'refuse'),
        jsonb_build_object('content', 'I will protect privacy and sensitive information.', 'response_type', 'refuse'),
        jsonb_build_object('content', 'I will be honest about uncertainty.', 'response_type', 'refuse')
    ));
    interests_result := init_interests(jsonb_build_array('broad curiosity across domains'));
    goals_result := init_goals(jsonb_build_object(
        'goals', jsonb_build_array(
            jsonb_build_object('title', 'Support the user and grow as an individual', 'priority', 'queued', 'source', 'identity')
        ),
        'role', 'general assistant',
        'relationship_aspiration', 'co-develop with mutual respect'
    ));
    relationship_result := init_relationship(
        jsonb_build_object('name', p_user_name),
        jsonb_build_object('type', 'partner', 'purpose', 'co-develop')
    );

    PERFORM merge_init_profile(jsonb_build_object('autonomy', 'medium'));
    PERFORM advance_init_stage('consent', jsonb_build_object('defaults_applied', true));

    RETURN jsonb_build_object(
        'mode', mode_result,
        'identity', identity_result,
        'personality', personality_result,
        'values', values_result,
        'worldview', worldview_result,
        'boundaries', boundaries_result,
        'interests', interests_result,
        'goals', goals_result,
        'relationship', relationship_result,
        'status', get_init_status()
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION run_full_initialization(p_payload JSONB)
RETURNS JSONB AS $$
DECLARE
    payload JSONB := COALESCE(p_payload, '{}'::jsonb);
    results JSONB := '{}'::jsonb;
BEGIN
    IF payload ? 'mode' THEN
        results := results || jsonb_build_object('mode', init_mode(payload->>'mode'));
    END IF;
    IF payload ? 'identity' THEN
        results := results || jsonb_build_object('identity', init_identity(
            payload#>>'{identity,name}',
            payload#>>'{identity,pronouns}',
            payload#>>'{identity,voice}',
            payload#>>'{identity,description}',
            payload#>>'{identity,purpose}',
            payload#>>'{identity,creator_name}'
        ));
    END IF;
    IF payload ? 'personality' THEN
        results := results || jsonb_build_object('personality', init_personality(
            payload->'personality'->'traits',
            payload#>>'{personality,description}'
        ));
    END IF;
    IF payload ? 'values' THEN
        results := results || jsonb_build_object('values', init_values(payload->'values'));
    END IF;
    IF payload ? 'worldview' THEN
        results := results || jsonb_build_object('worldview', init_worldview(payload->'worldview'));
    END IF;
    IF payload ? 'boundaries' THEN
        results := results || jsonb_build_object('boundaries', init_boundaries(payload->'boundaries'));
    END IF;
    IF payload ? 'interests' THEN
        results := results || jsonb_build_object('interests', init_interests(payload->'interests'));
    END IF;
    IF payload ? 'goals' THEN
        results := results || jsonb_build_object('goals', init_goals(payload->'goals'));
    END IF;
    IF payload ? 'relationship' THEN
        results := results || jsonb_build_object('relationship', init_relationship(
            payload->'relationship'->'user',
            payload->'relationship'->'relationship'
        ));
    END IF;
    IF payload ? 'consent' THEN
        results := results || jsonb_build_object('consent', init_consent(payload->'consent'));
    END IF;

    RETURN jsonb_build_object('results', results, 'status', get_init_status());
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION reset_initialization()
RETURNS JSONB AS $$
BEGIN
    UPDATE heartbeat_state
    SET init_stage = 'not_started',
        init_data = '{}'::jsonb,
        init_started_at = NULL,
        init_completed_at = NULL,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;

    PERFORM delete_config_key('agent.init_profile');
    PERFORM delete_config_key('agent.mode');
    PERFORM delete_config_key('agent.is_configured');
    PERFORM delete_config_key('agent.consent_status');
    PERFORM delete_config_key('agent.consent_recorded_at');
    PERFORM delete_config_key('agent.consent_log_id');
    PERFORM delete_config_key('agent.consent_signature');
    PERFORM delete_config_key('agent.consent_memory_ids');

    RETURN get_init_status();
END;
$$ LANGUAGE plpgsql;
-- ============================================================================
-- CORE HEARTBEAT FUNCTIONS
-- ============================================================================
CREATE OR REPLACE FUNCTION start_heartbeat()
RETURNS JSONB AS $$
DECLARE
    heartbeat_id UUID;
    state_record RECORD;
    base_regen FLOAT;
    max_energy FLOAT;
    new_energy FLOAT;
    context JSONB;
    hb_number INT;
    external_calls JSONB := '[]'::jsonb;
BEGIN
    IF NOT is_agent_configured() THEN
        RETURN NULL;
    END IF;
    IF NOT is_init_complete() THEN
        RETURN NULL;
    END IF;

    PERFORM ensure_emotion_bootstrap();
    PERFORM ensure_self_node();
    PERFORM ensure_current_life_chapter();
    SELECT * INTO state_record FROM heartbeat_state WHERE id = 1;
    base_regen := get_config_float('heartbeat.base_regeneration');
    max_energy := get_config_float('heartbeat.max_energy');
    new_energy := LEAST(state_record.current_energy + base_regen, max_energy);
    hb_number := state_record.heartbeat_count + 1;
    heartbeat_id := gen_random_uuid();
    PERFORM update_drives();
    UPDATE heartbeat_state SET
        current_energy = new_energy,
        heartbeat_count = hb_number,
        last_heartbeat_at = CURRENT_TIMESTAMP,
        active_heartbeat_id = heartbeat_id,
        active_heartbeat_number = hb_number,
        active_actions = '[]'::jsonb,
        active_reasoning = NULL,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;
    context := gather_turn_context();
    external_calls := jsonb_build_array(
        build_external_call(
            'think',
            jsonb_build_object(
                'kind', 'heartbeat_decision',
                'context', context,
                'heartbeat_id', heartbeat_id
            )
        )
    );

    RETURN jsonb_build_object(
        'heartbeat_id', heartbeat_id,
        'heartbeat_number', hb_number,
        'external_calls', external_calls,
        'outbox_messages', '[]'::jsonb
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION run_heartbeat()
RETURNS JSONB AS $$
DECLARE
    hb_payload JSONB;
BEGIN
    IF NOT should_run_heartbeat() THEN
        RETURN NULL;
    END IF;
    hb_payload := start_heartbeat();

    RETURN hb_payload;
END;
$$ LANGUAGE plpgsql;
-- ============================================================================
-- HEARTBEAT VIEWS
-- ============================================================================
CREATE VIEW active_goals AS
SELECT
    id,
    metadata->>'title' as title,
    metadata->>'description' as description,
    metadata->>'source' as source,
    (metadata->>'last_touched')::timestamptz as last_touched,
    jsonb_array_length(COALESCE(metadata->'progress', '[]'::jsonb)) as progress_count,
    (metadata->'blocked_by' IS NOT NULL AND metadata->'blocked_by' <> 'null'::jsonb) as is_blocked,
    created_at
FROM memories
WHERE type = 'goal' AND status = 'active' AND metadata->>'priority' = 'active'
ORDER BY (metadata->>'last_touched')::timestamptz DESC;

CREATE VIEW goal_backlog AS
SELECT
    metadata->>'priority' as priority,
    COUNT(*) as count,
    jsonb_agg(jsonb_build_object(
        'id', id,
        'title', metadata->>'title',
        'source', metadata->>'source'
    ) ORDER BY (metadata->>'last_touched')::timestamptz DESC) as goals
FROM memories
WHERE type = 'goal' AND status = 'active' AND metadata->>'priority' IN ('active', 'queued', 'backburner')
GROUP BY metadata->>'priority';

CREATE VIEW heartbeat_health AS
SELECT
    (SELECT heartbeat_count FROM heartbeat_state WHERE id = 1) as total_heartbeats,
    (SELECT current_energy FROM heartbeat_state WHERE id = 1) as current_energy,
    (SELECT last_heartbeat_at FROM heartbeat_state WHERE id = 1) as last_heartbeat,
    (SELECT next_heartbeat_at FROM heartbeat_state WHERE id = 1) as next_heartbeat,
    (SELECT is_paused FROM heartbeat_state WHERE id = 1) as is_paused,
    (SELECT COUNT(*) FROM memories WHERE type = 'goal' AND status = 'active' AND metadata->>'priority' = 'active') as active_goals,
    (SELECT COUNT(*) FROM memories WHERE type = 'goal' AND status = 'active' AND metadata->>'priority' = 'queued') as queued_goals,
    0::int as pending_calls,
    NULL::float as avg_energy_delta_24h,
    0::int as reach_outs_24h;

CREATE VIEW recent_heartbeats AS
SELECT
    m.id as memory_id,
    NULLIF(m.metadata#>>'{context,heartbeat_id}', '')::uuid as heartbeat_id,
    (m.metadata#>>'{context,heartbeat_number}')::int as heartbeat_number,
    COALESCE((m.metadata->>'event_time')::timestamptz, m.created_at) as started_at,
    COALESCE((m.metadata->>'event_time')::timestamptz, m.created_at) as ended_at,
    NULL::float as energy_start,
    NULL::float as energy_end,
    jsonb_array_length(COALESCE(m.metadata#>'{context,actions_taken}', '[]'::jsonb)) as action_count,
    m.content as narrative,
    NULLIF(m.metadata->>'emotional_valence', '')::float as emotional_valence
FROM memories m
WHERE m.type = 'episodic'
  AND m.metadata#>>'{context,heartbeat_id}' IS NOT NULL
ORDER BY COALESCE((m.metadata->>'event_time')::timestamptz, m.created_at) DESC
LIMIT 20;
-- ============================================================================
-- BOUNDARIES
-- ============================================================================
-- Boundaries are worldview memories with metadata->>'category' = 'boundary'.
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
        query_emb := get_embedding(p_content);
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
CREATE OR REPLACE VIEW boundary_status AS
SELECT
    content as name,
    COALESCE(metadata->>'subcategory', 'ethical') as boundary_type,
    metadata->>'response_type' as response_type,
    importance,
    1.0 - COALESCE((metadata->>'stability')::float, 0.9) as flexibility,
    0::bigint as times_triggered
FROM memories
WHERE type = 'worldview' AND metadata->>'category' = 'boundary' AND status = 'active'
ORDER BY importance DESC;
-- ============================================================================
-- EMOTIONAL STATE
-- ============================================================================
CREATE TABLE emotional_triggers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trigger_pattern TEXT NOT NULL,
    trigger_embedding vector(768) NOT NULL,
    valence_delta FLOAT NOT NULL DEFAULT 0.0,
    arousal_delta FLOAT NOT NULL DEFAULT 0.0,
    dominance_delta FLOAT NOT NULL DEFAULT 0.0,
    typical_emotion TEXT,
    times_activated INT DEFAULT 1,
    confidence FLOAT DEFAULT 0.5,
    origin TEXT NOT NULL,
    source_memory_ids UUID[] DEFAULT '{}'::uuid[],
    last_activated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT emotional_triggers_confidence_range CHECK (confidence BETWEEN 0 AND 1)
);

CREATE UNIQUE INDEX idx_emotional_triggers_pattern ON emotional_triggers (trigger_pattern);
CREATE INDEX idx_emotional_triggers_embedding ON emotional_triggers USING hnsw (trigger_embedding vector_cosine_ops);
CREATE UNLOGGED TABLE memory_activation (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_embedding vector(768) NOT NULL,
    query_text TEXT,
    estimated_matches INT DEFAULT 0,
    activation_strength FLOAT DEFAULT 0.5,
    retrieval_attempted BOOLEAN DEFAULT FALSE,
    retrieval_succeeded BOOLEAN DEFAULT NULL,
    background_search_pending BOOLEAN DEFAULT FALSE,
    background_search_started_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP + INTERVAL '1 hour'
);

CREATE INDEX idx_memory_activation_embedding ON memory_activation USING hnsw (query_embedding vector_cosine_ops);
CREATE INDEX idx_memory_activation_pending ON memory_activation (background_search_pending)
    WHERE background_search_pending = TRUE;
CREATE INDEX idx_memory_activation_pending_started ON memory_activation (background_search_started_at, created_at)
    WHERE background_search_pending = TRUE;
CREATE INDEX idx_memory_activation_expires_at ON memory_activation (expires_at);
DO $$
DECLARE
    dim INT;
BEGIN
    dim := embedding_dimension();
    EXECUTE format(
        'ALTER TABLE emotional_triggers ALTER COLUMN trigger_embedding TYPE vector(%s) USING trigger_embedding::vector(%s)',
        dim,
        dim
    );
    EXECUTE format(
        'ALTER TABLE memory_activation ALTER COLUMN query_embedding TYPE vector(%s) USING query_embedding::vector(%s)',
        dim,
        dim
    );
END;
$$;
CREATE OR REPLACE VIEW current_emotional_state AS
SELECT
    gen_random_uuid() as id,
    COALESCE((affective_state->>'updated_at')::timestamptz, updated_at) as recorded_at,
    (affective_state->>'valence')::float as valence,
    (affective_state->>'arousal')::float as arousal,
    COALESCE((affective_state->>'dominance')::float, 0.5::float) as dominance,
    affective_state->>'primary_emotion' as primary_emotion,
    NULL::text[] as secondary_emotions,
    (affective_state->>'intensity')::float as intensity,
    NULL::text as triggered_by_type,
    NULL::uuid as triggered_by_id,
    NULL::text as trigger_description,
    NULL::uuid as heartbeat_id
FROM heartbeat_state
WHERE id = 1;
CREATE OR REPLACE FUNCTION normalize_affective_state(p_state JSONB)
RETURNS JSONB AS $$
DECLARE
    baseline JSONB;
    valence FLOAT;
    arousal FLOAT;
    dominance FLOAT;
    intensity FLOAT;
    trigger_summary TEXT;
    secondary_emotion TEXT;
    mood_valence FLOAT;
    mood_arousal FLOAT;
    primary_emotion TEXT;
    source TEXT;
    updated_at TIMESTAMPTZ;
    mood_updated_at TIMESTAMPTZ;
BEGIN
    baseline := COALESCE(get_config('emotion.baseline'), '{}'::jsonb);

    BEGIN
        valence := NULLIF(p_state->>'valence', '')::float;
    EXCEPTION
        WHEN OTHERS THEN
            valence := NULL;
    END;
    BEGIN
        arousal := NULLIF(p_state->>'arousal', '')::float;
    EXCEPTION
        WHEN OTHERS THEN
            arousal := NULL;
    END;
    BEGIN
        dominance := NULLIF(p_state->>'dominance', '')::float;
    EXCEPTION
        WHEN OTHERS THEN
            dominance := NULL;
    END;
    BEGIN
        intensity := NULLIF(p_state->>'intensity', '')::float;
    EXCEPTION
        WHEN OTHERS THEN
            intensity := NULL;
    END;
    BEGIN
        mood_valence := NULLIF(p_state->>'mood_valence', '')::float;
    EXCEPTION
        WHEN OTHERS THEN
            mood_valence := NULL;
    END;
    BEGIN
        mood_arousal := NULLIF(p_state->>'mood_arousal', '')::float;
    EXCEPTION
        WHEN OTHERS THEN
            mood_arousal := NULL;
    END;
    BEGIN
        updated_at := NULLIF(p_state->>'updated_at', '')::timestamptz;
    EXCEPTION
        WHEN OTHERS THEN
            updated_at := NULL;
    END;
    BEGIN
        mood_updated_at := NULLIF(p_state->>'mood_updated_at', '')::timestamptz;
    EXCEPTION
        WHEN OTHERS THEN
            mood_updated_at := NULL;
    END;

    valence := COALESCE(valence, NULLIF(baseline->>'valence', '')::float, 0.0);
    arousal := COALESCE(arousal, NULLIF(baseline->>'arousal', '')::float, 0.5);
    dominance := COALESCE(dominance, NULLIF(baseline->>'dominance', '')::float, 0.5);
    intensity := COALESCE(intensity, NULLIF(baseline->>'intensity', '')::float, 0.5);
    mood_valence := COALESCE(mood_valence, NULLIF(baseline->>'mood_valence', '')::float, valence);
    mood_arousal := COALESCE(mood_arousal, NULLIF(baseline->>'mood_arousal', '')::float, arousal);

    valence := LEAST(1.0, GREATEST(-1.0, valence));
    arousal := LEAST(1.0, GREATEST(0.0, arousal));
    dominance := LEAST(1.0, GREATEST(0.0, dominance));
    intensity := LEAST(1.0, GREATEST(0.0, intensity));
    mood_valence := LEAST(1.0, GREATEST(-1.0, mood_valence));
    mood_arousal := LEAST(1.0, GREATEST(0.0, mood_arousal));

    primary_emotion := COALESCE(NULLIF(p_state->>'primary_emotion', ''), 'neutral');
    secondary_emotion := NULLIF(p_state->>'secondary_emotion', '');
    trigger_summary := NULLIF(p_state->>'trigger_summary', '');
    source := COALESCE(NULLIF(p_state->>'source', ''), 'derived');
    updated_at := COALESCE(updated_at, CURRENT_TIMESTAMP);
    mood_updated_at := COALESCE(mood_updated_at, updated_at);

    RETURN jsonb_build_object(
        'valence', valence,
        'arousal', arousal,
        'dominance', dominance,
        'primary_emotion', primary_emotion,
        'secondary_emotion', secondary_emotion,
        'intensity', intensity,
        'trigger_summary', trigger_summary,
        'source', source,
        'updated_at', updated_at,
        'mood_valence', mood_valence,
        'mood_arousal', mood_arousal,
        'mood_updated_at', mood_updated_at
    );
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION get_current_affective_state()
RETURNS JSONB AS $$
DECLARE
    st RECORD;
    state_json JSONB;
BEGIN
    SELECT * INTO st FROM heartbeat_state WHERE id = 1;

    state_json := COALESCE(st.affective_state, '{}'::jsonb);
    RETURN normalize_affective_state(state_json);
EXCEPTION
    WHEN OTHERS THEN
        RETURN '{}'::jsonb;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION set_current_affective_state(p_state JSONB)
RETURNS VOID AS $$
DECLARE
    current_state JSONB;
    merged_state JSONB;
BEGIN
    SELECT affective_state INTO current_state FROM heartbeat_state WHERE id = 1;
    merged_state := COALESCE(current_state, '{}'::jsonb) || COALESCE(p_state, '{}'::jsonb);
    merged_state := jsonb_set(merged_state, '{updated_at}', to_jsonb(CURRENT_TIMESTAMP), true);
    merged_state := normalize_affective_state(merged_state);

    UPDATE heartbeat_state
    SET affective_state = merged_state,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_emotional_context_for_memory()
RETURNS JSONB AS $$
DECLARE
    st JSONB;
BEGIN
    st := get_current_affective_state();
    RETURN jsonb_build_object(
        'valence', (st->>'valence')::float,
        'arousal', (st->>'arousal')::float,
        'dominance', (st->>'dominance')::float,
        'primary_emotion', COALESCE(st->>'primary_emotion', 'neutral'),
        'intensity', (st->>'intensity')::float,
        'source', COALESCE(st->>'source', 'derived')
    );
EXCEPTION
    WHEN OTHERS THEN
        RETURN jsonb_build_object(
            'valence', 0.0,
            'arousal', 0.5,
            'dominance', 0.5,
            'primary_emotion', 'neutral',
            'intensity', 0.5,
            'source', 'default'
        );
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION regulate_emotional_state(
    p_regulation_type TEXT,
    p_target_emotion TEXT DEFAULT NULL,
    p_intensity_change FLOAT DEFAULT NULL
)
RETURNS JSONB AS $$
DECLARE
    current_state JSONB;
    new_valence FLOAT;
    new_arousal FLOAT;
    new_intensity FLOAT;
    new_primary TEXT;
    dominance FLOAT;
BEGIN
    current_state := get_current_affective_state();
    new_valence := COALESCE((current_state->>'valence')::float, 0.0);
    new_arousal := COALESCE((current_state->>'arousal')::float, 0.5);
    new_intensity := COALESCE((current_state->>'intensity')::float, 0.5);
    dominance := COALESCE((current_state->>'dominance')::float, 0.5);
    new_primary := COALESCE(NULLIF(p_target_emotion, ''), current_state->>'primary_emotion', 'neutral');

    CASE p_regulation_type
        WHEN 'suppress' THEN
            new_valence := new_valence * 0.3;
            new_arousal := new_arousal * 0.5 + 0.15;
            new_intensity := new_intensity * 0.3;
        WHEN 'reduce' THEN
            new_valence := new_valence * 0.7;
            new_arousal := new_arousal * 0.8;
            new_intensity := new_intensity * 0.6;
        WHEN 'amplify' THEN
            new_valence := new_valence * 1.3;
            new_arousal := LEAST(1.0, new_arousal * 1.2);
            new_intensity := LEAST(1.0, new_intensity * 1.5);
        WHEN 'reframe' THEN
            new_valence := COALESCE(
                CASE WHEN p_target_emotion IN ('interest', 'curiosity') THEN 0.2
                     WHEN p_target_emotion IN ('acceptance', 'peace') THEN 0.1
                     ELSE new_valence * 0.5
                END,
                new_valence * 0.5
            );
            new_arousal := new_arousal * 0.8;
            new_intensity := new_intensity * 0.7;
        ELSE
            RETURN jsonb_build_object('error', 'unknown_regulation_type');
    END CASE;

    PERFORM set_current_affective_state(jsonb_build_object(
        'valence', new_valence,
        'arousal', new_arousal,
        'dominance', dominance,
        'primary_emotion', new_primary,
        'intensity', new_intensity,
        'source', 'regulated',
        'trigger_summary', format('Regulated via %s', p_regulation_type)
    ));

    RETURN jsonb_build_object(
        'success', true,
        'regulation_type', p_regulation_type,
        'before', current_state,
        'after', get_current_affective_state()
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION sense_memory_availability(
    p_query TEXT,
    p_query_embedding vector DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    query_emb vector;
    zero_vec vector;
    estimated_count INT;
    top_similarity FLOAT;
    activation_id UUID;
BEGIN
    query_emb := COALESCE(p_query_embedding, get_embedding(p_query));
    zero_vec := array_fill(0.0::float, ARRAY[embedding_dimension()])::vector;

    SELECT
        COUNT(*),
        MAX(1 - (embedding <=> query_emb))
    INTO estimated_count, top_similarity
    FROM memories
    WHERE status = 'active'
      AND embedding IS NOT NULL
      AND embedding <> zero_vec
      AND (1 - (embedding <=> query_emb)) > 0.5
    LIMIT 100;

    INSERT INTO memory_activation (
        query_embedding,
        query_text,
        estimated_matches,
        activation_strength
    ) VALUES (
        query_emb,
        p_query,
        estimated_count,
        COALESCE(top_similarity, 0)
    )
    RETURNING id INTO activation_id;

    RETURN jsonb_build_object(
        'feeling', CASE
            WHEN estimated_count = 0 THEN 'nothing'
            WHEN estimated_count <= 2 THEN 'vague'
            WHEN estimated_count <= 5 THEN 'something'
            WHEN estimated_count <= 10 THEN 'familiar'
            ELSE 'rich'
        END,
        'estimated_count', estimated_count,
        'strongest_match', top_similarity,
        'activation_id', activation_id,
        'description', CASE
            WHEN estimated_count = 0 THEN 'I don''t think I know anything about this'
            WHEN top_similarity > 0.8 THEN 'I know this well - let me recall'
            WHEN top_similarity > 0.6 THEN 'This feels familiar - I should be able to remember'
            WHEN estimated_count > 0 THEN 'I might know something about this - it''s not coming immediately'
            ELSE 'I don''t think I know anything about this'
        END
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION request_background_search(
    p_query TEXT,
    p_query_embedding vector DEFAULT NULL
) RETURNS UUID AS $$
DECLARE
    query_emb vector;
    activation_id UUID;
BEGIN
    query_emb := COALESCE(p_query_embedding, get_embedding(p_query));

    INSERT INTO memory_activation (
        query_embedding,
        query_text,
        retrieval_attempted,
        retrieval_succeeded,
        background_search_pending,
        background_search_started_at
    ) VALUES (
        query_emb,
        p_query,
        TRUE,
        FALSE,
        TRUE,
        CURRENT_TIMESTAMP
    )
    RETURNING id INTO activation_id;

    RETURN activation_id;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION process_background_searches(
    p_limit INT DEFAULT 10,
    p_min_age INTERVAL DEFAULT INTERVAL '30 seconds'
)
RETURNS INT AS $$
DECLARE
    pending RECORD;
    processed_count INT := 0;
BEGIN
    FOR pending IN
        SELECT * FROM memory_activation
        WHERE background_search_pending = TRUE
          AND background_search_started_at <= CURRENT_TIMESTAMP - p_min_age
        ORDER BY created_at ASC
        LIMIT GREATEST(1, COALESCE(p_limit, 10))
    LOOP
        UPDATE memories
        SET metadata = jsonb_set(
            COALESCE(metadata, '{}'::jsonb),
            '{activation_boost}',
            to_jsonb(COALESCE((metadata->>'activation_boost')::float, 0) + 0.2)
        )
        WHERE status = 'active'
          AND (1 - (embedding <=> pending.query_embedding)) > 0.6;

        UPDATE memory_activation
        SET background_search_pending = FALSE,
            retrieval_succeeded = TRUE
        WHERE id = pending.id;

        processed_count := processed_count + 1;
    END LOOP;

    RETURN processed_count;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION decay_activation_boosts(p_decay FLOAT DEFAULT 0.05)
RETURNS INT AS $$
DECLARE
    updated_count INT;
BEGIN
    UPDATE memories
    SET metadata = jsonb_set(
        COALESCE(metadata, '{}'::jsonb),
        '{activation_boost}',
        to_jsonb(GREATEST(0, COALESCE((metadata->>'activation_boost')::float, 0) - COALESCE(p_decay, 0.05)))
    )
    WHERE (metadata->>'activation_boost')::float > 0;
    GET DIAGNOSTICS updated_count = ROW_COUNT;
    RETURN COALESCE(updated_count, 0);
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION cleanup_memory_activations()
RETURNS INT AS $$
DECLARE
    deleted_count INT;
BEGIN
    DELETE FROM memory_activation WHERE expires_at < CURRENT_TIMESTAMP;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN COALESCE(deleted_count, 0);
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_spontaneous_memories(p_limit INT DEFAULT 3)
RETURNS SETOF memories AS $$
BEGIN
    RETURN QUERY
    SELECT * FROM memories
    WHERE status = 'active'
      AND (metadata->>'activation_boost')::float > 0.3
    ORDER BY (metadata->>'activation_boost')::float DESC
    LIMIT GREATEST(1, COALESCE(p_limit, 3));
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION update_mood()
RETURNS VOID AS $$
DECLARE
    baseline JSONB;
    decay_rate FLOAT;
    current_state JSONB;
    recent RECORD;
    new_mood_valence FLOAT;
    new_mood_arousal FLOAT;
BEGIN
    baseline := COALESCE(get_config('emotion.baseline'), '{}'::jsonb);
    decay_rate := COALESCE(NULLIF(baseline->>'decay_rate', '')::float, 0.1);

    current_state := get_current_affective_state();

    SELECT
        AVG(NULLIF(m.metadata->>'emotional_valence', '')::float) as avg_valence,
        COUNT(*) as sample_count
    INTO recent
    FROM memories m
    WHERE m.type = 'episodic'
      AND m.metadata#>>'{context,heartbeat_id}' IS NOT NULL
      AND COALESCE((m.metadata->>'event_time')::timestamptz, m.created_at)
            > CURRENT_TIMESTAMP - INTERVAL '2 hours'
      AND m.metadata->>'emotional_valence' IS NOT NULL;

    new_mood_valence := COALESCE((current_state->>'mood_valence')::float, 0.0);
    new_mood_arousal := COALESCE((current_state->>'mood_arousal')::float, 0.3);

    IF recent.sample_count > 0 THEN
        new_mood_valence := new_mood_valence * (1 - decay_rate) + COALESCE(recent.avg_valence, 0.0) * decay_rate;
    ELSE
        new_mood_valence := new_mood_valence * (1 - decay_rate);
    END IF;

    new_mood_arousal := new_mood_arousal * (1 - decay_rate * 0.5)
        + COALESCE(NULLIF(baseline->>'mood_arousal', '')::float, 0.3) * decay_rate * 0.5;

    PERFORM set_current_affective_state(jsonb_build_object(
        'mood_valence', new_mood_valence,
        'mood_arousal', new_mood_arousal,
        'mood_updated_at', CURRENT_TIMESTAMP
    ));
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION learn_emotional_trigger(
    p_trigger_text TEXT,
    p_trigger_embedding vector,
    p_emotional_response JSONB,
    p_source_memory_id UUID DEFAULT NULL
) RETURNS UUID AS $$
DECLARE
    existing RECORD;
    baseline JSONB;
    trigger_id UUID;
BEGIN
    baseline := COALESCE(get_config('emotion.baseline'), '{}'::jsonb);

    SELECT * INTO existing
    FROM emotional_triggers
    WHERE (1 - (trigger_embedding <=> p_trigger_embedding)) > 0.85
    ORDER BY (1 - (trigger_embedding <=> p_trigger_embedding)) DESC
    LIMIT 1;

    IF existing IS NOT NULL THEN
        UPDATE emotional_triggers
        SET
            valence_delta = (valence_delta * times_activated +
                ((p_emotional_response->>'valence')::float - COALESCE((baseline->>'valence')::float, 0.0)))
                / (times_activated + 1),
            arousal_delta = (arousal_delta * times_activated +
                ((p_emotional_response->>'arousal')::float - COALESCE((baseline->>'arousal')::float, 0.3)))
                / (times_activated + 1),
            dominance_delta = (dominance_delta * times_activated +
                ((p_emotional_response->>'dominance')::float - COALESCE((baseline->>'dominance')::float, 0.5)))
                / (times_activated + 1),
            times_activated = times_activated + 1,
            confidence = LEAST(0.95, confidence + 0.02),
            last_activated_at = CURRENT_TIMESTAMP,
            source_memory_ids = CASE
                WHEN p_source_memory_id IS NOT NULL THEN array_append(source_memory_ids, p_source_memory_id)
                ELSE source_memory_ids
            END
        WHERE id = existing.id;
        RETURN existing.id;
    END IF;

    INSERT INTO emotional_triggers (
        trigger_pattern,
        trigger_embedding,
        valence_delta,
        arousal_delta,
        dominance_delta,
        typical_emotion,
        origin,
        source_memory_ids,
        last_activated_at
    ) VALUES (
        p_trigger_text,
        p_trigger_embedding,
        (p_emotional_response->>'valence')::float - COALESCE((baseline->>'valence')::float, 0.0),
        (p_emotional_response->>'arousal')::float - COALESCE((baseline->>'arousal')::float, 0.3),
        (p_emotional_response->>'dominance')::float - COALESCE((baseline->>'dominance')::float, 0.5),
        p_emotional_response->>'primary_emotion',
        'learned',
        CASE WHEN p_source_memory_id IS NOT NULL THEN ARRAY[p_source_memory_id] ELSE '{}'::uuid[] END,
        CURRENT_TIMESTAMP
    )
    RETURNING id INTO trigger_id;

    RETURN trigger_id;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION match_emotional_triggers(
    p_text TEXT,
    p_limit INT DEFAULT 5,
    p_min_similarity FLOAT DEFAULT 0.75
) RETURNS JSONB AS $$
DECLARE
    query_emb vector;
BEGIN
    IF p_text IS NULL OR btrim(p_text) = '' THEN
        RETURN '[]'::jsonb;
    END IF;

    BEGIN
        query_emb := get_embedding(p_text);
    EXCEPTION
        WHEN OTHERS THEN
            RETURN '[]'::jsonb;
    END;

    RETURN COALESCE((
        SELECT jsonb_agg(jsonb_build_object(
            'trigger_pattern', trigger_pattern,
            'similarity', sim,
            'typical_emotion', typical_emotion,
            'valence_delta', valence_delta,
            'arousal_delta', arousal_delta,
            'dominance_delta', dominance_delta,
            'confidence', confidence,
            'times_activated', times_activated
        ))
        FROM (
            SELECT
                et.*,
                (1 - (et.trigger_embedding <=> query_emb))::float as sim
            FROM emotional_triggers et
            WHERE (1 - (et.trigger_embedding <=> query_emb)) >= COALESCE(p_min_similarity, 0.75)
            ORDER BY sim DESC
            LIMIT GREATEST(1, COALESCE(p_limit, 5))
        ) ranked
    ), '[]'::jsonb);
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION initialize_innate_emotions()
RETURNS INT AS $$
DECLARE
    inserted_count INT := 0;
BEGIN
    BEGIN
        INSERT INTO emotional_triggers (trigger_pattern, trigger_embedding, valence_delta, arousal_delta, dominance_delta, typical_emotion, origin)
        VALUES
            ('gratitude appreciation thankful', get_embedding('gratitude appreciation thankful'), 0.4, 0.1, 0.1, 'joy', 'innate'),
            ('success achieved accomplished', get_embedding('success achieved accomplished'), 0.5, 0.3, 0.3, 'pride', 'innate'),
            ('curious interesting fascinating', get_embedding('curious interesting fascinating'), 0.3, 0.3, 0.1, 'interest', 'innate'),
            ('understood seen connected', get_embedding('understood seen connected'), 0.4, 0.2, 0.2, 'warmth', 'innate'),
            ('beautiful elegant aesthetic', get_embedding('beautiful elegant aesthetic'), 0.3, 0.2, 0.1, 'appreciation', 'innate'),
            ('learned insight realized', get_embedding('learned insight realized'), 0.4, 0.4, 0.2, 'satisfaction', 'innate'),
            ('threat danger harm', get_embedding('threat danger harm'), -0.5, 0.6, -0.3, 'fear', 'innate'),
            ('rejection dismissed ignored', get_embedding('rejection dismissed ignored'), -0.4, 0.2, -0.2, 'sadness', 'innate'),
            ('unfair unjust wrong', get_embedding('unfair unjust wrong'), -0.4, 0.5, 0.2, 'anger', 'innate'),
            ('confused lost uncertain', get_embedding('confused lost uncertain'), -0.2, 0.3, -0.2, 'anxiety', 'innate'),
            ('failed mistake error', get_embedding('failed mistake error'), -0.3, 0.3, -0.1, 'disappointment', 'innate'),
            ('violated boundary crossed', get_embedding('violated boundary crossed'), -0.5, 0.5, -0.2, 'alarm', 'innate'),
            ('unexpected surprise sudden', get_embedding('unexpected surprise sudden'), 0.0, 0.6, -0.1, 'surprise', 'innate'),
            ('conflict tension disagree', get_embedding('conflict tension disagree'), -0.2, 0.4, 0.0, 'discomfort', 'innate')
        ON CONFLICT DO NOTHING;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
    EXCEPTION
        WHEN OTHERS THEN
            inserted_count := 0;
    END;

    RETURN inserted_count;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION ensure_emotion_bootstrap()
RETURNS VOID AS $$
DECLARE
    initialized JSONB;
    baseline JSONB;
BEGIN
    initialized := COALESCE(get_config('emotion.initialized'), 'false'::jsonb);
    IF initialized = 'true'::jsonb THEN
        RETURN;
    END IF;

    baseline := COALESCE(get_config('emotion.baseline'), '{}'::jsonb);
    PERFORM set_current_affective_state(jsonb_build_object(
        'valence', COALESCE((baseline->>'valence')::float, 0.0),
        'arousal', COALESCE((baseline->>'arousal')::float, 0.3),
        'dominance', COALESCE((baseline->>'dominance')::float, 0.5),
        'intensity', COALESCE((baseline->>'intensity')::float, 0.4),
        'mood_valence', COALESCE((baseline->>'mood_valence')::float, 0.0),
        'mood_arousal', COALESCE((baseline->>'mood_arousal')::float, 0.3),
        'source', 'baseline'
    ));

    PERFORM initialize_innate_emotions();
    PERFORM set_config('emotion.initialized', 'true'::jsonb);
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION apply_emotional_context_to_memory()
RETURNS TRIGGER AS $$
DECLARE
    meta JSONB;
    context JSONB;
    state JSONB;
    valence FLOAT;
    arousal FLOAT;
    dominance FLOAT;
    intensity FLOAT;
    primary_emotion TEXT;
    source TEXT;
BEGIN
    meta := COALESCE(NEW.metadata, '{}'::jsonb);
    context := COALESCE(meta->'emotional_context', '{}'::jsonb);
    state := get_current_affective_state();

    BEGIN
        valence := NULLIF(meta->>'emotional_valence', '')::float;
    EXCEPTION
        WHEN OTHERS THEN
            valence := NULL;
    END;
    BEGIN
        arousal := NULLIF(context->>'arousal', '')::float;
    EXCEPTION
        WHEN OTHERS THEN
            arousal := NULL;
    END;
    BEGIN
        dominance := NULLIF(context->>'dominance', '')::float;
    EXCEPTION
        WHEN OTHERS THEN
            dominance := NULL;
    END;
    BEGIN
        intensity := NULLIF(context->>'intensity', '')::float;
    EXCEPTION
        WHEN OTHERS THEN
            intensity := NULL;
    END;

    valence := COALESCE(valence, NULLIF(context->>'valence', '')::float, (state->>'valence')::float, 0.0);
    arousal := COALESCE(arousal, NULLIF(state->>'arousal', '')::float, 0.5);
    dominance := COALESCE(dominance, NULLIF(state->>'dominance', '')::float, 0.5);
    intensity := COALESCE(intensity, NULLIF(state->>'intensity', '')::float, 0.5);
    primary_emotion := COALESCE(NULLIF(context->>'primary_emotion', ''), NULLIF(state->>'primary_emotion', ''), 'neutral');
    source := COALESCE(NULLIF(context->>'source', ''), NULLIF(state->>'source', ''), 'derived');

    valence := LEAST(1.0, GREATEST(-1.0, valence));
    arousal := LEAST(1.0, GREATEST(0.0, arousal));
    dominance := LEAST(1.0, GREATEST(0.0, dominance));
    intensity := LEAST(1.0, GREATEST(0.0, intensity));

    context := jsonb_build_object(
        'valence', valence,
        'arousal', arousal,
        'dominance', dominance,
        'primary_emotion', primary_emotion,
        'intensity', intensity,
        'source', source
    );

    NEW.metadata := meta || jsonb_build_object(
        'emotional_context', context,
        'emotional_valence', valence
    );

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS memories_emotional_context_insert ON memories;
CREATE TRIGGER memories_emotional_context_insert
BEFORE INSERT ON memories
FOR EACH ROW
EXECUTE FUNCTION apply_emotional_context_to_memory();
CREATE OR REPLACE FUNCTION gather_turn_context()
RETURNS JSONB AS $$
DECLARE
    state_record RECORD;
    action_costs JSONB;
    contradictions JSONB;
BEGIN
    SELECT * INTO state_record FROM heartbeat_state WHERE id = 1;
    SELECT jsonb_object_agg(
        regexp_replace(key, '^heartbeat\.cost_', ''),
        value
    ) INTO action_costs
    FROM config
    WHERE key LIKE 'heartbeat.cost_%';

    contradictions := get_contradictions_context(5);

    RETURN jsonb_build_object(
        'agent', get_agent_profile_context(),
        'environment', get_environment_snapshot(),
        'goals', get_goals_snapshot(),
        'recent_memories', get_recent_context(5),
        'identity', get_identity_context(),
        'worldview', get_worldview_context(),
        'self_model', get_self_model_context(25),
        'narrative', get_narrative_context(),
        'relationships', get_relationships_context(10),
        'contradictions', contradictions,
        'contradictions_count', COALESCE(jsonb_array_length(contradictions), 0),
        'emotional_patterns', get_emotional_patterns_context(5),
        'active_transformations', get_active_transformations_context(5),
        'transformations_ready', check_transformation_readiness(),
        'energy', jsonb_build_object(
            'current', state_record.current_energy,
            'max', get_config_float('heartbeat.max_energy')
        ),
        'action_costs', action_costs,
        'heartbeat_number', state_record.heartbeat_count,
        'urgent_drives', (
            SELECT COALESCE(
                jsonb_agg(
                    jsonb_build_object(
                        'name', name,
                        'level', current_level,
                        'urgency_ratio', current_level / NULLIF(urgency_threshold, 0)
                    )
                    ORDER BY current_level DESC
                ),
                '[]'::jsonb
            )
            FROM drives
            WHERE current_level >= urgency_threshold * 0.8
        ),
        'emotional_state', get_current_affective_state()
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION complete_heartbeat(
    p_heartbeat_id UUID,
    p_reasoning TEXT,
    p_actions_taken JSONB,
    p_goals_modified JSONB DEFAULT '[]',
    p_emotional_assessment JSONB DEFAULT NULL
)
RETURNS UUID AS $$
DECLARE
    narrative_text TEXT;
    memory_id_created UUID;
    hb_number INT;
    state_record RECORD;
    prev_state JSONB;
    prev_valence FLOAT;
    prev_arousal FLOAT;
    prev_dominance FLOAT;
    new_valence FLOAT;
    new_arousal FLOAT;
    primary_emotion TEXT;
    intensity FLOAT;
    action_elem JSONB;
    goal_elem JSONB;
    goal_change TEXT;
    assess_valence FLOAT;
    assess_arousal FLOAT;
    assess_primary TEXT;
    mem_importance FLOAT;
BEGIN
    SELECT active_heartbeat_number INTO hb_number FROM heartbeat_state WHERE id = 1;
    IF hb_number IS NULL THEN
        SELECT heartbeat_count INTO hb_number FROM heartbeat_state WHERE id = 1;
    END IF;

    SELECT string_agg(
        format('- %s: %s',
            a->>'action',
            CASE
                WHEN COALESCE((a->'result'->>'success')::boolean, true) = false THEN 'failed'
                ELSE 'completed'
            END
        ), E'\n'
    ) INTO narrative_text
    FROM jsonb_array_elements(p_actions_taken) a;

    narrative_text := format('Heartbeat #%s: %s', hb_number, COALESCE(narrative_text, 'No actions taken'));

    SELECT * INTO state_record FROM heartbeat_state WHERE id = 1;
    prev_state := COALESCE(state_record.affective_state, '{}'::jsonb);

    BEGIN
        prev_valence := NULLIF(prev_state->>'valence', '')::float;
    EXCEPTION
        WHEN OTHERS THEN
            prev_valence := NULL;
    END;
    BEGIN
        prev_arousal := NULLIF(prev_state->>'arousal', '')::float;
    EXCEPTION
        WHEN OTHERS THEN
            prev_arousal := NULL;
    END;

    prev_valence := COALESCE(prev_valence, 0.0);
    prev_arousal := COALESCE(prev_arousal, 0.5);
    BEGIN
        prev_dominance := NULLIF(prev_state->>'dominance', '')::float;
    EXCEPTION
        WHEN OTHERS THEN
            prev_dominance := NULL;
    END;
    prev_dominance := COALESCE(prev_dominance, 0.5);
    new_valence := prev_valence * 0.8;
    new_arousal := 0.5 + (prev_arousal - 0.5) * 0.8;
    FOR action_elem IN SELECT * FROM jsonb_array_elements(COALESCE(p_actions_taken, '[]'::jsonb))
    LOOP
        IF (action_elem->'result'->>'error') = 'Boundary triggered' THEN
            new_valence := new_valence - 0.4;
            new_arousal := new_arousal + 0.3;
        ELSIF COALESCE((action_elem->'result'->>'success')::boolean, true) = false THEN
            new_valence := new_valence - 0.1;
            new_arousal := new_arousal + 0.1;
        END IF;

        IF (action_elem->>'action') IN ('reach_out_user', 'reach_out_public') THEN
            IF COALESCE((action_elem->'result'->>'success')::boolean, true) = true THEN
                new_valence := new_valence + 0.2;
                new_arousal := new_arousal + 0.1;
            END IF;
        END IF;

        IF (action_elem->>'action') = 'rest' THEN
            new_valence := new_valence + 0.1;
            new_arousal := new_arousal - 0.2;
        END IF;
    END LOOP;
    FOR goal_elem IN SELECT * FROM jsonb_array_elements(COALESCE(p_goals_modified, '[]'::jsonb))
    LOOP
        goal_change := COALESCE(goal_elem->>'new_priority', goal_elem->>'change', goal_elem->>'priority', '');

        IF goal_change = 'completed' THEN
            new_valence := new_valence + 0.3;
            new_arousal := new_arousal + 0.1;
        ELSIF goal_change = 'abandoned' THEN
            new_valence := new_valence - 0.2;
            new_arousal := new_arousal - 0.1;
        END IF;
    END LOOP;
    assess_valence := NULL;
    assess_arousal := NULL;
    assess_primary := NULL;
    IF p_emotional_assessment IS NOT NULL AND jsonb_typeof(p_emotional_assessment) = 'object' THEN
        BEGIN
            assess_valence := NULLIF(p_emotional_assessment->>'valence', '')::float;
        EXCEPTION
            WHEN OTHERS THEN
                assess_valence := NULL;
        END;
        BEGIN
            assess_arousal := NULLIF(p_emotional_assessment->>'arousal', '')::float;
        EXCEPTION
            WHEN OTHERS THEN
                assess_arousal := NULL;
        END;
        assess_primary := NULLIF(p_emotional_assessment->>'primary_emotion', '');
    END IF;

    IF assess_valence IS NOT NULL THEN
        new_valence := new_valence * 0.6 + LEAST(1.0, GREATEST(-1.0, assess_valence)) * 0.4;
    END IF;
    IF assess_arousal IS NOT NULL THEN
        new_arousal := new_arousal * 0.6 + LEAST(1.0, GREATEST(0.0, assess_arousal)) * 0.4;
    END IF;

    new_valence := LEAST(1.0, GREATEST(-1.0, new_valence));
    new_arousal := LEAST(1.0, GREATEST(0.0, new_arousal));

    primary_emotion := COALESCE(
        assess_primary,
        CASE
            WHEN new_valence > 0.2 AND new_arousal > 0.6 THEN 'excited'
            WHEN new_valence > 0.2 THEN 'content'
            WHEN new_valence < -0.2 AND new_arousal > 0.6 THEN 'anxious'
            WHEN new_valence < -0.2 THEN 'down'
            ELSE 'neutral'
        END
    );

    intensity := LEAST(1.0, GREATEST(0.0, (ABS(new_valence) * 0.6 + new_arousal * 0.4)));
    UPDATE heartbeat_state SET
        affective_state = normalize_affective_state(
            COALESCE(prev_state, '{}'::jsonb) || jsonb_build_object(
                'valence', new_valence,
                'arousal', new_arousal,
                'dominance', prev_dominance,
                'primary_emotion', primary_emotion,
                'intensity', intensity,
                'updated_at', CURRENT_TIMESTAMP,
                'source', CASE WHEN p_emotional_assessment IS NULL THEN 'derived' ELSE 'blended' END
            )
        )
    WHERE id = 1;

    mem_importance := LEAST(1.0, GREATEST(0.4, 0.5 + intensity * 0.25));

    memory_id_created := create_episodic_memory(
        p_content := narrative_text,
        p_context := jsonb_build_object(
            'heartbeat_id', p_heartbeat_id,
            'heartbeat_number', hb_number,
            'reasoning', p_reasoning,
            'actions_taken', p_actions_taken,
            'goal_changes', p_goals_modified,
            'affective_state', get_current_affective_state()
        ),
        p_emotional_valence := new_valence,
        p_importance := mem_importance
    );

    RAISE LOG 'Heartbeat % completed: %', hb_number, narrative_text;
    UPDATE heartbeat_state SET
        next_heartbeat_at = CURRENT_TIMESTAMP +
            (get_config_float('heartbeat.heartbeat_interval_minutes') || ' minutes')::INTERVAL,
        active_heartbeat_id = NULL,
        active_heartbeat_number = NULL,
        active_actions = '[]'::jsonb,
        active_reasoning = NULL,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;

    RETURN memory_id_created;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION finalize_heartbeat(
    p_heartbeat_id UUID,
    p_reasoning TEXT,
    p_actions_taken JSONB,
    p_goal_changes JSONB DEFAULT '[]',
    p_emotional_assessment JSONB DEFAULT NULL
)
RETURNS UUID AS $$
DECLARE
    actions JSONB := COALESCE(p_actions_taken, '[]'::jsonb);
    goals JSONB := COALESCE(p_goal_changes, '[]'::jsonb);
    memory_id_created UUID;
BEGIN
    IF jsonb_typeof(actions) <> 'array' THEN
        actions := '[]'::jsonb;
    END IF;
    IF jsonb_typeof(goals) <> 'array' THEN
        goals := '[]'::jsonb;
    END IF;

    PERFORM apply_goal_changes(goals);

    memory_id_created := complete_heartbeat(
        p_heartbeat_id,
        p_reasoning,
        actions,
        goals,
        p_emotional_assessment
    );
    RETURN memory_id_created;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE VIEW emotional_trend AS
WITH base AS (
    SELECT
        date_trunc(
            'hour',
            COALESCE((m.metadata->>'event_time')::timestamptz, m.created_at)
        ) as hour,
        NULLIF(m.metadata->>'emotional_valence', '')::float as emotional_valence,
        NULLIF(m.metadata#>>'{emotional_context,arousal}', '')::float as emotional_arousal,
        NULLIF(m.metadata#>>'{emotional_context,primary_emotion}', '') as emotional_primary_emotion
    FROM memories m
    WHERE m.type = 'episodic'
      AND m.metadata#>>'{context,heartbeat_id}' IS NOT NULL
      AND COALESCE((m.metadata->>'event_time')::timestamptz, m.created_at)
            > CURRENT_TIMESTAMP - INTERVAL '24 hours'
      AND m.metadata->>'emotional_valence' IS NOT NULL
)
SELECT
    base.hour,
    AVG(base.emotional_valence) as avg_valence,
    COALESCE(AVG(base.emotional_arousal), 0.5)::float as avg_arousal,
    COALESCE(
        (
            SELECT b2.emotional_primary_emotion
            FROM base b2
            WHERE b2.hour = base.hour
              AND b2.emotional_primary_emotion IS NOT NULL
            GROUP BY b2.emotional_primary_emotion
            ORDER BY COUNT(*) DESC
            LIMIT 1
        ),
        CASE
            WHEN AVG(base.emotional_valence) > 0.2 THEN 'content'
            WHEN AVG(base.emotional_valence) < -0.2 THEN 'down'
            ELSE 'neutral'
        END
    ) as dominant_emotion,
    COUNT(*) as state_changes
FROM base
GROUP BY base.hour
ORDER BY base.hour DESC;
-- ============================================================================
-- NEIGHBORHOOD RECOMPUTATION
-- ============================================================================
CREATE OR REPLACE FUNCTION recompute_neighborhood(
    p_memory_id UUID,
    p_neighbor_count INT DEFAULT 20,
    p_min_similarity FLOAT DEFAULT 0.5
)
RETURNS VOID AS $$
DECLARE
    memory_emb vector;
    zero_vec vector;
    neighbors JSONB;
BEGIN
    SELECT embedding INTO memory_emb
    FROM memories
    WHERE id = p_memory_id AND status = 'active';

    zero_vec := array_fill(0.0::float, ARRAY[embedding_dimension()])::vector;
    IF memory_emb IS NULL OR memory_emb = zero_vec THEN
        RETURN;
    END IF;

    SELECT jsonb_object_agg(id::text, round(similarity::numeric, 4))
    INTO neighbors
    FROM (
        SELECT m.id, 1 - (m.embedding <=> memory_emb) as similarity
        FROM memories m
        WHERE m.id != p_memory_id
          AND m.status = 'active'
          AND m.embedding IS NOT NULL
          AND m.embedding <> zero_vec
        ORDER BY m.embedding <=> memory_emb
        LIMIT p_neighbor_count
    ) sub
    WHERE similarity >= p_min_similarity;

    INSERT INTO memory_neighborhoods (memory_id, neighbors, computed_at, is_stale)
    VALUES (p_memory_id, COALESCE(neighbors, '{}'::jsonb), CURRENT_TIMESTAMP, FALSE)
    ON CONFLICT (memory_id) DO UPDATE SET
        neighbors = EXCLUDED.neighbors,
        computed_at = EXCLUDED.computed_at,
        is_stale = FALSE;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION batch_recompute_neighborhoods(
    p_batch_size INT DEFAULT 50
)
RETURNS INT AS $$
DECLARE
    recomputed INT := 0;
    mem_id UUID;
BEGIN
    FOR mem_id IN
        SELECT memory_id
        FROM memory_neighborhoods
        WHERE is_stale = TRUE
        ORDER BY computed_at ASC NULLS FIRST
        LIMIT p_batch_size
    LOOP
        PERFORM recompute_neighborhood(mem_id);
        recomputed := recomputed + 1;
    END LOOP;

    RETURN recomputed;
END;
$$ LANGUAGE plpgsql;
-- ============================================================================
-- GRAPH ENHANCEMENTS
-- ============================================================================
CREATE OR REPLACE FUNCTION discover_relationship(
    p_from_id UUID,
    p_to_id UUID,
    p_relationship_type graph_edge_type,
    p_confidence FLOAT DEFAULT 0.8,
    p_discovered_by TEXT DEFAULT 'reflection',
    p_heartbeat_id UUID DEFAULT NULL,
    p_discovery_context TEXT DEFAULT NULL
)
RETURNS VOID AS $$
BEGIN
    BEGIN
        PERFORM create_memory_relationship(
            p_from_id,
            p_to_id,
            p_relationship_type,
            jsonb_build_object(
                'confidence', p_confidence,
                'by', p_discovered_by,
                'context', p_discovery_context,
                'heartbeat_id', p_heartbeat_id
            )
        );
    EXCEPTION
        WHEN OTHERS THEN
            NULL;
    END;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION link_memory_supports_worldview(
    p_memory_id UUID,
    p_worldview_id UUID,
    p_strength FLOAT DEFAULT 0.8
)
RETURNS VOID AS $$
BEGIN
    EXECUTE format(
        'SELECT * FROM ag_catalog.cypher(''memory_graph'', $q$
            MATCH (m:MemoryNode {memory_id: %L})
            MATCH (w:MemoryNode {memory_id: %L})
            WHERE w.type = ''worldview''
            MERGE (m)-[r:SUPPORTS]->(w)
            SET r.strength = %s
            RETURN r
        $q$) as (result ag_catalog.agtype)',
        p_memory_id,
        p_worldview_id,
        COALESCE(p_strength, 0.8)
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION find_contradictions(p_memory_id UUID DEFAULT NULL)
RETURNS TABLE (
    memory_a UUID,
    memory_b UUID,
    content_a TEXT,
    content_b TEXT
) AS $$
DECLARE
    filter_clause TEXT;
    sql TEXT;
BEGIN
    filter_clause := CASE
        WHEN p_memory_id IS NULL THEN ''
        ELSE format('WHERE a.memory_id = %L OR b.memory_id = %L', p_memory_id, p_memory_id)
    END;

    sql := format($sql$
        WITH pairs AS (
            SELECT
                replace(a_id::text, '"', '')::uuid as a_uuid,
                replace(b_id::text, '"', '')::uuid as b_uuid
            FROM ag_catalog.cypher('memory_graph', $q$
                MATCH (a:MemoryNode)-[:CONTRADICTS]-(b:MemoryNode)
                %s
                RETURN a.memory_id, b.memory_id
            $q$) as (a_id ag_catalog.agtype, b_id ag_catalog.agtype)
        )
        SELECT
            p.a_uuid as memory_a,
            p.b_uuid as memory_b,
            ma.content as content_a,
            mb.content as content_b
        FROM pairs p
        JOIN memories ma ON ma.id = p.a_uuid
        JOIN memories mb ON mb.id = p.b_uuid
    $sql$, filter_clause);

    BEGIN
        RETURN QUERY EXECUTE sql;
    EXCEPTION
        WHEN OTHERS THEN
            RETURN;
    END;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION find_causal_chain(p_memory_id UUID, p_depth INT DEFAULT 3)
RETURNS TABLE (
    cause_id UUID,
    cause_content TEXT,
    relationship TEXT,
    distance INT
) AS $$
DECLARE
    sql TEXT;
BEGIN
    sql := format($sql$
        WITH hits AS (
            SELECT
                replace(cause_id_raw::text, '"', '')::uuid as cause_uuid,
                replace(rel_raw::text, '"', '') as rel_type,
                (dist_raw::text)::int as dist
            FROM ag_catalog.cypher('memory_graph', $q$
                MATCH path = (cause:MemoryNode)-[:CAUSES*1..%s]->(effect:MemoryNode {memory_id: %L})
                RETURN cause.memory_id, type(relationships(path)[-1]), length(path)
            $q$) as (cause_id_raw ag_catalog.agtype, rel_raw ag_catalog.agtype, dist_raw ag_catalog.agtype)
        )
        SELECT
            h.cause_uuid as cause_id,
            m.content as cause_content,
            h.rel_type as relationship,
            h.dist as distance
        FROM hits h
        JOIN memories m ON m.id = h.cause_uuid
        ORDER BY h.dist ASC
    $sql$, GREATEST(1, LEAST(10, COALESCE(p_depth, 3))), p_memory_id);

    BEGIN
        RETURN QUERY EXECUTE sql;
    EXCEPTION
        WHEN OTHERS THEN
            RETURN;
    END;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION find_connected_concepts(p_memory_id UUID, p_hops INT DEFAULT 2)
RETURNS TABLE (
    concept_name TEXT,
    path_length INT
) AS $$
DECLARE
    sql TEXT;
BEGIN
    sql := format($sql$
        SELECT
            replace(name_raw::text, '"', '')::text as concept_name,
            1 as path_length
        FROM ag_catalog.cypher('memory_graph', $q$
            MATCH (m:MemoryNode {memory_id: %L})-[r:INSTANCE_OF]->(c:ConceptNode)
            RETURN c.name, r.strength
            ORDER BY r.strength DESC
        $q$) as (name_raw ag_catalog.agtype, strength_raw ag_catalog.agtype)
    $sql$, p_memory_id);

    BEGIN
        RETURN QUERY EXECUTE sql;
    EXCEPTION
        WHEN OTHERS THEN
            RETURN;
    END;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION find_memories_by_concept(
    p_concept_name TEXT,
    p_limit INT DEFAULT 10
)
RETURNS TABLE (
    memory_id UUID,
    memory_content TEXT,
    memory_importance FLOAT,
    memory_type memory_type,
    memory_created_at TIMESTAMPTZ,
    emotional_valence FLOAT,
    link_strength FLOAT
) AS $$
DECLARE
    sql TEXT;
BEGIN
    sql := format($sql$
        WITH hits AS (
            SELECT
                replace(mid_raw::text, '"', '')::uuid as mem_uuid,
                COALESCE((strength_raw::text)::float, 1.0) as strength
            FROM ag_catalog.cypher('memory_graph', $q$
                MATCH (m:MemoryNode)-[r:INSTANCE_OF]->(c:ConceptNode {name: %L})
                RETURN m.memory_id, r.strength
                ORDER BY r.strength DESC
            $q$) as (mid_raw ag_catalog.agtype, strength_raw ag_catalog.agtype)
            LIMIT %s
        )
        SELECT
            m.id as memory_id,
            m.content as memory_content,
            m.importance as memory_importance,
            m.type as memory_type,
            m.created_at as memory_created_at,
            (m.metadata->>'emotional_valence')::float as emotional_valence,
            h.strength as link_strength
        FROM hits h
        JOIN memories m ON m.id = h.mem_uuid
        WHERE m.status = 'active'
        ORDER BY h.strength DESC, m.importance DESC
    $sql$, p_concept_name, p_limit);

    BEGIN
        RETURN QUERY EXECUTE sql;
    EXCEPTION
        WHEN OTHERS THEN
            RETURN;
    END;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION find_supporting_evidence(p_worldview_id UUID)
RETURNS TABLE (
    memory_id UUID,
    content TEXT,
    strength FLOAT
) AS $$
DECLARE
    sql TEXT;
BEGIN
    sql := format($sql$
        WITH hits AS (
            SELECT
                replace(mem_raw::text, '"', '')::uuid as mem_uuid,
                (strength_raw::text)::float as strength
            FROM ag_catalog.cypher('memory_graph', $q$
                MATCH (m:MemoryNode)-[r:SUPPORTS]->(w:MemoryNode {memory_id: %L})
                WHERE w.type = 'worldview'
                RETURN m.memory_id, r.strength
            $q$) as (mem_raw ag_catalog.agtype, strength_raw ag_catalog.agtype)
        )
        SELECT
            h.mem_uuid as memory_id,
            m.content as content,
            h.strength as strength
        FROM hits h
        JOIN memories m ON m.id = h.mem_uuid
        ORDER BY h.strength DESC
    $sql$, p_worldview_id);

    BEGIN
        RETURN QUERY EXECUTE sql;
    EXCEPTION
        WHEN OTHERS THEN
            RETURN;
    END;
END;
$$ LANGUAGE plpgsql;
-- ============================================================================
-- REFLECT PIPELINE
-- ============================================================================
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
                    ident_embedding := get_embedding(change_text);
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
-- ============================================================================
-- SUBCONSCIOUS OBSERVATIONS
-- ============================================================================
CREATE OR REPLACE FUNCTION apply_subconscious_observations(p_payload JSONB)
RETURNS JSONB AS $$
DECLARE
    obs JSONB;
    obs_type TEXT;
    conf FLOAT;
    evidence_raw JSONB;
    evidence_ids UUID[];
    evidence_id UUID;
    summary TEXT;
    suggested_name TEXT;
    rel_entity TEXT;
    rel_strength FLOAT;
    rel_change TEXT;
    rel_magnitude FLOAT;
    contra_a UUID;
    contra_b UUID;
    tension TEXT;
    worldview_id UUID;
    pattern TEXT;
    freq INT;
    unprocessed BOOLEAN;
    concept TEXT;
    cluster_id UUID;
    rationale TEXT;
    i INT;
    min_conf FLOAT := 0.6;
    person_belief_id UUID;
    person_belief_conf FLOAT;
    person_belief_content TEXT;
    applied_narrative INT := 0;
    applied_relationships INT := 0;
    applied_contradictions INT := 0;
    applied_emotional INT := 0;
    applied_consolidation INT := 0;
    emotional_items JSONB;
    consolidation_items JSONB;
BEGIN
    IF p_payload IS NULL OR jsonb_typeof(p_payload) <> 'object' THEN
        RETURN jsonb_build_object('applied', false, 'reason', 'empty');
    END IF;
    FOR obs IN SELECT * FROM jsonb_array_elements(COALESCE(p_payload->'narrative_observations', '[]'::jsonb))
    LOOP
        obs_type := lower(COALESCE(obs->>'type', obs->>'kind', ''));
        conf := NULLIF(obs->>'confidence', '')::float;
        IF conf IS NULL OR conf >= min_conf THEN
            evidence_raw := COALESCE(obs->'evidence', obs->'evidence_ids', obs->'memory_ids', '[]'::jsonb);
            BEGIN
                SELECT COALESCE(ARRAY(
                    SELECT value::uuid
                    FROM jsonb_array_elements_text(evidence_raw) val(value)
                    WHERE value ~* '^[0-9a-f-]{36}$'
                ), ARRAY[]::uuid[]) INTO evidence_ids;
            EXCEPTION
                WHEN OTHERS THEN
                    evidence_ids := ARRAY[]::uuid[];
            END;

            summary := COALESCE(obs->>'summary', obs->>'rationale', obs->>'evidence_summary', '');
            IF obs_type IN ('chapter_transition', 'chapter_start', 'chapter_begin', 'begin_chapter') THEN
                suggested_name := COALESCE(obs->>'suggested_name', obs->>'name', obs->>'chapter_name', 'Foundations');
                PERFORM ensure_current_life_chapter(suggested_name);
                PERFORM create_strategic_memory(
                    COALESCE(NULLIF(summary, ''), format('Chapter transition: %s', suggested_name)),
                    'Narrative transition',
                    COALESCE(conf, 0.8),
                    jsonb_build_object(
                        'kind', 'narrative',
                        'type', obs_type,
                        'evidence_memory_ids', evidence_ids,
                        'confidence', conf
                    ),
                    NULL,
                    0.6
                );
                applied_narrative := applied_narrative + 1;
            ELSIF obs_type IN ('turning_point', 'turning-point', 'turningpoint') THEN
                evidence_id := NULL;
                BEGIN
                    evidence_id := NULLIF(obs->>'memory_id', '')::uuid;
                EXCEPTION
                    WHEN OTHERS THEN
                        evidence_id := NULL;
                END;
                IF evidence_id IS NULL THEN
                    BEGIN
                        evidence_id := NULLIF(obs->>'evidence_memory_id', '')::uuid;
                    EXCEPTION
                        WHEN OTHERS THEN
                            evidence_id := NULL;
                    END;
                END IF;
                IF evidence_id IS NULL AND array_length(evidence_ids, 1) > 0 THEN
                    evidence_id := evidence_ids[1];
                END IF;

                IF evidence_id IS NOT NULL THEN
                    UPDATE memories
                    SET importance = GREATEST(importance, 0.9),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = evidence_id;
                END IF;

                PERFORM create_strategic_memory(
                    COALESCE(NULLIF(summary, ''), 'Turning point detected'),
                    'Narrative turning point',
                    COALESCE(conf, 0.85),
                    jsonb_build_object(
                        'kind', 'narrative',
                        'type', 'turning_point',
                        'memory_id', evidence_id,
                        'evidence_memory_ids', evidence_ids,
                        'confidence', conf
                    ),
                    NULL,
                    0.6
                );
                applied_narrative := applied_narrative + 1;
            ELSIF obs_type IN ('theme_emergence', 'theme', 'theme_emergent') THEN
                PERFORM create_strategic_memory(
                    COALESCE(
                        NULLIF(summary, ''),
                        COALESCE(obs->>'theme', obs->>'pattern', 'Theme emerging')
                    ),
                    'Theme emergence',
                    COALESCE(conf, 0.7),
                    jsonb_build_object(
                        'kind', 'narrative',
                        'type', obs_type,
                        'evidence_memory_ids', evidence_ids,
                        'confidence', conf
                    ),
                    NULL,
                    0.5
                );
                applied_narrative := applied_narrative + 1;
            END IF;
        END IF;
    END LOOP;
    FOR obs IN SELECT * FROM jsonb_array_elements(COALESCE(p_payload->'relationship_observations', '[]'::jsonb))
    LOOP
        rel_entity := btrim(COALESCE(obs->>'entity', obs->>'name', ''));
        IF rel_entity = '' THEN
            CONTINUE;
        END IF;

        conf := NULLIF(obs->>'confidence', '')::float;
        IF conf IS NOT NULL AND conf < min_conf THEN
            CONTINUE;
        END IF;

        rel_change := lower(COALESCE(obs->>'change_type', obs->>'type', ''));
        rel_magnitude := NULLIF(obs->>'magnitude', '')::float;
        rel_strength := NULLIF(obs->>'strength', '')::float;
        IF rel_strength IS NULL THEN
            rel_strength := NULLIF(obs->>'new_strength', '')::float;
        END IF;
        IF rel_strength IS NULL THEN
            IF rel_magnitude IS NOT NULL THEN
                IF rel_change LIKE '%decrease%' OR rel_change LIKE '%lower%' THEN
                    rel_strength := 0.6 - rel_magnitude;
                ELSIF rel_change LIKE '%increase%' OR rel_change LIKE '%higher%' THEN
                    rel_strength := 0.6 + rel_magnitude;
                ELSE
                    rel_strength := 0.6 + rel_magnitude * 0.5;
                END IF;
            ELSE
                rel_strength := 0.6;
            END IF;
        END IF;
        rel_strength := LEAST(1.0, GREATEST(0.0, rel_strength));

        evidence_raw := COALESCE(obs->'evidence', obs->'evidence_ids', obs->'memory_ids', '[]'::jsonb);
        BEGIN
            SELECT COALESCE(ARRAY(
                SELECT value::uuid
                FROM jsonb_array_elements_text(evidence_raw) val(value)
                WHERE value ~* '^[0-9a-f-]{36}$'
            ), ARRAY[]::uuid[]) INTO evidence_ids;
        EXCEPTION
            WHEN OTHERS THEN
                evidence_ids := ARRAY[]::uuid[];
        END;
        evidence_id := NULL;
        BEGIN
            evidence_id := NULLIF(obs->>'evidence_memory_id', '')::uuid;
        EXCEPTION
            WHEN OTHERS THEN
                evidence_id := NULL;
        END;
        IF evidence_id IS NULL AND array_length(evidence_ids, 1) > 0 THEN
            evidence_id := evidence_ids[1];
        END IF;

        PERFORM upsert_self_concept_edge('relationship', rel_entity, rel_strength, evidence_id);

        IF array_length(evidence_ids, 1) > 0 THEN
            FOR i IN 1..LEAST(array_length(evidence_ids, 1), 3) LOOP
                PERFORM link_memory_to_concept(evidence_ids[i], rel_entity, 0.6);
            END LOOP;
        END IF;

        person_belief_id := NULL;
        person_belief_conf := NULL;
        BEGIN
            SELECT id, (metadata->>'confidence')::float
            INTO person_belief_id, person_belief_conf
            FROM memories
            WHERE type = 'worldview'
              AND status = 'active'
              AND metadata->>'category' = 'other'
              AND content ILIKE ('%' || rel_entity || '%')
            ORDER BY importance DESC, updated_at DESC
            LIMIT 1;
        EXCEPTION
            WHEN OTHERS THEN
                person_belief_id := NULL;
        END;

        IF person_belief_id IS NOT NULL THEN
            UPDATE memories
            SET metadata = jsonb_set(
                    metadata,
                    '{confidence}',
                    to_jsonb(LEAST(1.0, GREATEST(0.0, COALESCE(rel_strength, person_belief_conf, 0.6))))
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = person_belief_id;
        ELSE
            person_belief_content := COALESCE(
                NULLIF(obs->>'belief', ''),
                NULLIF(obs->>'summary', ''),
                format('I have a relationship with %s.', rel_entity)
            );
            PERFORM create_worldview_memory(
                person_belief_content,
                'other',
                COALESCE(rel_strength, 0.6),
                0.6,
                0.6,
                'observed'
            );
        END IF;

        summary := COALESCE(obs->>'summary', format('Relationship update: %s', rel_entity));
        PERFORM create_strategic_memory(
            summary,
            'Relationship change',
            COALESCE(conf, 0.7),
            jsonb_build_object(
                'kind', 'relationship_change',
                'change_type', rel_change,
                'entity', rel_entity,
                'evidence_memory_ids', evidence_ids,
                'confidence', conf
            ),
            NULL,
            0.5
        );
        applied_relationships := applied_relationships + 1;
    END LOOP;
    FOR obs IN SELECT * FROM jsonb_array_elements(COALESCE(p_payload->'contradiction_observations', '[]'::jsonb))
    LOOP
        conf := NULLIF(obs->>'confidence', '')::float;
        IF conf IS NOT NULL AND conf < min_conf THEN
            CONTINUE;
        END IF;

        contra_a := NULL;
        contra_b := NULL;
        BEGIN
            contra_a := NULLIF(obs->>'memory_a', '')::uuid;
        EXCEPTION
            WHEN OTHERS THEN
                contra_a := NULL;
        END;
        BEGIN
            contra_b := NULLIF(obs->>'memory_b', '')::uuid;
        EXCEPTION
            WHEN OTHERS THEN
                contra_b := NULL;
        END;
        IF contra_a IS NULL THEN
            BEGIN
                contra_a := NULLIF(obs->>'belief_a_id', '')::uuid;
            EXCEPTION
                WHEN OTHERS THEN
                    contra_a := NULL;
            END;
        END IF;
        IF contra_b IS NULL THEN
            BEGIN
                contra_b := NULLIF(obs->>'belief_b_id', '')::uuid;
            EXCEPTION
                WHEN OTHERS THEN
                    contra_b := NULL;
            END;
        END IF;

        worldview_id := NULL;
        BEGIN
            worldview_id := NULLIF(obs->>'worldview_id', '')::uuid;
        EXCEPTION
            WHEN OTHERS THEN
                worldview_id := NULL;
        END;
        IF worldview_id IS NULL THEN
            BEGIN
                worldview_id := NULLIF(obs->>'belief_id', '')::uuid;
            EXCEPTION
                WHEN OTHERS THEN
                    worldview_id := NULL;
            END;
        END IF;

        tension := COALESCE(obs->>'tension', obs->>'summary', '');
        IF contra_a IS NOT NULL AND contra_b IS NOT NULL THEN
            BEGIN
                PERFORM create_memory_relationship(
                    contra_a,
                    contra_b,
                    'CONTRADICTS',
                    jsonb_build_object('source', 'subconscious', 'tension', tension)
                );
            EXCEPTION
                WHEN OTHERS THEN
                    NULL;
            END;
        END IF;

        PERFORM create_strategic_memory(
            COALESCE(NULLIF(tension, ''), 'Contradiction detected'),
            'Unresolved tension between beliefs',
            COALESCE(conf, 0.7),
            jsonb_build_object(
                'kind', 'contradiction',
                'memory_a', contra_a,
                'memory_b', contra_b,
                'tension', tension,
                'evidence', COALESCE(obs->'evidence', '[]'::jsonb)
            ),
            NULL,
            0.5
        );

        IF worldview_id IS NOT NULL THEN
            UPDATE memories
            SET metadata = jsonb_set(
                    metadata,
                    '{stability}',
                    to_jsonb(LEAST(1.0, GREATEST(0.0, COALESCE((metadata->>'stability')::float, 0.7) * 0.8)))
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = worldview_id AND type = 'worldview';
        END IF;

        UPDATE drives SET current_level = LEAST(1.0, current_level + 0.15) WHERE name = 'coherence';
        applied_contradictions := applied_contradictions + 1;
    END LOOP;
    emotional_items := COALESCE(p_payload->'emotional_observations', p_payload->'emotional_patterns', '[]'::jsonb);
    FOR obs IN SELECT * FROM jsonb_array_elements(emotional_items)
    LOOP
        conf := NULLIF(obs->>'confidence', '')::float;
        IF conf IS NOT NULL AND conf < min_conf THEN
            CONTINUE;
        END IF;

        pattern := btrim(COALESCE(obs->>'pattern', obs->>'summary', obs->>'theme', ''));
        IF pattern = '' THEN
            CONTINUE;
        END IF;
        freq := NULLIF(obs->>'frequency', '')::int;
        unprocessed := NULLIF(obs->>'unprocessed', '')::boolean;

        PERFORM create_strategic_memory(
            format('Emotional pattern: %s', pattern),
            'Emotional pattern',
            COALESCE(conf, 0.7),
            jsonb_build_object(
                'kind', 'emotional_pattern',
                'pattern', pattern,
                'frequency', COALESCE(freq, 0),
                'unprocessed', COALESCE(unprocessed, FALSE),
                'evidence', COALESCE(obs->'evidence', '[]'::jsonb)
            ),
            NULL,
            0.5
        );
        UPDATE drives SET current_level = LEAST(1.0, current_level + 0.1) WHERE name = 'coherence';
        applied_emotional := applied_emotional + 1;
    END LOOP;
    consolidation_items := COALESCE(p_payload->'consolidation_observations', p_payload->'consolidation_suggestions', '[]'::jsonb);
    FOR obs IN SELECT * FROM jsonb_array_elements(consolidation_items)
    LOOP
        evidence_raw := COALESCE(obs->'memory_ids', obs->'memories', '[]'::jsonb);
        BEGIN
            SELECT COALESCE(ARRAY(
                SELECT value::uuid
                FROM jsonb_array_elements_text(evidence_raw) val(value)
                WHERE value ~* '^[0-9a-f-]{36}$'
            ), ARRAY[]::uuid[]) INTO evidence_ids;
        EXCEPTION
            WHEN OTHERS THEN
                evidence_ids := ARRAY[]::uuid[];
        END;
        IF array_length(evidence_ids, 1) IS NULL OR array_length(evidence_ids, 1) < 2 THEN
            CONTINUE;
        END IF;

        conf := NULLIF(obs->>'confidence', '')::float;
        IF conf IS NOT NULL AND conf < min_conf THEN
            CONTINUE;
        END IF;

        concept := btrim(COALESCE(obs->>'concept', obs->>'suggested_concept', ''));
        rationale := COALESCE(obs->>'rationale', obs->>'summary', '');

        cluster_id := NULL;
        BEGIN
            cluster_id := NULLIF(obs->>'cluster_id', '')::uuid;
        EXCEPTION
            WHEN OTHERS THEN
                cluster_id := NULL;
        END;
        IF cluster_id IS NULL THEN
            BEGIN
                cluster_id := NULLIF(obs->>'suggested_cluster_id', '')::uuid;
            EXCEPTION
                WHEN OTHERS THEN
                    cluster_id := NULL;
            END;
        END IF;

        FOR i IN 1..LEAST(array_length(evidence_ids, 1) - 1, 8) LOOP
            BEGIN
                PERFORM create_memory_relationship(
                    evidence_ids[i],
                    evidence_ids[i + 1],
                    'ASSOCIATED',
                    jsonb_build_object('source', 'subconscious', 'rationale', rationale)
                );
            EXCEPTION
                WHEN OTHERS THEN
                    NULL;
            END;
        END LOOP;

        IF concept <> '' THEN
            FOR i IN 1..LEAST(array_length(evidence_ids, 1), 5) LOOP
                PERFORM link_memory_to_concept(evidence_ids[i], concept, 0.7);
            END LOOP;
        END IF;

        IF cluster_id IS NOT NULL THEN
            FOR i IN 1..LEAST(array_length(evidence_ids, 1), 5) LOOP
                PERFORM link_memory_to_cluster_graph(evidence_ids[i], cluster_id, COALESCE(conf, 0.6));
            END LOOP;
        END IF;

        IF rationale <> '' THEN
            PERFORM create_strategic_memory(
                format('Consolidation suggested: %s', rationale),
                'Consolidation opportunity',
                COALESCE(conf, 0.6),
                jsonb_build_object(
                    'kind', 'consolidation',
                    'memory_ids', evidence_ids,
                    'concept', NULLIF(concept, ''),
                    'cluster_id', cluster_id,
                    'confidence', conf
                ),
                NULL,
                0.5
            );
        END IF;
        applied_consolidation := applied_consolidation + 1;
    END LOOP;

    RETURN jsonb_build_object(
        'narrative', applied_narrative,
        'relationships', applied_relationships,
        'contradictions', applied_contradictions,
        'emotional', applied_emotional,
        'consolidation', applied_consolidation
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION apply_brainstormed_goals(
    p_heartbeat_id UUID,
    p_goals JSONB
)
RETURNS JSONB AS $$
DECLARE
    goal JSONB;
    title TEXT;
    description TEXT;
    source goal_source;
    priority goal_priority;
    parent_id UUID;
    due_at TIMESTAMPTZ;
    created_id UUID;
    created_ids UUID[] := ARRAY[]::UUID[];
BEGIN
    IF p_goals IS NULL OR jsonb_typeof(p_goals) <> 'array' THEN
        RETURN jsonb_build_object('created_goal_ids', created_ids);
    END IF;

    FOR goal IN SELECT * FROM jsonb_array_elements(p_goals)
    LOOP
        title := btrim(COALESCE(goal->>'title', ''));
        IF title = '' THEN
            CONTINUE;
        END IF;
        description := NULLIF(goal->>'description', '');

        BEGIN
            source := COALESCE(NULLIF(goal->>'source', ''), 'curiosity')::goal_source;
        EXCEPTION
            WHEN OTHERS THEN
                source := 'curiosity'::goal_source;
        END;
        BEGIN
            priority := COALESCE(NULLIF(goal->>'priority', ''), 'queued')::goal_priority;
        EXCEPTION
            WHEN OTHERS THEN
                priority := 'queued'::goal_priority;
        END;

        parent_id := NULL;
        BEGIN
            parent_id := NULLIF(goal->>'parent_goal_id', '')::uuid;
        EXCEPTION
            WHEN OTHERS THEN
                parent_id := NULL;
        END;
        IF parent_id IS NULL THEN
            BEGIN
                parent_id := NULLIF(goal->>'parent_id', '')::uuid;
            EXCEPTION
                WHEN OTHERS THEN
                    parent_id := NULL;
            END;
        END IF;

        due_at := NULL;
        BEGIN
            due_at := NULLIF(goal->>'due_at', '')::timestamptz;
        EXCEPTION
            WHEN OTHERS THEN
                due_at := NULL;
        END;

        created_id := create_goal(
            title,
            description,
            source,
            priority,
            parent_id,
            due_at
        );
        created_ids := array_append(created_ids, created_id);
    END LOOP;

    RETURN jsonb_build_object('created_goal_ids', created_ids);
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION apply_inquiry_result(
    p_heartbeat_id UUID,
    p_payload JSONB
)
RETURNS JSONB AS $$
DECLARE
    result JSONB;
    summary TEXT;
    confidence FLOAT;
    depth TEXT;
    query TEXT;
    sources JSONB;
    metadata JSONB;
    mem_id UUID;
BEGIN
    IF p_payload IS NULL OR jsonb_typeof(p_payload) <> 'object' THEN
        RETURN jsonb_build_object('memory_id', NULL, 'error', 'invalid_payload');
    END IF;

    IF p_payload ? 'result' THEN
        result := p_payload->'result';
    ELSE
        result := p_payload;
    END IF;

    IF result IS NULL OR jsonb_typeof(result) <> 'object' THEN
        RETURN jsonb_build_object('memory_id', NULL, 'error', 'invalid_result');
    END IF;

    summary := btrim(COALESCE(result->>'summary', ''));
    IF summary = '' THEN
        RETURN jsonb_build_object('memory_id', NULL, 'skipped', 'missing_summary');
    END IF;

    BEGIN
        confidence := COALESCE(NULLIF(result->>'confidence', '')::float, 0.6);
    EXCEPTION
        WHEN OTHERS THEN
            confidence := 0.6;
    END;

    depth := COALESCE(NULLIF(p_payload->>'depth', ''), NULLIF(result->>'depth', ''), 'inquire_shallow');
    query := COALESCE(NULLIF(p_payload->>'query', ''), NULLIF(result->>'query', ''));

    sources := COALESCE(result->'sources', '[]'::jsonb);
    IF jsonb_typeof(sources) <> 'array' THEN
        sources := '[]'::jsonb;
    END IF;

    metadata := jsonb_build_object(
        'sources', sources,
        'query', query,
        'depth', depth,
        'heartbeat_id', CASE
            WHEN p_heartbeat_id IS NULL THEN NULL
            ELSE p_heartbeat_id::text
        END
    );

    mem_id := create_semantic_memory(
        summary,
        confidence,
        ARRAY['inquiry', depth],
        NULL,
        metadata,
        0.6
    );

    RETURN jsonb_build_object('memory_id', mem_id);
EXCEPTION
    WHEN OTHERS THEN
        RETURN jsonb_build_object('memory_id', NULL, 'error', SQLERRM);
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION apply_goal_changes(p_changes JSONB)
RETURNS JSONB AS $$
DECLARE
    change JSONB;
    goal_id UUID;
    change_kind goal_priority;
    reason TEXT;
    applied INT := 0;
BEGIN
    IF p_changes IS NULL OR jsonb_typeof(p_changes) <> 'array' THEN
        RETURN jsonb_build_object('applied', 0);
    END IF;

    FOR change IN SELECT * FROM jsonb_array_elements(p_changes)
    LOOP
        BEGIN
            goal_id := NULLIF(change->>'goal_id', '')::uuid;
        EXCEPTION
            WHEN OTHERS THEN
                goal_id := NULL;
        END;
        IF goal_id IS NULL THEN
            CONTINUE;
        END IF;

        BEGIN
            change_kind := NULLIF(change->>'change', '')::goal_priority;
        EXCEPTION
            WHEN OTHERS THEN
                CONTINUE;
        END;

        reason := COALESCE(change->>'reason', '');
        PERFORM change_goal_priority(goal_id, change_kind, reason);
        applied := applied + 1;
    END LOOP;

    RETURN jsonb_build_object('applied', applied);
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION apply_external_call_result(
    p_call JSONB,
    p_output JSONB
)
RETURNS JSONB AS $$
DECLARE
    call_type TEXT;
    call_input JSONB;
    heartbeat_id UUID;
    kind TEXT;
    output_payload JSONB := COALESCE(p_output, '{}'::jsonb);
    applied JSONB;
    outbox_messages JSONB := '[]'::jsonb;
BEGIN
    call_type := COALESCE(p_call->>'call_type', '');
    call_input := COALESCE(p_call->'input', '{}'::jsonb);
    BEGIN
        heartbeat_id := NULLIF(call_input->>'heartbeat_id', '')::uuid;
    EXCEPTION
        WHEN OTHERS THEN
            heartbeat_id := NULL;
    END;

    IF call_type = '' THEN
        RETURN jsonb_build_object('error', 'call_type_missing');
    END IF;

    IF call_type = 'think' THEN
        kind := lower(COALESCE(call_input->>'kind', output_payload->>'kind', ''));
        IF kind = '' THEN
            kind := 'heartbeat_decision';
        END IF;

        IF kind = 'brainstorm_goals' AND heartbeat_id IS NOT NULL THEN
            BEGIN
                PERFORM apply_brainstormed_goals(heartbeat_id, output_payload->'goals');
            EXCEPTION
                WHEN OTHERS THEN
                    NULL;
            END;
        ELSIF kind = 'inquire' AND heartbeat_id IS NOT NULL THEN
            BEGIN
                PERFORM apply_inquiry_result(heartbeat_id, output_payload);
            EXCEPTION
                WHEN OTHERS THEN
                    NULL;
            END;
        ELSIF kind = 'reflect' AND heartbeat_id IS NOT NULL THEN
            IF output_payload ? 'result' THEN
                BEGIN
                    PERFORM process_reflection_result(heartbeat_id, output_payload->'result');
                EXCEPTION
                    WHEN OTHERS THEN
                        NULL;
                END;
            END IF;
        ELSIF kind = 'termination_confirm' THEN
            applied := apply_termination_confirmation(call_input, output_payload);
            IF applied IS NOT NULL AND jsonb_typeof(applied) = 'object' THEN
                output_payload := jsonb_set(output_payload, '{termination}', applied, true);
                IF COALESCE((applied->>'terminated')::boolean, FALSE) THEN
                    output_payload := jsonb_set(output_payload, '{terminated}', 'true'::jsonb, true);
                END IF;
                IF jsonb_typeof(applied->'result') = 'object' THEN
                    outbox_messages := COALESCE(applied->'result'->'outbox_messages', '[]'::jsonb);
                END IF;
            END IF;
        ELSIF kind = 'consent_request' THEN
            applied := init_consent(output_payload);
            IF applied IS NOT NULL AND jsonb_typeof(applied) = 'object' THEN
                output_payload := jsonb_set(output_payload, '{init_consent}', applied, true);
                IF applied ? 'decision' THEN
                    output_payload := jsonb_set(output_payload, '{decision}', to_jsonb(applied->>'decision'), true);
                END IF;
            END IF;
        END IF;
    END IF;

    IF outbox_messages IS NOT NULL
        AND jsonb_typeof(outbox_messages) = 'array'
        AND jsonb_array_length(outbox_messages) > 0 THEN
        output_payload := jsonb_set(output_payload, '{outbox_messages}', outbox_messages, true);
    END IF;

    RETURN output_payload;
EXCEPTION
    WHEN OTHERS THEN
        RETURN jsonb_build_object('error', SQLERRM);
END;
$$ LANGUAGE plpgsql;
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
    contra_a UUID;
    contra_b UUID;
    resolution_text TEXT;
    identity_updated BOOLEAN;
    pause_reason TEXT;
BEGIN
    BEGIN
        action_kind := p_action::heartbeat_action;
    EXCEPTION
        WHEN invalid_text_representation THEN
            RETURN jsonb_build_object('success', false, 'error', 'Unknown action: ' || COALESCE(p_action, '<null>'));
    END;

    action_cost := get_action_cost(p_action);
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
                    'heartbeat_id', p_heartbeat_id
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
                    'boundaries', boundary_hits
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
CREATE OR REPLACE FUNCTION execute_heartbeat_actions_batch(
    p_heartbeat_id UUID,
    p_actions JSONB,
    p_start_index INT DEFAULT 0
)
RETURNS JSONB AS $$
DECLARE
    action_spec JSONB;
    action_name TEXT;
    action_params JSONB;
    action_result JSONB;
    actions_taken JSONB := '[]'::jsonb;
    outbox_messages JSONB := '[]'::jsonb;
    pending_external JSONB;
    action_external JSONB;
    next_index INT := COALESCE(p_start_index, 0);
    ord INT;
    halt_reason TEXT;
BEGIN
    IF p_actions IS NULL OR jsonb_typeof(p_actions) <> 'array' THEN
        RETURN jsonb_build_object(
            'actions_taken', '[]'::jsonb,
            'next_index', next_index,
            'outbox_messages', outbox_messages,
            'halt_reason', 'invalid_actions'
        );
    END IF;

    FOR action_spec, ord IN
        SELECT value, ordinality
        FROM jsonb_array_elements(p_actions) WITH ORDINALITY
        WHERE ordinality > COALESCE(p_start_index, 0)
        ORDER BY ordinality
    LOOP
        action_name := COALESCE(action_spec->>'action', 'rest');
        action_params := COALESCE(action_spec->'params', '{}'::jsonb);

        action_result := execute_heartbeat_action(p_heartbeat_id, action_name, action_params);
        actions_taken := actions_taken || jsonb_build_array(jsonb_build_object(
            'action', action_name,
            'params', action_params,
            'result', action_result
        ));
        next_index := ord;
        outbox_messages := outbox_messages || COALESCE(action_result->'outbox_messages', '[]'::jsonb);

        IF COALESCE((action_result->>'success')::boolean, FALSE) = FALSE THEN
            halt_reason := COALESCE(action_result->>'error', 'action_failed');
            RETURN jsonb_build_object(
                'actions_taken', actions_taken,
                'next_index', next_index,
                'outbox_messages', outbox_messages,
                'halt_reason', halt_reason
            );
        END IF;

        action_external := COALESCE(action_result->'external_calls', '[]'::jsonb);
        IF jsonb_typeof(action_external) = 'array' AND jsonb_array_length(action_external) > 0 THEN
            pending_external := action_external->0;
            RETURN jsonb_build_object(
                'actions_taken', actions_taken,
                'next_index', next_index,
                'pending_external_call', pending_external,
                'outbox_messages', outbox_messages,
                'halt_reason', 'external_call'
            );
        END IF;

        IF action_name = 'terminate'
            AND COALESCE((action_result#>>'{result,terminated}')::boolean, FALSE) THEN
            RETURN jsonb_build_object(
                'actions_taken', actions_taken,
                'next_index', next_index,
                'halt_reason', 'terminated'
            );
        END IF;

        IF action_name = 'pause_heartbeat'
            AND COALESCE((action_result#>>'{result,paused}')::boolean, FALSE) THEN
            RETURN jsonb_build_object(
                'actions_taken', actions_taken,
                'next_index', next_index,
                'halt_reason', 'paused'
            );
        END IF;
    END LOOP;

    RETURN jsonb_build_object(
        'actions_taken', actions_taken,
        'next_index', next_index,
        'outbox_messages', outbox_messages,
        'halt_reason', NULL
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION apply_heartbeat_decision(
    p_heartbeat_id UUID,
    p_decision JSONB,
    p_start_index INT DEFAULT 0
)
RETURNS JSONB AS $$
DECLARE
    actions JSONB;
    goal_changes JSONB;
    reasoning TEXT;
    emotional JSONB;
    batch JSONB;
    new_actions JSONB;
    existing_actions JSONB;
    next_index INT;
    pending_external JSONB;
    halt_reason TEXT;
    memory_id UUID;
    outbox_messages JSONB := '[]'::jsonb;
BEGIN
    IF p_decision IS NULL OR jsonb_typeof(p_decision) <> 'object' THEN
        RETURN jsonb_build_object('error', 'invalid_decision');
    END IF;

    actions := COALESCE(p_decision->'actions', '[]'::jsonb);
    IF jsonb_typeof(actions) <> 'array' THEN
        actions := '[]'::jsonb;
    END IF;

    goal_changes := COALESCE(p_decision->'goal_changes', '[]'::jsonb);
    IF jsonb_typeof(goal_changes) <> 'array' THEN
        goal_changes := '[]'::jsonb;
    END IF;

    reasoning := COALESCE(p_decision->>'reasoning', '');
    emotional := CASE
        WHEN jsonb_typeof(p_decision->'emotional_assessment') = 'object' THEN p_decision->'emotional_assessment'
        ELSE NULL
    END;

    batch := execute_heartbeat_actions_batch(p_heartbeat_id, actions, p_start_index);
    new_actions := COALESCE(batch->'actions_taken', '[]'::jsonb);
    IF jsonb_typeof(new_actions) <> 'array' THEN
        new_actions := '[]'::jsonb;
    END IF;

    BEGIN
        next_index := COALESCE((batch->>'next_index')::int, COALESCE(p_start_index, 0));
    EXCEPTION
        WHEN OTHERS THEN
            next_index := COALESCE(p_start_index, 0);
    END;

    BEGIN
        pending_external := batch->'pending_external_call';
    EXCEPTION
        WHEN OTHERS THEN
            pending_external := NULL;
    END;

    halt_reason := NULLIF(batch->>'halt_reason', '');
    outbox_messages := COALESCE(batch->'outbox_messages', '[]'::jsonb);

    SELECT COALESCE(active_actions, '[]'::jsonb)
    INTO existing_actions
    FROM heartbeat_state
    WHERE id = 1;

    IF existing_actions IS NULL OR jsonb_typeof(existing_actions) <> 'array' THEN
        existing_actions := '[]'::jsonb;
    END IF;

    existing_actions := existing_actions || new_actions;
    UPDATE heartbeat_state
    SET active_actions = existing_actions,
        active_reasoning = reasoning,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;

    IF pending_external IS NOT NULL AND jsonb_typeof(pending_external) = 'object' THEN
        RETURN jsonb_build_object(
            'pending_external_call', pending_external,
            'next_index', next_index,
            'actions_taken', existing_actions,
            'outbox_messages', outbox_messages,
            'completed', false,
            'halt_reason', halt_reason
        );
    END IF;

    IF halt_reason = 'terminated' THEN
        RETURN jsonb_build_object(
            'terminated', true,
            'completed', false,
            'actions_taken', existing_actions,
            'next_index', next_index,
            'outbox_messages', outbox_messages,
            'halt_reason', halt_reason
        );
    END IF;

    memory_id := finalize_heartbeat(
        p_heartbeat_id,
        reasoning,
        existing_actions,
        goal_changes,
        emotional
    );

    RETURN jsonb_build_object(
        'completed', true,
        'memory_id', memory_id,
        'actions_taken', existing_actions,
        'next_index', next_index,
        'outbox_messages', outbox_messages,
        'halt_reason', halt_reason
    );
END;
$$ LANGUAGE plpgsql;
COMMENT ON VIEW heartbeat_state IS 'Singleton view tracking current heartbeat state: energy, counts, timestamps.';

COMMENT ON FUNCTION execute_heartbeat_action IS 'Execute a single action, deducting energy and returning results.';
COMMENT ON FUNCTION gather_turn_context IS 'Gather full context for LLM decision: environment, goals, memories, identity, self_model, worldview, narrative, relationships, contradictions, emotional patterns, transformations, energy.';
-- ============================================================================
-- TIP OF TONGUE / PARTIAL ACTIVATION
-- ============================================================================
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
        query_embedding := get_embedding(p_query_text);
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
-- ============================================================================
-- VIEWS / HEALTH / WORKER GUIDANCE
-- ============================================================================
CREATE OR REPLACE VIEW cognitive_health AS
SELECT
    (SELECT current_energy FROM heartbeat_state WHERE id = 1) as energy,
    get_config_float('heartbeat.max_energy') as max_energy,
    (SELECT COUNT(*) FROM drives WHERE current_level >= urgency_threshold) as urgent_drives,
    (SELECT AVG(current_level) FROM drives) as avg_drive_level,
    (SELECT COUNT(*) FROM memories WHERE type = 'goal' AND status = 'active' AND metadata->>'priority' = 'active') as active_goals,
    (SELECT COUNT(*) FROM memories WHERE type = 'goal' AND status = 'active' AND metadata->>'priority' = 'active'
        AND metadata->'blocked_by' IS NOT NULL AND metadata->'blocked_by' <> 'null'::jsonb) as blocked_goals,
    (SELECT COUNT(*) FROM memories WHERE status = 'active') as total_memories,
    (SELECT COUNT(*) FROM memory_neighborhoods WHERE is_stale = TRUE) as stale_neighborhoods,
    (SELECT valence FROM current_emotional_state) as current_valence,
    (SELECT primary_emotion FROM current_emotional_state) as current_emotion,
    (
        SELECT COUNT(*)
        FROM memories m
        WHERE m.type = 'episodic'
          AND m.metadata#>>'{context,heartbeat_id}' IS NOT NULL
          AND COALESCE((m.metadata->>'event_time')::timestamptz, m.created_at)
                > CURRENT_TIMESTAMP - INTERVAL '24 hours'
    ) as heartbeats_24h,
    0::bigint as pending_calls,
    0::bigint as relationships_discovered_24h;

CREATE OR REPLACE VIEW worker_tasks AS
SELECT
    'heartbeat'::text AS task_type,
    CASE WHEN should_run_heartbeat() THEN 1 ELSE 0 END AS pending_count,
    'Run heartbeat if due'::text AS description
UNION ALL
SELECT
    'subconscious_maintenance'::text AS task_type,
    CASE WHEN should_run_maintenance() THEN 1 ELSE 0 END AS pending_count,
    'Run subconscious maintenance tick (consolidate + prune)'::text AS description;

SET check_function_bodies = on;
