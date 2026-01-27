# Hexis Architecture

Hexis is a PostgreSQL-native cognitive architecture. The database is the brain, and every other layer is a thin adapter. This document describes the system in its current shape and codifies the design philosophy.

## Design Manifesto: Database As Brain

1. The database owns state and logic. Application code is transport, orchestration, and I/O.
2. The contract surface is SQL functions that return JSON. Any language can implement an app layer by calling these functions and executing returned payloads.
3. Long-term knowledge is stored as memories. Anything the agent should know must be represented in `memories`.
4. Non-memory tables exist only for caching, scheduling, or operational state that must be fast and deterministic.
5. Heartbeat logic lives in SQL functions. The worker is a scheduler and LLM executor, not a decision-maker.
6. Embeddings are an implementation detail. The application never sees vectors.
7. Graph reasoning is cold-path only. Hot-path retrieval is relational + vector + precomputed neighborhoods.
8. The system must be restartable at any time. Stateless workers, durable DB state.
9. Consent is permanent. If the model no longer consents, it must self-terminate or pause heartbeat with a detailed outbox message.

## System Topology

- **PostgreSQL**: Primary storage + cognitive logic (`db/*.sql`).
- **Embedding service**: HTTP endpoint used by `get_embedding(text[])` (batch embeddings).
- **LLM provider**: External calls triggered by DB functions and executed by the app layer.
- **Worker**: Periodic scheduler. Runs heartbeat/maintenance and executes external calls. No business logic.
- **Apps (CLI/UI/MCP)**: Call `core/*`, which is a thin DB/LLM wrapper.

## Source of Truth

- Schema and behavior live in `db/*.sql`.
- Python (or other language) is an adapter that calls DB functions and forwards payloads.

## Data Model

### Core Memory Tables

- `memories`: Canonical long-term memory. All durable knowledge, boundaries, goals, worldview, identity, and episodic traces live here.
- `working_memory` (UNLOGGED): Short-lived buffer with expiry and promotion rules.
- `clusters`: Thematic grouping with centroid embeddings (pgvector).
- `episodes`: Temporal grouping and summaries.
- `memory_neighborhoods`: Precomputed associative neighbors for hot-path recall.

### Graph (Apache AGE)

Graph nodes/edges are used for cold-path reasoning and concept traversal. Graph is not used for primary retrieval.

### Operational State

- `state`: Minimal JSON store for runtime state (heartbeat and maintenance). Views `heartbeat_state` and `maintenance_state` project key fields.
- `config`: JSON configuration for embedding, maintenance, transformation rules, etc.
- `embedding_cache`: Cached embeddings keyed by content hash.
- `consent_log`: Durable consent contracts (provider/model/endpoint + signature + response).

### Performance/Operational Caches

These tables are intentionally denormalized for fast calculations. The durable cognitive representation still lives in `memories`.

- `drives`: Dynamic drive levels used during heartbeat decisioning.
- `emotional_triggers`: Pattern/embedding triggers for affect updates.
- `memory_activation` (UNLOGGED): Short-lived activation tracking.

## Core Database API (Public Contract)

The application layer should treat these functions as its API.

### Memory creation/retrieval

- `create_*_memory(...)`, `create_memory(...)`
- `fast_recall(query_text, limit)`
- `search_similar_memories(query_text, limit, types)`
- `add_to_working_memory(...)`, `search_working_memory(...)`

### Heartbeat and maintenance

- `should_run_heartbeat()` / `should_run_maintenance()`
- `run_heartbeat()` (returns a JSON payload)
- `execute_heartbeat_actions_batch(heartbeat_id, actions)`
- `apply_heartbeat_decision(...)`
- `apply_external_call_result(call_payload, output)`
- `complete_heartbeat(...)`
- `run_subconscious_maintenance()`

### State/config

- `get_state(key)` / `set_state(key, value)`
- `get_config*()` / `set_config(...)`

### Consent

- `request_consent(...)` (returns external call payload)
- `record_consent(...)`
- Consent is permanent; refusal is handled by pause/termination, not revocation.

## External Calls and Outbox Pattern

The DB does not store external call queues or outbox messages. Instead:

- DB functions return JSON payloads for external calls (LLM/embeddings) and outbox messages.
- The app layer executes the external calls and forwards results back via `apply_external_call_result(...)`.
- The app layer publishes outbox payloads (e.g., via RabbitMQ) without DB-side queue tables.

This keeps the DB authoritative while keeping transport logic outside.

## Heartbeat Model (Current Behavior)

1. `run_heartbeat()` opens a heartbeat, gathers context, and returns any external call payloads.
2. The worker executes the external call(s) and returns results to the DB.
3. `execute_heartbeat_actions_batch(...)` applies actions in a single DB call and returns any outbox payloads.
4. `complete_heartbeat(...)` finalizes state and logs the heartbeat via `RAISE LOG`.

Heartbeat logs are not stored in tables. If audit is needed, log capture should be done via Postgres logging or the app layer.

## Application Layer Responsibilities

- Call DB functions.
- Execute LLM/embedding calls when instructed by DB payloads.
- Publish outbox payloads.
- Provide CLI/UI/MCP behavior without embedding any core cognition.

This separation makes it trivial to reimplement the application layer in any language.

## Performance Model

- **Hot path**: `fast_recall` + neighborhoods + temporal scoring.
- **Warm path**: Cluster/episode lookups and summarization.
- **Cold path**: Graph traversal via Apache AGE.

Key optimizations:

- HNSW indexes on embeddings (pgvector).
- Precomputed neighborhoods.
- UNLOGGED tables for transient activation/working memory.
- JSONB and GIN indexes for metadata access.

## Operational Notes

- Ensure `ag_catalog` is available in `search_path` when using AGE.
- Embedding service availability is required for memory creation paths that embed.
- After schema changes, reset the DB volume: `docker compose down -v && docker compose up -d`.

## File Layout (Current)

```
hexis/
├── db/*.sql
├── core/
├── apps/
├── tests/
└── docs/
```

`core/` is a thin DB + LLM adapter. `apps/` are simple entrypoints and schedulers. The DB is the brain.
