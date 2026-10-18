<!--
title: Workers
summary: Heartbeat and maintenance worker lifecycle management
read_when:
  - "You want to start, stop, or monitor workers"
  - "You want to understand worker architecture"
section: operations
-->

# Workers

Hexis has four independent background workers that drive autonomous behavior.

## Worker Types

| Worker | Purpose | Schedule |
|--------|---------|----------|
| **Heartbeat** (conscious) | Polls `external_calls`, triggers heartbeats | `should_run_heartbeat()` |
| **Maintenance** (subconscious) | Substrate upkeep, outbox/inbox bridging | `should_run_maintenance()` |
| **Ingest** (reading) | Reads queued documents (uploads, pasted text, imported mail and chats) into memory | Woken by `NOTIFY ingestion_jobs`; rechecks every 30 s |
| **Channel** | Bridges messaging platforms to RabbitMQ | Persistent connections |

All workers are **stateless** -- they can be killed and restarted without losing anything. All state lives in Postgres.

## Starting and Stopping

### Host user services (recommended)

Keep PostgreSQL and RabbitMQ in Docker while launchd on macOS or the systemd
user manager on Linux owns the stateless Python workers:

```bash
hexis up
hexis service install --replace-docker-workers
hexis service status
```

The migration command installs and enables the host units before stopping the
matching Docker workers, then starts the host copies. If host startup fails,
Hexis stops the attempted host copies and restores the previous Docker workers.
It refuses to proceed when it cannot determine whether Docker workers are
running. This ensures each worker has one owner.

The CLI selects the same `.env` file as the stack. You can make that choice
explicit without copying its values into service definitions:

```bash
hexis service install --env-file /path/to/.env --replace-docker-workers
```

Service lifecycle commands are:

```bash
hexis service status [--json]
hexis service start [heartbeat maintenance channels]
hexis service stop [heartbeat maintenance channels]
hexis service restart [heartbeat maintenance channels]
hexis service logs [-f] [heartbeat maintenance channels]
hexis service uninstall [--yes]
```

The channel worker remains opt-in for host ownership; add `--channels` during
installation to migrate it too. On Linux, `--enable-linger` explicitly allows
the systemd user manager to survive logout. Without it, Hexis reports the live
linger status and leaves that system setting under your control. On macOS the
units live in `~/Library/LaunchAgents`; on Linux they live in
`~/.config/systemd/user`. `hexis service uninstall` preserves worker logs.

Once installed, `hexis up`, `down`, `start`, `stop`, `reset`, and `upgrade`
coordinate host workers and omit their Docker copies. `hexis upgrade` restarts
them onto the updated Python package after migrations. `hexis uninstall`
removes managed units before removing the CLI. Source watch mode deliberately
refuses to start while host workers are active because it owns Docker workers.

### Docker Compose alternative

```bash
# Start the default background stack
docker compose up -d

# Start specific default workers
docker compose up -d heartbeat_worker maintenance_worker

# Stop workers (containers stay)
docker compose stop heartbeat_worker maintenance_worker

# Restart
docker compose restart heartbeat_worker maintenance_worker
```

### Via CLI

```bash
hexis up       # start DB, queue, heartbeat, maintenance and ingest workers
hexis start    # start workers manually if they were stopped
hexis stop     # stop workers
```

Without host units, these commands retain the Docker-only behavior. The channel
delivery relay is part of the default stack.

### Running Locally

Run workers on the host machine (connects to Postgres over TCP):

```bash
hexis worker -- --mode heartbeat
hexis worker -- --mode maintenance
hexis worker -- --mode ingest
hexis worker -- --mode both   # all three in one process

# For a specific instance
hexis worker -- --instance myagent --mode heartbeat
```

Or directly:

```bash
hexis-worker --mode heartbeat
hexis-worker --mode maintenance
hexis-worker --mode ingest
```

## Heartbeat Worker

The heartbeat worker drives the agent's conscious cognitive loop:

1. Checks `should_run_heartbeat()` on a polling interval
2. Opens a beat in Postgres, which regenerates/decays energy and gathers context
3. Runs the energy-bounded agent loop and its tool calls
4. Derives useful outcomes from durable tool receipts
5. Deducts exact spend, records the episode, and stores the urgency-adjusted next due time

### Prerequisites

The heartbeat won't run until:
- `agent.is_configured = true` (set by `hexis init`)
- `is_init_complete = true`
- Heartbeat is not paused

### Pausing from the DB

```sql
-- Pause (without stopping containers)
UPDATE heartbeat_state SET is_paused = TRUE WHERE id = 1;

-- Resume
UPDATE heartbeat_state SET is_paused = FALSE WHERE id = 1;
```

## Ingest Worker

The ingest worker reads queued documents into memory and does nothing else, so
a document never waits behind the maintenance steps. It `LISTEN`s on the
`ingestion_jobs` channel (a trigger on the job table sends `NOTIFY` for every
pending job) and keeps `ingest.job_batch_size` documents in flight until the
queue is empty, then rechecks every 30 seconds for retries coming due and jobs
left in progress by a worker that stopped (`ingest.job_claim_timeout_s`).
Several can run at once; claims use `SKIP LOCKED`.

Each document is cleaned before it is read (invisible characters, odd spaces,
blank-line runs; email also loses markup, entities and tracking links). Every
reading call declares its output schema, a truncated answer is re-asked with a
larger budget, and one section's failure never discards the others: the
sections that read are kept and receipted, and the retry re-reads only what is
missing. A model or embedding outage does not use up a document's attempts.
Every reading call waits for a live chat turn to finish first (`chat.priority_max_wait_s`).

```bash
docker compose logs ingest_worker -f
```

### Reading speed and load on the model

Imported history is read in the background with the background model. Chat
always comes first: while someone is chatting, reading waits before each model
call. Three settings control the rest:

| Setting | Default | Effect |
|---|---|---|
| `ingest.job_batch_size` | 4 | Documents read at the same time |
| `ingest.max_parallel_llm` | 4 | Model calls at the same time per document |
| `ingest.llm_reasoning_effort` | not set | Thinking for reading calls: `none`, `low`, `medium`, `high`; not set leaves the model's default |

At most `job_batch_size × max_parallel_llm` reading calls (16 by default) run at
once. Lower them for a hosted API with rate limits or a small GPU. A thinking
model reads much faster with less thinking; the effect on what it remembers
depends on the model. In one measurement (GLM-5.3-Flash reading 20 days of
chat history) the default read 3.2 documents a minute; `none` read 8.3 a minute
with the same judged coverage, and `low` found 41% fewer facts. Compare on your
own model before changing it. A server that does not accept the setting is
asked without it. Change a setting with:

```bash
docker exec hexis_brain psql -U hexis_user -d hexis_memory \
  -c "SELECT set_config('ingest.llm_reasoning_effort', '\"none\"'::jsonb)"
```

(Use your `POSTGRES_USER` and `POSTGRES_DB` if you changed them.) It applies to
documents that start reading after the change.

## Maintenance Worker

The maintenance worker handles subconscious upkeep:

- **Working memory cleanup** -- promotes or deletes expired items
- **Neighborhood recomputation** -- refreshes stale precomputed neighbors
- **Embedding cache pruning** -- cleans old cached embeddings
- **Outbox/inbox bridging** -- publishes outbox messages to RabbitMQ, ingests inbox messages
- **Conscious-episode extraction** -- sweeps recent chat turns and heartbeat episodes (`subconscious_units`) and selectively promotes salient facts into durable memories; one LLM call per batch, importance floor `extraction.min_importance`, duplicates corroborate existing beliefs. Gated by `extraction.enabled` (default on) Each turn is encoded with the previous turn of its conversation as context, a fact may name the belief it replaces (the older memory is marked no longer current), and the agent's own positions are kept in its own voice.
- **Transcript retention** -- once a day, deletes raw transcripts past their limits: per-turn traces, old web-chat and channel conversations with their message documents, and raw turns the memory system has finished with. Memories are untouched. Keys `retention.transcripts_enabled`, `retention.*_days` (0 keeps forever); the Settings page's Retention tab edits them
- **Origin-memory seeding** -- idempotently keeps the protected origin-story memories seeded (`origin_memories.enabled`, default on); a flipped flag takes effect on the next tick

Note: the workers no longer eagerly connect MCP servers at startup — MCP is
skill-gated by default (`mcp.skill_gated`) and connects on skill activation.

### Pausing

```sql
UPDATE maintenance_state SET is_paused = TRUE WHERE id = 1;
UPDATE maintenance_state SET is_paused = FALSE WHERE id = 1;
```

### Alternative Scheduling

If you don't want the maintenance worker, schedule directly:

```sql
SELECT run_subconscious_maintenance();
```

The function uses an advisory lock, so multiple schedulers won't double-run.

## Outbox and RabbitMQ

The maintenance worker bridges outbox/inbox:

- Publishes pending `outbox_messages` to `hexis.outbox` RabbitMQ queue
- Polls `hexis.inbox` and inserts messages into working memory

RabbitMQ details:
- Management UI: `http://localhost:45673`
- AMQP: `amqp://localhost:45672`
- Credentials: `hexis` / `hexis_password`

## Monitoring

```bash
hexis status                          # heartbeat number, energy, last run
hexis service status                  # launchd/systemd process state
hexis service logs -f                 # launchd/systemd worker logs
hexis logs -f                         # Docker logs
docker compose logs heartbeat_worker -f
docker compose logs maintenance_worker -f
```

## Related

- [Heartbeat guide](../guides/heartbeat.md) -- enabling autonomous behavior
- [Docker Compose](docker-compose.md) -- profiles and services
- [Troubleshooting](troubleshooting.md) -- diagnosing worker issues
