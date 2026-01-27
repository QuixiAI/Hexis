-- Hexis schema: indexes.
CREATE INDEX IF NOT EXISTS idx_memories_source_content_hash
    ON memories ((source_attribution->>'content_hash'))
    WHERE source_attribution->>'content_hash' IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_episodes_ended_at ON episodes (ended_at);
CREATE INDEX IF NOT EXISTS idx_config_key_pattern ON config (key text_pattern_ops);
CREATE INDEX idx_memories_embedding ON memories USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_memories_status ON memories (status);
CREATE INDEX idx_memories_type ON memories (type);
CREATE INDEX idx_memories_content ON memories USING GIN (content gin_trgm_ops);
CREATE INDEX idx_memories_importance ON memories (importance DESC) WHERE status = 'active';
CREATE INDEX idx_memories_created ON memories (created_at DESC);
CREATE INDEX idx_memories_last_accessed ON memories (last_accessed DESC NULLS LAST);
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
CREATE INDEX idx_memories_goal_priority ON memories ((metadata->>'priority'))
    WHERE type = 'goal';
CREATE INDEX idx_embedding_cache_created ON embedding_cache (created_at);
CREATE INDEX idx_consent_log_model_endpoint ON consent_log (provider, model, endpoint);
CREATE UNIQUE INDEX idx_emotional_triggers_pattern ON emotional_triggers (trigger_pattern);
CREATE INDEX idx_emotional_triggers_embedding ON emotional_triggers USING hnsw (trigger_embedding vector_cosine_ops);
CREATE INDEX idx_memory_activation_embedding ON memory_activation USING hnsw (query_embedding vector_cosine_ops);
CREATE INDEX idx_memory_activation_pending ON memory_activation (background_search_pending)
    WHERE background_search_pending = TRUE;
CREATE INDEX idx_memory_activation_pending_started ON memory_activation (background_search_started_at, created_at)
    WHERE background_search_pending = TRUE;
CREATE INDEX idx_memory_activation_expires_at ON memory_activation (expires_at);
CREATE INDEX idx_scheduled_tasks_due ON scheduled_tasks (next_run_at)
    WHERE status = 'active';
CREATE INDEX idx_scheduled_tasks_status ON scheduled_tasks (status);
