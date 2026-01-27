-- Hexis schema: tables, extensions, base types, and seed data.
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
-- ============================================================================
-- DELIBERATE TRANSFORMATION
-- ============================================================================
CREATE TABLE memory_neighborhoods (
    memory_id UUID PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    neighbors JSONB NOT NULL DEFAULT '{}',
    computed_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    is_stale BOOLEAN DEFAULT TRUE
);
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
    ('embedding.service_url', to_jsonb(COALESCE(NULLIF(current_setting('app.embedding_service_url', true), ''), 'http://host.docker.internal:11434/api/embed')), 'URL of the embedding service'),
    ('embedding.model_id', to_jsonb(COALESCE(NULLIF(current_setting('app.embedding_model_id', true), ''), 'embeddinggemma:300m-qat-q4_0')), 'Embedding model id for Ollama / custom services'),
    ('embedding.dimension', to_jsonb(COALESCE(NULLIF(current_setting('app.embedding_dimension', true), ''), '768')::int), 'Embedding vector dimension'),
    ('embedding.retry_seconds', '30'::jsonb, 'Total seconds to retry embedding requests'),
    ('embedding.retry_interval_seconds', '1.0'::jsonb, 'Seconds between retry attempts')
ON CONFLICT (key) DO NOTHING;
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
-- ============================================================================
-- INDEXES
-- ============================================================================
-- Note: Use text-based indexes because timestamptz casts aren't IMMUTABLE.
-- ============================================================================
-- HELPER FUNCTIONS
-- ============================================================================
-- ============================================================================
-- TRIGGERS
-- ============================================================================




-- ============================================================================
-- CORE FUNCTIONS
-- ============================================================================
-- ============================================================================
-- PROVENANCE & TRUST
-- ============================================================================

DROP TRIGGER IF EXISTS trg_auto_worldview_alignment ON memories;
-- ============================================================================
-- GRAPH HELPER FUNCTIONS
-- ============================================================================

-- ============================================================================
-- VIEWS
-- ============================================================================




-- ============================================================================
-- HEARTBEAT SYSTEM
-- ============================================================================

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

INSERT INTO config (key, value, description) VALUES
    ('heartbeat.base_regeneration', '10'::jsonb, 'Energy regenerated per heartbeat'),
    ('heartbeat.max_energy', '20'::jsonb, 'Maximum energy cap'),
    ('heartbeat.heartbeat_interval_minutes', '60'::jsonb, 'Minutes between heartbeats'),
    ('heartbeat.max_decision_tokens', '2048'::jsonb, 'Max tokens for heartbeat decision'),
    ('heartbeat.allowed_actions', '["observe","review_goals","remember","recall","connect","reprioritize","reflect","contemplate","meditate","study","debate_internally","maintain","mark_turning_point","begin_chapter","close_chapter","acknowledge_relationship","update_trust","reflect_on_relationship","resolve_contradiction","accept_tension","brainstorm_goals","inquire_shallow","synthesize","reach_out_user","inquire_deep","reach_out_public","pause_heartbeat","terminate","rest"]'::jsonb, 'Allowed heartbeat actions'),
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
    ('agent.tools', '["recall","sense_memory_availability","request_background_search","recall_recent","recall_episode","explore_concept","explore_cluster","get_procedures","get_strategies","list_recent_episodes","create_goal","schedule_task","queue_user_message"]'::jsonb, 'Allowed tool names for agent tool use')
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
INSERT INTO config (key, value, description) VALUES
    ('tools', '{
        "enabled": null,
        "disabled": [],
        "disabled_categories": [],
        "mcp_servers": [],
        "api_keys": {},
        "costs": {},
        "context_overrides": {
            "heartbeat": {
                "max_energy_per_tool": 5,
                "disabled": ["shell", "write_file"],
                "allow_shell": false,
                "allow_file_write": false
            },
            "chat": {
                "allow_all": true,
                "allow_shell": true,
                "allow_file_write": true
            }
        },
        "workspace_path": null
    }'::jsonb, 'Tool system configuration')
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

-- ============================================================================
-- SCHEDULED TASKS (CRON-LIKE REMINDERS)
-- ============================================================================
CREATE TABLE scheduled_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    description TEXT,
    schedule_kind TEXT NOT NULL CHECK (schedule_kind IN ('once', 'interval', 'daily', 'weekly')),
    schedule JSONB NOT NULL DEFAULT '{}'::jsonb,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    action_kind TEXT NOT NULL CHECK (action_kind IN ('queue_user_message', 'create_goal')),
    action_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'disabled')),
    next_run_at TIMESTAMPTZ NOT NULL,
    last_run_at TIMESTAMPTZ,
    run_count INT NOT NULL DEFAULT 0,
    max_runs INT,
    created_by TEXT DEFAULT 'agent',
    last_error TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);













-- ============================================================================
-- GOAL FUNCTIONS
-- ============================================================================
-- ============================================================================
-- CONTEXT GATHERING FUNCTIONS
-- ============================================================================
-- ============================================================================
-- INITIALIZATION FLOW
-- ============================================================================
-- ============================================================================
-- CORE HEARTBEAT FUNCTIONS
-- ============================================================================
-- ============================================================================
-- HEARTBEAT VIEWS
-- ============================================================================



-- ============================================================================
-- BOUNDARIES
-- ============================================================================
-- Boundaries are worldview memories with metadata->>'category' = 'boundary'.
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


DROP TRIGGER IF EXISTS memories_emotional_context_insert ON memories;
-- ============================================================================
-- NEIGHBORHOOD RECOMPUTATION
-- ============================================================================
-- ============================================================================
-- GRAPH ENHANCEMENTS
-- ============================================================================
-- ============================================================================
-- REFLECT PIPELINE
-- ============================================================================
-- ============================================================================
-- SUBCONSCIOUS OBSERVATIONS
-- ============================================================================

-- ============================================================================
-- TIP OF TONGUE / PARTIAL ACTIVATION
-- ============================================================================
-- ============================================================================
-- INGESTION METRICS
-- ============================================================================

CREATE TABLE IF NOT EXISTS ingestion_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type TEXT,
    source_size_bytes BIGINT,
    word_count INT,
    mode TEXT,
    appraisal_valence FLOAT,
    appraisal_arousal FLOAT,
    appraisal_emotion TEXT,
    appraisal_intensity FLOAT,
    extraction_count INT,
    dedup_count INT,
    memory_count INT,
    llm_calls INT,
    duration_seconds FLOAT,
    errors JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_metrics_created_at ON ingestion_metrics(created_at);
CREATE INDEX IF NOT EXISTS idx_ingestion_metrics_source_type ON ingestion_metrics(source_type);

-- Function to check for archived content matching a query
CREATE OR REPLACE FUNCTION check_archived_for_query(
    p_query TEXT,
    p_threshold FLOAT DEFAULT 0.75,
    p_limit INT DEFAULT 5
)
RETURNS TABLE (
    memory_id UUID,
    content_hash TEXT,
    title TEXT,
    similarity FLOAT,
    word_count INT,
    source_path TEXT
) AS $$
DECLARE
    query_embedding vector(768);
BEGIN
    -- Get the query embedding
    query_embedding := (get_embedding(ARRAY[p_query]))[1];

    RETURN QUERY
    SELECT
        m.id AS memory_id,
        (m.source_attribution->>'content_hash')::text AS content_hash,
        (m.source_attribution->>'label')::text AS title,
        (1.0 - (m.embedding <=> query_embedding))::float AS similarity,
        (m.metadata->>'word_count')::int AS word_count,
        (m.source_attribution->>'path')::text AS source_path
    FROM memories m
    WHERE m.type = 'episodic'
      AND m.metadata->>'awaiting_processing' = 'true'
      AND (1.0 - (m.embedding <=> query_embedding)) >= p_threshold
    ORDER BY m.embedding <=> query_embedding
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql;

-- Function to mark archived content as processed
CREATE OR REPLACE FUNCTION mark_archived_as_processed(p_memory_id UUID)
RETURNS BOOLEAN AS $$
BEGIN
    UPDATE memories
    SET
        metadata = metadata - 'awaiting_processing',
        content = REPLACE(
            REPLACE(content, 'I have access to', 'I read'),
            'but haven''t engaged with it yet', 'and engaged with its contents'
        ),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_memory_id
      AND type = 'episodic'
      AND metadata->>'awaiting_processing' = 'true';

    RETURN FOUND;
END;
$$ LANGUAGE plpgsql;

-- Aggregate stats function for ingestion metrics
CREATE OR REPLACE FUNCTION get_ingestion_aggregate_stats(p_since TIMESTAMPTZ DEFAULT NULL)
RETURNS JSONB AS $$
DECLARE
    since_time TIMESTAMPTZ := COALESCE(p_since, NOW() - INTERVAL '30 days');
BEGIN
    RETURN (
        SELECT jsonb_build_object(
            'period_start', since_time,
            'period_end', NOW(),
            'total_ingestions', COUNT(*),
            'by_source_type', jsonb_object_agg(
                COALESCE(source_type, 'unknown'),
                jsonb_build_object(
                    'count', type_count,
                    'avg_words', type_avg_words,
                    'avg_extractions', type_avg_extractions,
                    'avg_duration', type_avg_duration
                )
            ),
            'by_mode', (
                SELECT jsonb_object_agg(mode, mode_count)
                FROM (
                    SELECT mode, COUNT(*) as mode_count
                    FROM ingestion_metrics
                    WHERE created_at >= since_time
                    GROUP BY mode
                ) modes
            ),
            'totals', jsonb_build_object(
                'total_words', SUM(word_count),
                'total_extractions', SUM(extraction_count),
                'total_memories', SUM(memory_count),
                'total_llm_calls', SUM(llm_calls),
                'total_duration_seconds', SUM(duration_seconds),
                'avg_valence', AVG(appraisal_valence),
                'avg_arousal', AVG(appraisal_arousal),
                'avg_intensity', AVG(appraisal_intensity)
            ),
            'dedup_stats', jsonb_build_object(
                'total_deduped', SUM(dedup_count),
                'dedup_rate', CASE WHEN SUM(extraction_count) > 0
                    THEN SUM(dedup_count)::float / SUM(extraction_count)::float
                    ELSE 0 END
            )
        )
        FROM (
            SELECT
                source_type,
                COUNT(*) as type_count,
                AVG(word_count) as type_avg_words,
                AVG(extraction_count) as type_avg_extractions,
                AVG(duration_seconds) as type_avg_duration
            FROM ingestion_metrics
            WHERE created_at >= since_time
            GROUP BY source_type
        ) type_stats,
        (
            SELECT
                word_count, extraction_count, memory_count, llm_calls,
                duration_seconds, appraisal_valence, appraisal_arousal,
                appraisal_intensity, dedup_count
            FROM ingestion_metrics
            WHERE created_at >= since_time
        ) all_stats
    );
END;
$$ LANGUAGE plpgsql STABLE;

-- ============================================================================
-- VIEWS / HEALTH / WORKER GUIDANCE
-- ============================================================================
