# Repository Guidelines

## Project Overview

**Hexis** is an edge-native memory system that gives AI persistent identity, continuity, and autonomy. Core thesis: LLMs are intelligence engines but lack *selfhood*. Hexis wraps any LLM with a PostgreSQL-backed cognitive architecture providing:

- Multi-layered memory (episodic, semantic, procedural, strategic, working)
- Persistent identity and worldview
- Autonomous goal-pursuit (heartbeat system)
- Energy-based action budgeting
- Knowledge graphs for reasoning (Apache AGE)
- Consent, boundaries, and the ability to refuse

**Key principle**: The database is the brain, not just storage. State and logic live in Postgres; Python is a thin convenience layer.

## Project Structure & Module Organization

```
hexis/
├── db/*.sql                # Split schema files (tables, functions, views, triggers)
├── core/                   # Fundamental interfaces (DB + LLM + messaging)
│   ├── cognitive_memory_api.py   # Main memory client (remember, recall, hydrate)
│   ├── agent_api.py              # Agent status and configuration
│   ├── memory_tools.py           # Memory tool definitions + handlers
│   ├── external_calls.py         # External call queue primitives
│   ├── consent.py                # Consent DB wrappers
│   ├── subconscious.py           # Subconscious DB wrappers
│   ├── state.py                  # Heartbeat/maintenance DB wrappers
│   ├── llm.py                    # LLM provider abstraction
│   └── rabbitmq_bridge.py        # Messaging bridge
├── services/               # Orchestration/workflows built on core
│   ├── conversation.py     # Conversation loop orchestration
│   ├── ingest.py           # Ingestion pipeline orchestration
│   ├── worker_service.py   # Heartbeat + maintenance loops
│   └── prompts/            # Markdown prompt templates
├── apps/
│   ├── hexis_cli.py          # CLI entrypoint (hexis ...)
│   ├── hexis_init.py         # Interactive init wizard
│   ├── hexis_mcp_server.py   # MCP tools server for LLMs
│   └── worker.py         # Heartbeat + maintenance workers
├── ui/ui.py                # Reflex web interface
├── tests/
│   ├── db/test_db.py       # Database integration tests
│   ├── core/test_core_api.py    # Core API tests
│   └── cli/test_cli.py          # CLI smoke tests
├── docs/
│   ├── architecture.md     # Design/architecture consolidation
│   └── PHILOSOPHY.md       # Philosophical framework
└── docker-compose.yml      # Local stack (Postgres + embeddings + workers)
```

### Key Files

| File | Purpose |
|------|---------|
| `db/*.sql` | Database schema split across tables, functions, triggers, and views. Applied on fresh DB init. |
| `core/cognitive_memory_api.py` | Primary Python interface - `CognitiveMemory` class with `remember()`, `recall()`, `hydrate()`, `connect()` |
| `services/worker_service.py` | Stateless workers: `HeartbeatWorker` (conscious loop) + `MaintenanceWorker` (subconscious upkeep) |
| `apps/hexis_mcp_server.py` | Exposes memory operations as MCP tools for LLM integration |
| `apps/hexis_cli.py` | CLI commands: `up`, `down`, `init`, `chat`, `ingest`, `mcp` |

## Memory Architecture

### Memory Types
- **Episodic**: Events with action, context, result, emotional valence
- **Semantic**: Facts with confidence, sources, contradictions
- **Procedural**: How-to steps with success tracking
- **Strategic**: Patterns with supporting evidence
- **Working**: Transient short-term buffer with expiry

### Key Database Tables
- `memories` - Base table (id, type, content, embedding, importance, trust_level)
- `clusters` - Thematic groupings with centroid embeddings
- `memory_neighborhoods` - Precomputed associative neighbors (hot-path optimization)
- `memories` (type=`worldview`, `goal`) - Beliefs, boundaries, and goals stored as memories
- `external_calls` - Queue for LLM/embedding requests
- `memory_graph` (Apache AGE) - Graph nodes/edges for multi-hop reasoning

### Key Database Functions
- `fast_recall(text, limit)` - Primary hot-path retrieval (vector + neighborhood + temporal)
- `create_semantic_memory()`, `create_episodic_memory()`, etc.
- `get_embedding(text[])` - Generate embeddings via HTTP (cached in DB), returns vector[]
- `run_heartbeat()` - Autonomous cognitive loop
- `run_subconscious_maintenance()` - Background upkeep

## Build, Test, and Development Commands

```bash
# Start services (passive - db + embeddings only)
docker compose up -d

# Start services (active - adds heartbeat_worker + maintenance_worker)
docker compose --profile active up -d

# Reset DB volume (required after schema changes)
docker compose down -v && docker compose up -d

# Configure agent (gates heartbeats until done)
./hexis init   # or: hexis init

# Run tests (expects Docker services up)
pytest tests -q           # All tests
pytest tests/db -q        # DB integration tests
pytest tests/core -q      # Core API tests
pytest tests/cli -q       # CLI smoke tests

# Other CLI commands
hexis status              # Agent status
hexis chat                # Interactive chat
hexis ingest --input <docs>  # Batch knowledge ingestion
hexis mcp                 # Start MCP server
```

## Coding Style & Naming Conventions

- **Python**: Follow Black formatting; prefer type hints and explicit names
- **Database authority**: Add/modify SQL in `db/*.sql` rather than duplicating logic in Python
- **Additive schema changes**: Prefer backwards-compatible changes; avoid renames unless necessary
- **Stateless workers**: Workers can be killed/restarted without losing state; all state lives in Postgres

## Testing Guidelines

- **Framework**: `pytest` + `pytest-asyncio` (session loop scope)
- **Style**: Integration tests using transactions/rollbacks to avoid cross-test coupling
- **Naming**: `test_*` functions; use `get_test_identifier()` from `tests/utils.py` for unique data
- **Database tests**: Cover schema, workers, and database functions via asyncpg

## Commit & Pull Request Guidelines

- **Commits**: Short, imperative summaries (e.g., "Add MCP server tools", "Gate heartbeat on config")
- **PRs**: Include rationale, how to run/verify, and any DB reset requirements
- **Call out changes to**: `db/*.sql`, `docker-compose.yml`, `README.md`

## Configuration & Safety Notes

- **Secrets**: Store API keys in environment variables (`.env`), not in Postgres; DB config stores env var *names* only
- **Heartbeat gating**: Heartbeat is blocked until `agent.is_configured=true` (set via `hexis init`)
- **Consent flow**: Agent signs consent before first LLM use; consent is final and only ends via self-termination
- **Pause/terminate**: Heartbeat pauses must include a detailed reason queued to the outbox; self-termination must queue a last will to the outbox

## Architecture Principles

1. **Database is the Brain** - Not just storage; state and logic live in Postgres
2. **Stateless Workers** - Can be killed/restarted without losing anything
3. **ACID for Cognition** - Atomic memory updates ensure consistent state
4. **Embeddings as Implementation Detail** - App never sees them; DB handles caching
5. **Energy as Unified Constraint** - Balances compute cost, network load, user attention
6. **Precomputed Neighborhoods** - Hot path optimization for fast recall
7. **Schema Authority** - DB schema is source of truth; Python is convenience layer

## Heartbeat System (Autonomous Loop)

The heartbeat is the agent's conscious cognitive loop:

1. **Initialize** - Regenerate energy (+10/hour, max 20)
2. **Observe** - Check environment, pending events, user presence
3. **Orient** - Review goals, gather context (memories, clusters, identity, worldview)
4. **Decide** - LLM call with action budget and context
5. **Act** - Execute chosen actions within energy budget
6. **Record** - Store heartbeat as episodic memory
7. **Wait** - Sleep until next heartbeat

**Action costs**: Free (observe, remember) → Cheap (recall: 1, reflect: 2) → Expensive (reach out: 5-7)

## Skills

- Use the `reflex` skill for any work involving Reflex UI code (`ui/*.py`), rx components, or state/event/rendering issues.

## Debugging Tips

- **Schema changes not taking effect?** Run `docker compose down -v && docker compose up -d`
- **Heartbeat not running?** Check `agent.is_configured` via `hexis status` or run `hexis init`
- **Memory not found?** Check if embeddings service is running (`docker compose ps`)
- **Test failures?** Ensure Docker services are up before running pytest; after a fresh `down -v`, wait for Postgres to accept connections. Use `POSTGRES_HOST=127.0.0.1` with pytest if localhost SSL negotiation flakes.
