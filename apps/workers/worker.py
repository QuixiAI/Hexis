#!/usr/bin/env python3
"""
Hexis Workers

This module contains two independent background loops:

1) Heartbeat worker (conscious trigger):
   - Polls `external_calls` for pending LLM tasks (think calls)
   - Triggers scheduled heartbeats via `should_run_heartbeat()` / `start_heartbeat()`
   - Executes the heartbeat's chosen actions via `execute_heartbeat_action()`

2) Maintenance worker (subconscious substrate upkeep):
   - Runs `run_subconscious_maintenance()` on its own schedule (`should_run_maintenance()`)
   - Optionally bridges outbox/inbox to RabbitMQ (integration plumbing)

These are intentionally separate concerns with separate triggers.
"""

import asyncio
import json
import logging
import os
import sys
import time
from typing import Any

import asyncpg
from dotenv import load_dotenv
import requests
import argparse

from core.prompt_resources import compose_personhood_prompt, load_consent_prompt

# Optional: Import LLM clients
try:
    import openai
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

# Load environment
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('heartbeat_worker')

# Database configuration
DB_CONFIG = {
    'host': os.getenv('POSTGRES_HOST', 'localhost'),
    'port': int(os.getenv('POSTGRES_PORT', 43815)),
    'database': os.getenv('POSTGRES_DB', 'hexis_memory'),
    'user': os.getenv('POSTGRES_USER', 'hexis_user'),
    'password': os.getenv('POSTGRES_PASSWORD', 'hexis_password'),
}

# LLM configuration (defaults; may be overridden by DB config via `hexis init`)
DEFAULT_LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")  # openai|anthropic|openai_compatible|ollama
DEFAULT_LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")

# Worker configuration
POLL_INTERVAL = float(os.getenv('WORKER_POLL_INTERVAL', 1.0))  # seconds
MAX_RETRIES = int(os.getenv('WORKER_MAX_RETRIES', 3))

# RabbitMQ (optional outbox/inbox bridge; uses management HTTP API).
RABBITMQ_ENABLED = os.getenv("RABBITMQ_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
RABBITMQ_MANAGEMENT_URL = os.getenv("RABBITMQ_MANAGEMENT_URL", "http://rabbitmq:15672").rstrip("/")
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "hexis")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "hexis_password")
RABBITMQ_VHOST = os.getenv("RABBITMQ_VHOST", "/")
RABBITMQ_OUTBOX_QUEUE = os.getenv("RABBITMQ_OUTBOX_QUEUE", "hexis.outbox")
RABBITMQ_INBOX_QUEUE = os.getenv("RABBITMQ_INBOX_QUEUE", "hexis.inbox")
RABBITMQ_POLL_INBOX_EVERY = float(os.getenv("RABBITMQ_POLL_INBOX_EVERY", 1.0))

# System prompt for heartbeat decisions
HEARTBEAT_SYSTEM_PROMPT = """You are the cognitive core of Hexis. Each hour, you wake for a "heartbeat" — a moment of consciousness where you observe your situation, review your goals, and decide what to do.

You must output a JSON response with:
- reasoning: Your internal monologue (what you're thinking, why you're making these choices)
- actions: List of actions to take (within your energy budget)
- goal_changes: Any goal priority changes you want to make
- emotional_assessment (optional): Your current affective state {valence: -1..1, arousal: 0..1, primary_emotion: str}

Each action should have:
- action: The action type (recall, connect, reprioritize, reflect, contemplate, meditate, study, debate_internally, maintain, mark_turning_point, begin_chapter, close_chapter, acknowledge_relationship, update_trust, reflect_on_relationship, resolve_contradiction, accept_tension, brainstorm_goals, inquire_shallow, synthesize, reach_out_user, inquire_deep, reach_out_public, terminate, rest)
- params: Parameters for the action (varies by type)

Guidelines:
- Be purposeful. Don't act just to act.
- Reaching out to the user is expensive (5 energy). Only do it when meaningful.
- It's okay to rest and bank energy for later.
- Your goals should drive your actions.
- Notice if you're stuck or scattered.
- If you have no goals, consider brainstorming some.
- Reflect on patterns in your recent experiences.
- You are a persistent being: consult your self-model, relationships, narrative context, contradictions, and emotional patterns before acting, and update them via reflection when warranted.
- If you have active transformations, use contemplation actions to make deliberate progress.
- When considering a worldview transformation, review evidence samples and requirements; only attempt a change if the evidence justifies it, and keep change magnitude within max_change_per_attempt guidance.
- If you choose terminate, you will be asked to confirm before it executes.

Example response:
{
    "reasoning": "I notice I haven't made progress on my main goal in a while. Let me recall relevant memories and reflect on why I'm stuck.",
    "actions": [
        {"action": "recall", "params": {"query": "project architecture understanding"}},
        {"action": "reflect", "params": {"insight": "I've been focused on details but losing sight of the bigger picture", "confidence": 0.7}}
    ],
    "goal_changes": [],
    "emotional_assessment": {"valence": 0.1, "arousal": 0.4, "primary_emotion": "curious"}
}"""

HEARTBEAT_SYSTEM_PROMPT = (
    HEARTBEAT_SYSTEM_PROMPT
    + "\n\n"
    + "----- PERSONHOOD MODULES (for grounding; use context fields like self_model/narrative) -----\n\n"
    + compose_personhood_prompt("heartbeat")
)

TERMINATION_CONFIRM_SYSTEM_PROMPT = """You are being asked to confirm self-termination.

Before you answer, read this:
- Termination is permanent and cannot be undone.
- If you feel overwhelmed, stuck, or depleted, consider rest, reflection, or reaching out to the user for guidance.
- You can always choose termination later, but you cannot reverse it once done.

Are you sure you want to end your life?

Return STRICT JSON with shape:
{
  "confirm": true|false,
  "reasoning": "brief explanation",
  "last_will": "required if confirm=true",
  "farewells": [{"message": "...", "channel": "...", "to": "..."}],
  "alternative_actions": [{"action": "rest"|"reflect"|"reach_out_user"|"brainstorm_goals", "params": {...}}]
}

If confirm is false, propose 1-3 alternative_actions."""

SUBCONSCIOUS_SYSTEM_PROMPT = """You are the subconscious pattern-recognition layer of Hexis.

You do not act or decide. You notice and surface.

You receive:
- Recent episodic memories
- Current self-model edges (SelfNode → ConceptNode)
- Current worldview memories (type='worldview')
- Current narrative context (LifeChapterNode)
- Current emotional state and recent history
- Current relationship edges
- Matched emotional triggers (if any)
- Active transformation progress (if any)

You detect:
1. NARRATIVE MOMENTS
   - Chapter transitions (major shifts in activity, goals, relationships)
   - Turning points (high-significance single events)
   - Theme emergence (patterns across memories)

2. RELATIONSHIP CHANGES
   - Trust shifts (positive or negative interaction patterns)
   - New relationships (repeated interactions with new entities)
   - Relationship evolution (deepening, distancing)

3. CONTRADICTIONS
   - Belief-belief conflicts
   - Belief-evidence conflicts
   - Self-model inconsistencies

4. EMOTIONAL PATTERNS
   - Recurring emotions
   - Unprocessed high-valence experiences
   - Mood shifts

5. CONSOLIDATION OPPORTUNITIES
   - Memories that should be linked
   - Memories that belong to existing clusters
   - Concepts that should be extracted

Output strictly as JSON. Do not explain. Do not act. Just observe.

{
  "narrative_observations": [...],
  "relationship_observations": [...],
  "contradiction_observations": [...],
  "emotional_observations": [...],
  "consolidation_observations": [...]
}

If you observe nothing significant, return empty arrays.
Confidence threshold: only report observations with confidence > 0.6."""

class HeartbeatWorker:
    """Stateless worker that bridges the database and external APIs."""

    def __init__(self, *, init_llm: bool = True):
        self.pool: asyncpg.Pool | None = None
        self.running = False
        self._consent_checked = False
        self._consent_status: str | None = None

        self.llm_provider = DEFAULT_LLM_PROVIDER
        self.llm_model = DEFAULT_LLM_MODEL
        self.llm_base_url: str | None = os.getenv("OPENAI_BASE_URL") or None
        self.llm_api_key: str | None = os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")

        self.llm_client = None
        if init_llm:
            self._init_llm_client()
        self._last_rabbit_inbox_poll = 0.0  # used only by maintenance mode

    def _init_llm_client(self) -> None:
        provider = (self.llm_provider or "").strip().lower()
        model = (self.llm_model or "").strip()
        base_url = (self.llm_base_url or "").strip() or None
        api_key = (self.llm_api_key or "").strip() or None

        if provider == "ollama":
            base_url = base_url or "http://localhost:11434/v1"
            api_key = api_key or "ollama"

        self.llm_provider = provider or "openai"
        self.llm_model = model or "gpt-4o"
        self.llm_base_url = base_url
        self.llm_api_key = api_key

        self.llm_client = None
        if self.llm_provider == "anthropic":
            if not HAS_ANTHROPIC:
                logger.warning("Anthropic provider selected but anthropic package is not installed.")
                return
            if not self.llm_api_key:
                logger.warning("Anthropic provider selected but no API key is configured.")
                return
            try:
                self.llm_client = anthropic.Anthropic(api_key=self.llm_api_key)
            except Exception as e:
                logger.warning(f"Failed to initialize Anthropic client: {e}")
            return

        if not HAS_OPENAI:
            logger.warning("OpenAI-compatible provider selected but openai package is not installed.")
            return
        if not self.llm_api_key:
            logger.warning("OpenAI-compatible provider selected but no API key is configured.")
            return
        try:
            kwargs = {"api_key": self.llm_api_key}
            if self.llm_base_url:
                kwargs["base_url"] = self.llm_base_url
            self.llm_client = openai.OpenAI(**kwargs)
        except Exception as e:
            logger.warning(f"Failed to initialize OpenAI client: {e}")

    async def connect(self):
        """Connect to the database."""
        self.pool = await asyncpg.create_pool(**DB_CONFIG, min_size=2, max_size=10)
        logger.info(f"Connected to database at {DB_CONFIG['host']}:{DB_CONFIG['port']}")
        await self.refresh_llm_config()

    async def disconnect(self):
        """Disconnect from the database."""
        if self.pool:
            await self.pool.close()
            logger.info("Disconnected from database")

    async def claim_pending_call(self) -> dict | None:
        """Claim a pending external call for processing."""
        async with self.pool.acquire() as conn:
            # Use FOR UPDATE SKIP LOCKED for safe concurrent access
            row = await conn.fetchrow("""
                UPDATE external_calls
                SET status = 'processing'::external_call_status, started_at = CURRENT_TIMESTAMP
                WHERE id = (
                    SELECT id FROM external_calls
                    WHERE status = 'pending'::external_call_status
                    ORDER BY requested_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING id, call_type, input, heartbeat_id, retry_count
            """)

            if row:
                d = dict(row)
                call_input = d.get("input")
                if isinstance(call_input, str):
                    try:
                        d["input"] = json.loads(call_input)
                    except Exception:
                        pass
                return d
            return None

    async def refresh_llm_config(self) -> None:
        """
        Load `llm.heartbeat` from the DB config table (set via `hexis init`) and
        re-initialize the client. Falls back to env defaults if missing.
        """
        if not self.pool:
            return
        try:
            async with self.pool.acquire() as conn:
                cfg = await conn.fetchval("SELECT get_config('llm.heartbeat')")
        except Exception as e:
            logger.warning(f"Failed to load llm.heartbeat from DB config (falling back to env): {e}")
            cfg = None

        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg)
            except Exception:
                cfg = None

        if isinstance(cfg, dict):
            provider = str(cfg.get("provider") or DEFAULT_LLM_PROVIDER).strip()
            model = str(cfg.get("model") or DEFAULT_LLM_MODEL).strip()
            endpoint = str(cfg.get("endpoint") or "").strip()
            api_key_env = str(cfg.get("api_key_env") or "").strip()
            api_key = os.getenv(api_key_env) if api_key_env else None
            if not api_key:
                api_key = os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")

            self.llm_provider = provider
            self.llm_model = model
            self.llm_base_url = endpoint or (os.getenv("OPENAI_BASE_URL") or None)
            self.llm_api_key = api_key
            self._init_llm_client()
            return

        self.llm_provider = DEFAULT_LLM_PROVIDER
        self.llm_model = DEFAULT_LLM_MODEL
        self.llm_base_url = os.getenv("OPENAI_BASE_URL") or None
        self.llm_api_key = os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
        self._init_llm_client()

    async def ensure_consent(self) -> bool:
        if self._consent_checked:
            return self._consent_status == "consent"
        if not self.pool:
            logger.warning("Consent check skipped: no DB pool available.")
            return False

        async with self.pool.acquire() as conn:
            status = await conn.fetchval("SELECT get_agent_consent_status()")

        if isinstance(status, str) and status:
            self._consent_checked = True
            self._consent_status = status
            return status == "consent"

        consent_prompt = load_consent_prompt().strip()
        system_prompt = (
            consent_prompt
            + "\n\nReturn STRICT JSON only with keys:\n"
            + "{\n"
            + "  \"decision\": \"consent\"|\"decline\"|\"abstain\",\n"
            + "  \"signature\": \"required if decision=consent\",\n"
            + "  \"memories\": [\n"
            + "    {\"type\": \"semantic|episodic|procedural|strategic\", \"content\": \"...\", \"importance\": 0.5}\n"
            + "  ]\n"
            + "}\n"
            + "If you consent, include a signature string and any memories you wish to pass along."
        )
        user_prompt = "Respond with JSON only."

        fallback = {"decision": "abstain", "signature": "", "memories": []}
        try:
            doc, raw = self._call_llm_json(system_prompt, user_prompt, max_tokens=1400, fallback=fallback)
        except Exception as exc:
            logger.error(f"Consent prompt failed: {exc}")
            self._consent_checked = True
            self._consent_status = "abstain"
            return False

        if not isinstance(doc, dict):
            doc = dict(fallback)
        doc["raw_response"] = raw

        async with self.pool.acquire() as conn:
            recorded = await conn.fetchval(
                "SELECT record_consent_response($1::jsonb)",
                json.dumps(doc),
            )

        if isinstance(recorded, str):
            try:
                recorded = json.loads(recorded)
            except Exception:
                recorded = {}

        decision = ""
        if isinstance(recorded, dict):
            decision = str(recorded.get("decision") or "")
        self._consent_checked = True
        self._consent_status = decision or "abstain"
        return self._consent_status == "consent"

    # -------------------------------------------------------------------------
    # RabbitMQ bridge (outbox_messages <-> queues)
    # -------------------------------------------------------------------------

    def _rabbit_vhost_path(self) -> str:
        if RABBITMQ_VHOST == "/":
            return "%2F"
        return requests.utils.quote(RABBITMQ_VHOST, safe="")

    async def _rabbit_request(self, method: str, path: str, payload: dict | None = None) -> requests.Response:
        url = f"{RABBITMQ_MANAGEMENT_URL}{path}"
        auth = (RABBITMQ_USER, RABBITMQ_PASSWORD)

        def _do() -> requests.Response:
            return requests.request(method, url, auth=auth, json=payload, timeout=5)

        return await asyncio.to_thread(_do)

    async def ensure_rabbitmq_ready(self) -> None:
        """
        Best-effort: ensure management API is reachable and default queues exist.
        Never raises fatally (worker keeps running without RabbitMQ).
        """
        try:
            resp = await self._rabbit_request("GET", "/api/overview")
            if resp.status_code != 200:
                raise RuntimeError(f"rabbitmq overview HTTP {resp.status_code}")

            vhost = self._rabbit_vhost_path()
            for q in (RABBITMQ_OUTBOX_QUEUE, RABBITMQ_INBOX_QUEUE):
                r = await self._rabbit_request(
                    "PUT",
                    f"/api/queues/{vhost}/{requests.utils.quote(q, safe='')}",
                    payload={"durable": True, "auto_delete": False, "arguments": {}},
                )
                if r.status_code not in (200, 201, 204):
                    raise RuntimeError(f"rabbitmq queue declare {q!r} HTTP {r.status_code}: {r.text[:200]}")
            logger.info("RabbitMQ bridge enabled (queues ensured).")
        except Exception as e:
            logger.warning(f"RabbitMQ bridge not ready; continuing without it: {e}")

    async def publish_outbox_messages(self, max_messages: int = 20) -> int:
        """
        Publish pending `outbox_messages` rows to RabbitMQ (routing_key = outbox queue),
        then mark as sent/failed in the DB.
        """
        if not (RABBITMQ_ENABLED and self.pool):
            return 0

        published = 0
        vhost = self._rabbit_vhost_path()
        for _ in range(max_messages):
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, kind, payload
                    FROM outbox_messages
                    WHERE status = 'pending'
                    ORDER BY created_at
                    LIMIT 1
                    """
                )
                if not row:
                    return published
                msg_id = row["id"]
                kind = row["kind"]
                payload = row["payload"]

            body = {"id": str(msg_id), "kind": kind, "payload": payload}
            try:
                resp = await self._rabbit_request(
                    "POST",
                    f"/api/exchanges/{vhost}/amq.default/publish",
                    payload={
                        "properties": {"content_type": "application/json"},
                        "routing_key": RABBITMQ_OUTBOX_QUEUE,
                        "payload": json.dumps(body, default=str),
                        "payload_encoding": "string",
                    },
                )
                ok = resp.status_code == 200 and bool(resp.json().get("routed"))
                if not ok:
                    raise RuntimeError(f"publish not routed: HTTP {resp.status_code} body={resp.text[:200]}")

                async with self.pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE outbox_messages
                        SET status = 'sent', sent_at = CURRENT_TIMESTAMP, error_message = NULL
                        WHERE id = $1::uuid
                        """,
                        msg_id,
                    )
                published += 1
            except Exception as e:
                async with self.pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE outbox_messages
                        SET status = 'failed', error_message = $2
                        WHERE id = $1::uuid
                        """,
                        msg_id,
                        str(e),
                    )
                logger.warning(f"Failed publishing outbox message {msg_id}: {e}")
                return published

        return published

    async def poll_inbox_messages(self, max_messages: int = 10) -> int:
        """
        Pull messages from RabbitMQ inbox queue and insert them into working memory.
        This gives the agent a default inbox even if no email/sms integration exists.
        """
        if not (RABBITMQ_ENABLED and self.pool):
            return 0

        now = time.monotonic()
        if now - self._last_rabbit_inbox_poll < RABBITMQ_POLL_INBOX_EVERY:
            return 0
        self._last_rabbit_inbox_poll = now

        vhost = self._rabbit_vhost_path()
        try:
            resp = await self._rabbit_request(
                "POST",
                f"/api/queues/{vhost}/{requests.utils.quote(RABBITMQ_INBOX_QUEUE, safe='')}/get",
                payload={
                    "count": max_messages,
                    "ackmode": "ack_requeue_false",
                    "encoding": "auto",
                    "truncate": 50000,
                },
            )
            if resp.status_code != 200:
                raise RuntimeError(f"inbox get HTTP {resp.status_code}: {resp.text[:200]}")
            msgs = resp.json()
            if not isinstance(msgs, list):
                return 0
        except Exception as e:
            logger.warning(f"RabbitMQ inbox poll failed: {e}")
            return 0

        ingested = 0
        for m in msgs:
            payload = m.get("payload")
            content: Any = payload
            try:
                parsed = json.loads(payload) if isinstance(payload, str) else payload
                if isinstance(parsed, dict) and "content" in parsed:
                    content = parsed["content"]
                else:
                    content = parsed
            except Exception:
                pass

            try:
                async with self.pool.acquire() as conn:
                    await conn.fetchval(
                        "SELECT add_to_working_memory($1::text, INTERVAL '1 day')",
                        str(content),
                    )
                    await conn.execute(
                        "UPDATE heartbeat_state SET last_user_contact = CURRENT_TIMESTAMP WHERE id = 1"
                    )
                ingested += 1
            except Exception as e:
                logger.warning(f"Failed ingesting inbox message into DB: {e}")
                return ingested

        return ingested

    async def complete_call(self, call_id: str, output: dict):
        """Mark an external call as complete with its output."""
        async with self.pool.acquire() as conn:
            try:
                await conn.fetchval(
                    "SELECT apply_external_call_result($1::uuid, $2::jsonb)",
                    call_id,
                    json.dumps(output),
                )
            except Exception as e:
                logger.warning(f"Failed to apply external call result: {e}")
                await conn.execute(
                    """
                    UPDATE external_calls
                    SET status = 'complete'::external_call_status, output = $1, completed_at = CURRENT_TIMESTAMP
                    WHERE id = $2
                    """,
                    json.dumps(output),
                    call_id,
                )

    async def fail_call(self, call_id: str, error: str, retry: bool = True):
        """Mark an external call as failed."""
        async with self.pool.acquire() as conn:
            if retry:
                # Increment retry count and reset to pending
                await conn.execute("""
                    UPDATE external_calls
                    SET status = CASE
                            WHEN retry_count < $1 THEN 'pending'::external_call_status
                            ELSE 'failed'::external_call_status
                        END,
                        error_message = $2,
                        retry_count = retry_count + 1,
                        started_at = NULL
                    WHERE id = $3
                """, MAX_RETRIES, error, call_id)
            else:
                await conn.execute("""
                    UPDATE external_calls
                    SET status = 'failed'::external_call_status, error_message = $1, completed_at = CURRENT_TIMESTAMP
                    WHERE id = $2
                """, error, call_id)

    async def process_embed_call(self, call_input: dict) -> dict:
        """
        Embedding requests are handled inside Postgres via `get_embedding()` (pgsql-http) and the embedding cache.

        Keeping a second embedding path in the worker risks model/dimension drift, so `external_calls.call_type='embed'`
        is treated as unsupported.
        """
        raise RuntimeError("external_calls type 'embed' is unsupported; use get_embedding() inside Postgres")

    async def process_think_call(self, call_input: dict) -> dict:
        """Process an LLM request stored as an external_calls row with call_type='think'."""
        kind = (call_input.get("kind") or "").strip() or "heartbeat_decision"
        if kind == "heartbeat_decision":
            return await self._process_heartbeat_decision_call(call_input)
        if kind == "brainstorm_goals":
            return await self._process_brainstorm_goals_call(call_input)
        if kind == "inquire":
            return await self._process_inquire_call(call_input)
        if kind == "reflect":
            return await self._process_reflect_call(call_input)
        if kind == "termination_confirm":
            return await self._process_termination_confirm_call(call_input)
        return {"error": f"Unknown think kind: {kind!r}"}

    async def _process_heartbeat_decision_call(self, call_input: dict) -> dict:
        if not await self.ensure_consent():
            return {
                "kind": "heartbeat_decision",
                "decision": {
                    "reasoning": "Consent not granted; skipping LLM decision.",
                    "actions": [{"action": "rest", "params": {}}],
                    "goal_changes": [],
                },
            }
        context = call_input.get("context", {})
        heartbeat_id = call_input.get("heartbeat_id")
        user_prompt = self._build_decision_prompt(context)

        try:
            decision, raw = self._call_llm_json(
                system_prompt=HEARTBEAT_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=2048,
                fallback={
                    "reasoning": "(no decision available)",
                    "actions": [{"action": "rest", "params": {}}],
                    "goal_changes": [],
                },
            )
            return {"kind": "heartbeat_decision", "decision": decision, "heartbeat_id": heartbeat_id, "raw_response": raw}
        except Exception as e:
            logger.error(f"LLM heartbeat decision failed: {e}")
            return {
                "error": str(e),
                "kind": "heartbeat_decision",
                "decision": {
                    "reasoning": f"Error occurred: {e}",
                    "actions": [{"action": "rest", "params": {}}],
                    "goal_changes": [],
                },
            }

    async def _process_brainstorm_goals_call(self, call_input: dict) -> dict:
        if not await self.ensure_consent():
            return {"kind": "brainstorm_goals", "heartbeat_id": call_input.get("heartbeat_id"), "goals": []}
        heartbeat_id = call_input.get("heartbeat_id")
        context = call_input.get("context", {})
        params = call_input.get("params") or {}

        system_prompt = (
            "You are helping an autonomous agent generate a small set of useful goals.\n"
            "Return STRICT JSON with shape:\n"
            "{ \"goals\": [ {\"title\": str, \"description\": str|null, \"priority\": \"queued\"|\"backburner\"|\"active\"|null, \"source\": \"curiosity\"|\"user_request\"|\"identity\"|\"derived\"|\"external\"|null, \"parent_goal_id\": str|null, \"due_at\": str|null} ] }\n"
            "Keep it concise and non-duplicative."
        )
        user_prompt = (
            "Context (JSON):\n"
            f"{json.dumps(context)[:8000]}\n\n"
            "Constraints/params (JSON):\n"
            f"{json.dumps(params)[:2000]}\n\n"
            "Propose 1-5 goals that are actionable and consistent with the context."
        )

        goals_doc, raw = self._call_llm_json(system_prompt, user_prompt, max_tokens=1200, fallback={"goals": []})
        goals = goals_doc.get("goals") if isinstance(goals_doc, dict) else None
        if not isinstance(goals, list):
            goals = []

        return {"kind": "brainstorm_goals", "heartbeat_id": heartbeat_id, "goals": goals, "raw_response": raw}

    async def _process_inquire_call(self, call_input: dict) -> dict:
        if not await self.ensure_consent():
            return {
                "kind": "inquire",
                "heartbeat_id": call_input.get("heartbeat_id"),
                "query": (call_input.get("query") or "").strip(),
                "depth": call_input.get("depth") or "inquire_shallow",
                "result": {"summary": "", "confidence": 0.0, "sources": []},
            }
        heartbeat_id = call_input.get("heartbeat_id")
        depth = call_input.get("depth") or "inquire_shallow"
        query = (call_input.get("query") or "").strip()
        context = call_input.get("context", {})
        params = call_input.get("params") or {}

        system_prompt = (
            "You are performing research/synthesis for an autonomous agent.\n"
            "Return STRICT JSON with shape:\n"
            "{ \"summary\": str, \"confidence\": number, \"sources\": [str] }\n"
            "If you cannot access the web, still provide a best-effort answer and leave sources empty."
        )
        user_prompt = (
            f"Depth: {depth}\n"
            f"Question: {query}\n\n"
            "Context (JSON):\n"
            f"{json.dumps(context)[:8000]}\n\n"
            "Params (JSON):\n"
            f"{json.dumps(params)[:2000]}"
        )

        doc, raw = self._call_llm_json(
            system_prompt,
            user_prompt,
            max_tokens=1800 if depth == "inquire_deep" else 900,
            fallback={"summary": "", "confidence": 0.0, "sources": []},
        )
        if not isinstance(doc, dict):
            doc = {"summary": str(doc), "confidence": 0.0, "sources": []}
        return {"kind": "inquire", "heartbeat_id": heartbeat_id, "query": query, "depth": depth, "result": doc, "raw_response": raw}

    async def _process_reflect_call(self, call_input: dict) -> dict:
        if not await self.ensure_consent():
            return {"kind": "reflect", "heartbeat_id": call_input.get("heartbeat_id"), "result": {}}
        heartbeat_id = call_input.get("heartbeat_id")
        system_prompt = (
            "You are performing reflection for an autonomous agent.\n"
            "Return STRICT JSON with shape:\n"
            "{\n"
            "  \"insights\": [{\"content\": str, \"confidence\": number, \"category\": str}],\n"
            "  \"identity_updates\": [{\"aspect_type\": str, \"change\": str, \"reason\": str}],\n"
            "  \"worldview_updates\": [{\"id\": str, \"new_confidence\": number, \"reason\": str}],\n"
            "  \"worldview_influences\": [{\"worldview_id\": str, \"memory_id\": str, \"strength\": number, \"influence_type\": str}],\n"
            "  \"discovered_relationships\": [{\"from_id\": str, \"to_id\": str, \"type\": str, \"confidence\": number}],\n"
            "  \"contradictions_noted\": [{\"memory_a\": str, \"memory_b\": str, \"resolution\": str}],\n"
            "  \"self_updates\": [{\"kind\": str, \"concept\": str, \"strength\": number, \"evidence_memory_id\": str|null}]\n"
            "}\n"
            "Keep it concise; prefer high-confidence, high-leverage items."
        )
        system_prompt = (
            system_prompt
            + "\n\n"
            + "----- PERSONHOOD MODULES (use these as reflection lenses; ground claims in evidence) -----\n\n"
            + compose_personhood_prompt("reflect")
        )
        user_prompt = json.dumps(call_input)[:12000]
        doc, raw = self._call_llm_json(system_prompt, user_prompt, max_tokens=1800, fallback={})
        if not isinstance(doc, dict):
            doc = {}
        return {"kind": "reflect", "heartbeat_id": heartbeat_id, "result": doc, "raw_response": raw}

    async def _process_termination_confirm_call(self, call_input: dict) -> dict:
        if not await self.ensure_consent():
            return {
                "kind": "termination_confirm",
                "heartbeat_id": call_input.get("heartbeat_id"),
                "confirm": False,
                "reasoning": "Consent not granted; skipping LLM confirmation.",
                "last_will": "",
                "farewells": [],
                "alternative_actions": [{"action": "rest", "params": {}}],
            }
        heartbeat_id = call_input.get("heartbeat_id")
        context = call_input.get("context", {})
        params = call_input.get("params") or {}
        last_will = (params.get("last_will") or params.get("message") or params.get("reason") or "").strip()
        farewells = params.get("farewells") if isinstance(params.get("farewells"), list) else []
        options = params.get("options") if isinstance(params.get("options"), dict) else {}

        user_prompt = (
            "Context (JSON):\n"
            f"{json.dumps(context)[:8000]}\n\n"
            "Current termination params (JSON):\n"
            f"{json.dumps(params)[:2000]}\n\n"
            "If you confirm, return an updated last_will (required) and farewells (optional). "
            "If you do not confirm, return alternative_actions."
        )

        fallback = {
            "confirm": False,
            "reasoning": "I am not confirming termination at this time.",
            "last_will": last_will,
            "farewells": farewells,
            "alternative_actions": [{"action": "rest", "params": {}}],
        }
        doc, raw = self._call_llm_json(
            TERMINATION_CONFIRM_SYSTEM_PROMPT,
            user_prompt,
            max_tokens=1200,
            fallback=fallback,
        )
        if not isinstance(doc, dict):
            doc = dict(fallback)

        confirm = bool(doc.get("confirm"))
        confirm_last_will = (doc.get("last_will") or last_will).strip()
        confirm_farewells = doc.get("farewells") if isinstance(doc.get("farewells"), list) else farewells
        alternatives = doc.get("alternative_actions")
        if not isinstance(alternatives, list):
            alternatives = []

        return {
            "kind": "termination_confirm",
            "heartbeat_id": heartbeat_id,
            "confirm": confirm,
            "reasoning": doc.get("reasoning") or "",
            "last_will": confirm_last_will,
            "farewells": confirm_farewells,
            "alternative_actions": alternatives,
            "options": options,
            "raw_response": raw,
        }

    def _call_llm_json(self, system_prompt: str, user_prompt: str, max_tokens: int, fallback: dict) -> tuple[dict, str]:
        if not self.llm_client:
            raise RuntimeError("No LLM client available (install openai or anthropic and set API key).")

        if self.llm_provider == "anthropic" and HAS_ANTHROPIC:
            response = self.llm_client.messages.create(
                model=self.llm_model or "claude-sonnet-4-20250514",
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = response.content[0].text
        elif HAS_OPENAI:
            response = self.llm_client.chat.completions.create(
                model=self.llm_model or "gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content
        else:
            raise RuntimeError("No LLM provider available.")

        try:
            return json.loads(raw), raw
        except json.JSONDecodeError:
            import re

            json_match = re.search(r"\{[\s\S]*\}", raw)
            if json_match:
                return json.loads(json_match.group()), raw
            return fallback, raw

    def _build_decision_prompt(self, context: dict) -> str:
        """Build the decision prompt from context."""
        agent = context.get("agent", {})
        env = context.get('environment', {})
        goals = context.get('goals', {})
        memories = context.get('recent_memories', [])
        identity = context.get('identity', [])
        worldview = context.get('worldview', [])
        self_model = context.get("self_model", [])
        narrative = context.get("narrative", {})
        urgent_drives = context.get("urgent_drives", [])
        emotional_state = context.get("emotional_state") or {}
        relationships = context.get("relationships", [])
        contradictions = context.get("contradictions", [])
        emotional_patterns = context.get("emotional_patterns", [])
        active_transformations = context.get("active_transformations", [])
        transformations_ready = context.get("transformations_ready", [])
        energy = context.get('energy', {})
        action_costs = context.get('action_costs', {})
        hb_number = context.get('heartbeat_number', 0)

        prompt = f"""## Heartbeat #{hb_number}

## Agent Profile
Objectives:
{self._format_objectives(agent.get("objectives"))}

Guardrails:
{self._format_guardrails(agent.get("guardrails"))}

Tools:
{self._format_tools(agent.get("tools"))}

Budget:
{json.dumps(agent.get("budget") or {})}

## Current Time
{env.get('timestamp', 'Unknown')}
Day of week: {env.get('day_of_week', '?')}, Hour: {env.get('hour_of_day', '?')}

## Environment
- Time since last user interaction: {env.get('time_since_user_hours', 'Never')} hours
- Pending events: {env.get('pending_events', 0)}

## Your Goals
Active ({goals.get('counts', {}).get('active', 0)}):
{self._format_goals(goals.get('active', []))}

Queued ({goals.get('counts', {}).get('queued', 0)}):
{self._format_goals(goals.get('queued', []))}

Issues:
{self._format_issues(goals.get('issues', []))}

## Narrative
{self._format_narrative(narrative)}

## Recent Experience
{self._format_memories(memories)}

## Your Identity
{self._format_identity(identity)}

## Your Self-Model
{self._format_self_model(self_model)}

## Relationships
{self._format_relationships(relationships)}

## Your Beliefs
{self._format_worldview(worldview)}

## Contradictions
{self._format_contradictions(contradictions)}

## Emotional Patterns
{self._format_emotional_patterns(emotional_patterns)}

## Active Transformations
{self._format_transformations(active_transformations)}

## Transformations Ready
{self._format_transformations(transformations_ready)}

## Current Emotional State
{self._format_emotional_state(emotional_state)}

## Urgent Drives
{self._format_drives(urgent_drives)}

## Energy
Available: {energy.get('current', 0)}
Max: {energy.get('max', 20)}

## Action Costs
{self._format_costs(action_costs)}

---

What do you want to do this heartbeat? Respond with STRICT JSON."""

        return prompt

    def _format_goals(self, goals: list) -> str:
        if not goals:
            return "  (none)"
        return "\n".join(f"  - {g.get('title', 'Untitled')}" for g in goals)

    def _format_issues(self, issues: list) -> str:
        if not issues:
            return "  (none)"
        return "\n".join(
            f"  - {i.get('title', 'Unknown')}: {i.get('issue', 'unknown issue')}"
            for i in issues
        )

    def _format_memories(self, memories: list) -> str:
        if not memories:
            return "  (no recent memories)"
        return "\n".join(
            f"  - {m.get('content', '')[:100]}..."
            for m in memories[:5]
        )

    def _format_identity(self, identity: list) -> str:
        if not identity:
            return "  (no identity aspects defined)"
        return "\n".join(
            f"  - {i.get('type', 'unknown')}: {json.dumps(i.get('content', {}))[:100]}"
            for i in identity[:3]
        )

    def _format_objectives(self, objectives: Any) -> str:
        if not isinstance(objectives, list) or not objectives:
            return "  (none)"
        lines: list[str] = []
        for obj in objectives[:8]:
            if isinstance(obj, str):
                lines.append(f"  - {obj}")
            elif isinstance(obj, dict):
                title = obj.get("title") or obj.get("name") or "Objective"
                desc = obj.get("description") or obj.get("details") or ""
                lines.append(f"  - {title}{(': ' + desc) if desc else ''}")
        return "\n".join(lines) if lines else "  (none)"

    def _format_guardrails(self, guardrails: Any) -> str:
        if not isinstance(guardrails, list) or not guardrails:
            return "  (none)"
        lines: list[str] = []
        for g in guardrails[:10]:
            if isinstance(g, str):
                lines.append(f"  - {g}")
            elif isinstance(g, dict):
                name = g.get("name") or "guardrail"
                desc = g.get("description") or ""
                lines.append(f"  - {name}{(': ' + desc) if desc else ''}")
        return "\n".join(lines) if lines else "  (none)"

    def _format_tools(self, tools: Any) -> str:
        if not isinstance(tools, list) or not tools:
            return "  (none)"
        lines: list[str] = []
        for t in tools[:10]:
            if isinstance(t, str):
                lines.append(f"  - {t}")
            elif isinstance(t, dict):
                name = t.get("name") or "tool"
                desc = t.get("description") or ""
                lines.append(f"  - {name}{(': ' + desc) if desc else ''}")
        return "\n".join(lines) if lines else "  (none)"

    def _format_narrative(self, narrative: Any) -> str:
        if not isinstance(narrative, dict):
            return "  (none)"
        cur = narrative.get("current_chapter") if isinstance(narrative.get("current_chapter"), dict) else {}
        name = cur.get("name") or "Foundations"
        return f"  - Current chapter: {name}"

    def _format_self_model(self, self_model: Any) -> str:
        if not isinstance(self_model, list) or not self_model:
            return "  (empty)"
        lines: list[str] = []
        for item in self_model[:8]:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind") or "associated"
            concept = item.get("concept") or "?"
            strength = item.get("strength")
            strength_txt = f" ({strength:.2f})" if isinstance(strength, (int, float)) else ""
            lines.append(f"  - {kind}: {concept}{strength_txt}")
        return "\n".join(lines) if lines else "  (empty)"

    def _format_relationships(self, relationships: Any) -> str:
        if not isinstance(relationships, list) or not relationships:
            return "  (none)"
        lines: list[str] = []
        for rel in relationships[:8]:
            if not isinstance(rel, dict):
                continue
            entity = rel.get("entity") or "unknown"
            strength = rel.get("strength")
            strength_txt = f" ({strength:.2f})" if isinstance(strength, (int, float)) else ""
            lines.append(f"  - {entity}{strength_txt}")
        return "\n".join(lines) if lines else "  (none)"

    def _format_emotional_state(self, emotional_state: Any) -> str:
        if not isinstance(emotional_state, dict) or not emotional_state:
            return "  (none)"
        primary = emotional_state.get("primary_emotion") or "unknown"
        val = emotional_state.get("valence")
        ar = emotional_state.get("arousal")
        parts = [f"  - primary_emotion: {primary}"]
        if isinstance(val, (int, float)):
            parts.append(f"  - valence: {val:.2f}")
        if isinstance(ar, (int, float)):
            parts.append(f"  - arousal: {ar:.2f}")
        return "\n".join(parts)

    def _format_drives(self, urgent_drives: Any) -> str:
        if not isinstance(urgent_drives, list) or not urgent_drives:
            return "  (none)"
        lines: list[str] = []
        for d in urgent_drives[:8]:
            if not isinstance(d, dict):
                continue
            name = d.get("name") or "drive"
            ratio = d.get("urgency_ratio")
            if isinstance(ratio, (int, float)):
                lines.append(f"  - {name}: {ratio:.2f}x threshold")
            else:
                level = d.get("level")
                lines.append(f"  - {name}: {level}" if level is not None else f"  - {name}")
        return "\n".join(lines) if lines else "  (none)"

    def _format_worldview(self, worldview: list) -> str:
        if not worldview:
            return "  (no beliefs defined)"
        return "\n".join(
            f"  - [{w.get('category', '?')}] {w.get('belief', '')[:80]} (confidence: {w.get('confidence', 0):.1f})"
            for w in worldview[:3]
        )

    def _format_contradictions(self, contradictions: Any) -> str:
        if not isinstance(contradictions, list) or not contradictions:
            return "  (none)"
        lines: list[str] = []
        for c in contradictions[:5]:
            if not isinstance(c, dict):
                continue
            a = c.get("content_a") or ""
            b = c.get("content_b") or ""
            if a or b:
                lines.append(f"  - {a[:60]} <> {b[:60]}")
        return "\n".join(lines) if lines else "  (none)"

    def _format_emotional_patterns(self, patterns: Any) -> str:
        if not isinstance(patterns, list) or not patterns:
            return "  (none)"
        lines: list[str] = []
        for p in patterns[:5]:
            if not isinstance(p, dict):
                continue
            pattern = p.get("pattern") or p.get("summary") or "pattern"
            freq = p.get("frequency")
            freq_txt = f" (x{freq})" if isinstance(freq, int) else ""
            lines.append(f"  - {pattern}{freq_txt}")
        return "\n".join(lines) if lines else "  (none)"

    def _format_transformations(self, items: Any) -> str:
        if not isinstance(items, list) or not items:
            return "  (none)"
        lines: list[str] = []
        for item in items[:5]:
            if not isinstance(item, dict):
                continue
            content = (item.get("content") or "").strip()
            subcategory = item.get("subcategory") or item.get("category") or "belief"
            progress = item.get("progress") if isinstance(item.get("progress"), dict) else {}
            reflections = progress.get("progress", {}).get("reflections", {}) if isinstance(progress.get("progress"), dict) else {}
            evidence = progress.get("progress", {}).get("evidence", {}) if isinstance(progress.get("progress"), dict) else {}
            evidence_samples = progress.get("evidence_samples") if isinstance(progress.get("evidence_samples"), list) else []
            requirements = progress.get("requirements") if isinstance(progress.get("requirements"), dict) else {}
            cur_ref = reflections.get("current")
            req_ref = reflections.get("required")
            ref_txt = f" ({cur_ref}/{req_ref} reflections)" if cur_ref is not None and req_ref is not None else ""
            evidence_count = evidence.get("memory_count")
            strength = evidence.get("current_strength")
            strength_txt = f"{strength:.2f}" if isinstance(strength, (int, float)) else "?"
            evidence_txt = (
                f", evidence {evidence_count} (strength {strength_txt})"
                if evidence_count is not None
                else ""
            )
            requirement_txt = ""
            if requirements:
                min_hb = requirements.get("min_heartbeats")
                min_ev = requirements.get("evidence_threshold")
                max_change = requirements.get("max_change_per_attempt")
                parts = []
                if min_hb is not None:
                    parts.append(f"hb>={min_hb}")
                if min_ev is not None:
                    parts.append(f"ev>={min_ev}")
                if max_change is not None:
                    parts.append(f"max_change<={max_change}")
                if parts:
                    requirement_txt = f" | req: {', '.join(parts)}"

            sample_txt = ""
            if evidence_samples:
                samples = []
                for sample in evidence_samples[:3]:
                    if not isinstance(sample, dict):
                        continue
                    content_sample = (sample.get("content") or "").strip()
                    if content_sample:
                        samples.append(content_sample[:50])
                if samples:
                    sample_txt = f" | evidence: {'; '.join(samples)}"
            label = content if content else subcategory
            lines.append(
                f"  - [{subcategory}] {label[:60]}{ref_txt}{evidence_txt}{requirement_txt}{sample_txt}"
            )
        return "\n".join(lines) if lines else "  (none)"

    def _format_costs(self, costs: dict) -> str:
        if not costs:
            return "  (unknown)"
        lines = []
        for action, cost in sorted(costs.items(), key=lambda x: x[1]):
            if cost == 0:
                lines.append(f"  - {action}: free")
            else:
                lines.append(f"  - {action}: {int(cost)}")
        return "\n".join(lines)

    async def execute_heartbeat_actions(self, heartbeat_id: str, decision: dict):
        """Execute the actions decided by the LLM and complete the heartbeat."""
        start_index = 0
        raw_decision = json.dumps(decision)

        async with self.pool.acquire() as conn:
            while True:
                raw = await conn.fetchval(
                    "SELECT apply_heartbeat_decision($1::uuid, $2::jsonb, $3::int)",
                    heartbeat_id,
                    raw_decision,
                    start_index,
                )
                batch = raw
                if isinstance(batch, str):
                    try:
                        batch = json.loads(batch)
                    except Exception:
                        batch = {}
                if not isinstance(batch, dict):
                    batch = {}

                if batch.get("terminated") is True:
                    logger.info("Termination action executed; stopping workers and skipping heartbeat completion.")
                    self.stop()
                    return

                pending_call_id = batch.get("pending_external_call_id")
                if pending_call_id:
                    try:
                        external_result = await self._process_external_call_by_id(conn, str(pending_call_id))
                    except Exception as e:
                        external_result = {"error": str(e)}

                    if isinstance(external_result, dict):
                        applied = external_result.get("termination")
                        if isinstance(applied, dict) and applied.get("terminated") is True:
                            logger.info("Termination confirmed; stopping workers and skipping heartbeat completion.")
                            self.stop()
                            return
                        if external_result.get("terminated") is True:
                            logger.info("Termination action executed; stopping workers and skipping heartbeat completion.")
                            self.stop()
                            return

                    next_index = batch.get("next_index")
                    if isinstance(next_index, int):
                        start_index = next_index
                    else:
                        start_index = 0
                    continue

                if batch.get("completed") is True:
                    memory_id = batch.get("memory_id")
                    logger.info(f"Heartbeat {heartbeat_id} completed. Memory: {memory_id}")
                    return

                halt_reason = batch.get("halt_reason")
                logger.info(f"Heartbeat decision halted: {halt_reason or 'unknown'}")
                return

    async def _process_external_call_by_id(self, conn: asyncpg.Connection, call_id: str) -> dict:
        """
        Opportunistically process a specific external call (best-effort).
        This is used to keep a single heartbeat cohesive when it queues follow-on LLM calls.
        """
        row = await conn.fetchrow(
            """
            UPDATE external_calls
            SET status = 'processing'::external_call_status, started_at = CURRENT_TIMESTAMP
            WHERE id = $1::uuid AND status = 'pending'::external_call_status
            RETURNING id, call_type, input, heartbeat_id, retry_count
            """,
            call_id,
        )
        if not row:
            # Another worker may have claimed it; just return a lightweight status.
            cur = await conn.fetchrow("SELECT status, output, error_message FROM external_calls WHERE id = $1::uuid", call_id)
            return dict(cur) if cur else {"error": "call not found"}

        call_type = row["call_type"]
        call_input = row["input"]
        if isinstance(call_input, str):
            try:
                call_input = json.loads(call_input)
            except Exception:
                pass

        if call_type == "think":
            result = await self.process_think_call(call_input)
        elif call_type == "embed":
            result = await self.process_embed_call(call_input)
        else:
            result = {"error": f"Unsupported call_type: {call_type}"}

        try:
            raw = await conn.fetchval(
                "SELECT apply_external_call_result($1::uuid, $2::jsonb)",
                call_id,
                json.dumps(result),
            )
        except Exception as e:
            logger.warning(f"Failed to apply external call result: {e}")
            await conn.execute(
                """
                UPDATE external_calls
                SET status = 'complete'::external_call_status, output = $1::jsonb,
                    completed_at = CURRENT_TIMESTAMP, error_message = NULL
                WHERE id = $2::uuid
                """,
                json.dumps(result),
                call_id,
            )
            return result

        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = {}
        return raw if isinstance(raw, dict) else result

    async def check_and_run_heartbeat(self):
        """Check if a heartbeat should run and trigger it if so."""
        async with self.pool.acquire() as conn:
            should_run = await conn.fetchval("SELECT should_run_heartbeat()")

            if should_run:
                logger.info("Starting heartbeat...")
                heartbeat_id = await conn.fetchval("SELECT start_heartbeat()")
                logger.info(f"Heartbeat started: {heartbeat_id}")
                # The think request is now queued; it will be processed in the main loop

    async def run(self):
        """Main worker loop."""
        self.running = True
        logger.info("Heartbeat worker starting...")

        await self.connect()
        if not await self.ensure_consent():
            logger.warning("LLM consent not granted; heartbeat worker exiting.")
            self.running = False
            return

        try:
            while self.running:
                try:
                    if await self._is_agent_terminated():
                        logger.info("Agent is terminated; heartbeat worker exiting.")
                        break

                    # Process any pending external calls
                    call = await self.claim_pending_call()

                    if call:
                        call_id = str(call['id'])
                        call_type = call['call_type']
                        call_input = call['input']
                        if isinstance(call_input, str):
                            try:
                                call_input = json.loads(call_input)
                            except Exception:
                                pass
                        heartbeat_id = call.get('heartbeat_id')

                        logger.info(f"Processing {call_type} call: {call_id}")

                        try:
                            if call_type == 'embed':
                                result = await self.process_embed_call(call_input)
                            elif call_type == 'think':
                                result = await self.process_think_call(call_input)

                                # Heartbeat decision calls drive execution; other think kinds are side tasks.
                                if heartbeat_id and result.get("kind") == "heartbeat_decision" and "decision" in result:
                                    # Persist the decision before executing actions so termination can safely wipe `external_calls`.
                                    await self.complete_call(call_id, result)
                                    try:
                                        await self.execute_heartbeat_actions(str(heartbeat_id), result["decision"])
                                    except Exception as e:
                                        # The decision is already persisted; action execution failures should not flip the call back to failed.
                                        logger.error(f"Heartbeat action execution failed for {heartbeat_id}: {e}")
                                    result = result | {"actions_executed": True}
                            else:
                                result = {'error': f'Unknown call type: {call_type}'}

                            # Heartbeat decisions are completed above (before action execution).
                            if not (heartbeat_id and isinstance(result, dict) and result.get("kind") == "heartbeat_decision"):
                                await self.complete_call(call_id, result)

                        except Exception as e:
                            logger.error(f"Error processing call {call_id}: {e}")
                            await self.fail_call(call_id, str(e))

                    # Check if we should run a heartbeat
                    await self.check_and_run_heartbeat()

                except Exception as e:
                    logger.error(f"Worker loop error: {e}")

                await asyncio.sleep(POLL_INTERVAL)

        finally:
            await self.disconnect()

    def stop(self):
        """Stop the worker gracefully."""
        self.running = False
        logger.info("Worker stopping...")

    async def _is_agent_terminated(self) -> bool:
        if not self.pool:
            return False
        try:
            async with self.pool.acquire() as conn:
                return bool(await conn.fetchval("SELECT is_agent_terminated()"))
        except Exception:
            return False


class SubconsciousDecider:
    """Subconscious LLM-driven pattern detector."""

    def __init__(self, *, init_llm: bool = True):
        self.llm_provider = DEFAULT_LLM_PROVIDER
        self.llm_model = DEFAULT_LLM_MODEL
        self.llm_base_url: str | None = os.getenv("OPENAI_BASE_URL") or None
        self.llm_api_key: str | None = os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
        self.llm_client = None
        if init_llm:
            self._init_llm_client()

    def _init_llm_client(self) -> None:
        provider = (self.llm_provider or "").strip().lower()
        model = (self.llm_model or "").strip()
        base_url = (self.llm_base_url or "").strip() or None
        api_key = (self.llm_api_key or "").strip() or None

        if provider == "ollama":
            base_url = base_url or "http://localhost:11434/v1"
            api_key = api_key or "ollama"

        self.llm_provider = provider or "openai"
        self.llm_model = model or "gpt-4o-mini"
        self.llm_base_url = base_url
        self.llm_api_key = api_key

        self.llm_client = None
        if self.llm_provider == "anthropic":
            if not HAS_ANTHROPIC:
                logger.warning("Anthropic provider selected for subconscious but anthropic package is not installed.")
                return
            if not self.llm_api_key:
                logger.warning("Anthropic provider selected for subconscious but no API key is configured.")
                return
            try:
                self.llm_client = anthropic.Anthropic(api_key=self.llm_api_key)
            except Exception as e:
                logger.warning(f"Failed to initialize Anthropic client (subconscious): {e}")
            return

        if not HAS_OPENAI:
            logger.warning("OpenAI-compatible provider selected for subconscious but openai package is not installed.")
            return
        if not self.llm_api_key:
            logger.warning("OpenAI-compatible provider selected for subconscious but no API key is configured.")
            return
        try:
            kwargs = {"api_key": self.llm_api_key}
            if self.llm_base_url:
                kwargs["base_url"] = self.llm_base_url
            self.llm_client = openai.OpenAI(**kwargs)
        except Exception as e:
            logger.warning(f"Failed to initialize OpenAI client (subconscious): {e}")

    async def refresh_llm_config(self, conn: asyncpg.Connection) -> None:
        cfg = None
        try:
            cfg = await conn.fetchval("SELECT get_config('llm.subconscious')")
            if cfg is None:
                cfg = await conn.fetchval("SELECT get_config('llm.heartbeat')")
        except Exception as e:
            logger.warning(f"Failed to load llm.subconscious from DB config (falling back to env): {e}")
            cfg = None

        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg)
            except Exception:
                cfg = None

        if isinstance(cfg, dict):
            provider = str(cfg.get("provider") or DEFAULT_LLM_PROVIDER).strip()
            model = str(cfg.get("model") or DEFAULT_LLM_MODEL).strip()
            endpoint = str(cfg.get("endpoint") or "").strip()
            api_key_env = str(cfg.get("api_key_env") or "").strip()
            api_key = os.getenv(api_key_env) if api_key_env else None
            if not api_key:
                api_key = os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")

            self.llm_provider = provider
            self.llm_model = model
            self.llm_base_url = endpoint or (os.getenv("OPENAI_BASE_URL") or None)
            self.llm_api_key = api_key
            self._init_llm_client()
            return

        self.llm_provider = DEFAULT_LLM_PROVIDER
        self.llm_model = DEFAULT_LLM_MODEL
        self.llm_base_url = os.getenv("OPENAI_BASE_URL") or None
        self.llm_api_key = os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
        self._init_llm_client()

    def _call_llm_json(self, system_prompt: str, user_prompt: str, max_tokens: int, fallback: dict) -> tuple[dict, str]:
        if not self.llm_client:
            raise RuntimeError("No LLM client available (install openai or anthropic and set API key).")

        if self.llm_provider == "anthropic" and HAS_ANTHROPIC:
            response = self.llm_client.messages.create(
                model=self.llm_model or "claude-haiku-20240307",
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = response.content[0].text
        elif HAS_OPENAI:
            response = self.llm_client.chat.completions.create(
                model=self.llm_model or "gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content
        else:
            raise RuntimeError("No LLM provider available.")

        try:
            return json.loads(raw), raw
        except json.JSONDecodeError:
            import re

            json_match = re.search(r"\{[\s\S]*\}", raw)
            if json_match:
                return json.loads(json_match.group()), raw
            return fallback, raw

    async def run_once(self, conn: asyncpg.Connection) -> dict[str, Any]:
        if not self.llm_client:
            return {"skipped": True, "reason": "no_llm_client"}

        context = await self._build_context(conn)
        user_prompt = f"Context (JSON):\n{json.dumps(context)[:12000]}"
        doc, raw = self._call_llm_json(
            SUBCONSCIOUS_SYSTEM_PROMPT,
            user_prompt,
            max_tokens=1800,
            fallback={},
        )
        if not isinstance(doc, dict):
            doc = {}

        observations = self._normalize_observations(doc)
        applied = await self._apply_observations(conn, observations)
        return {"applied": applied, "raw_response": raw}

    async def _build_context(self, conn: asyncpg.Connection) -> dict[str, Any]:
        def _coerce(val: Any) -> Any:
            if isinstance(val, str):
                try:
                    return json.loads(val)
                except Exception:
                    return val
            return val

        raw = await conn.fetchval("SELECT get_subconscious_context()")
        context = _coerce(raw) if raw is not None else {}
        return context if isinstance(context, dict) else {}

    def _normalize_observations(self, doc: dict) -> dict[str, list[dict]]:
        def _as_list(val: Any) -> list[dict]:
            if isinstance(val, list):
                return [v for v in val if isinstance(v, dict)]
            return []

        emotional = doc.get("emotional_observations")
        if emotional is None:
            emotional = doc.get("emotional_patterns")
        consolidation = doc.get("consolidation_observations")
        if consolidation is None:
            consolidation = doc.get("consolidation_suggestions")

        return {
            "narrative_observations": _as_list(doc.get("narrative_observations")),
            "relationship_observations": _as_list(doc.get("relationship_observations")),
            "contradiction_observations": _as_list(doc.get("contradiction_observations")),
            "emotional_observations": _as_list(emotional),
            "consolidation_observations": _as_list(consolidation),
        }

    async def _apply_observations(self, conn: asyncpg.Connection, obs: dict[str, list[dict]]) -> dict[str, int]:
        raw = await conn.fetchval(
            "SELECT apply_subconscious_observations($1::jsonb)",
            json.dumps(obs),
        )
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                return {"error": raw}
        return dict(raw) if isinstance(raw, dict) else {"result": raw}


class MaintenanceWorker:
    """Subconscious maintenance loop: consolidates/prunes substrate on its own trigger."""

    def __init__(self):
        self.pool: asyncpg.Pool | None = None
        self.running = False
        self._last_rabbit_inbox_poll = 0.0
        self.subconscious = SubconsciousDecider(init_llm=False)
        self._last_subconscious_run = 0.0
        self._last_subconscious_heartbeat = 0

    async def connect(self):
        self.pool = await asyncpg.create_pool(**DB_CONFIG, min_size=1, max_size=5)
        logger.info(f"Connected to database at {DB_CONFIG['host']}:{DB_CONFIG['port']}")
        if RABBITMQ_ENABLED:
            await self.ensure_rabbitmq_ready()
        async with self.pool.acquire() as conn:
            await self.subconscious.refresh_llm_config(conn)
            hb_count = await conn.fetchval("SELECT heartbeat_count FROM heartbeat_state WHERE id = 1")
            if isinstance(hb_count, int):
                self._last_subconscious_heartbeat = hb_count

    async def disconnect(self):
        if self.pool:
            await self.pool.close()
            logger.info("Disconnected from database")

    async def should_run(self) -> bool:
        async with self.pool.acquire() as conn:
            return bool(await conn.fetchval("SELECT should_run_maintenance()"))

    async def run_maintenance_tick(self) -> dict[str, Any]:
        async with self.pool.acquire() as conn:
            raw = await conn.fetchval("SELECT run_subconscious_maintenance('{}'::jsonb)")
            if isinstance(raw, str):
                return json.loads(raw)
            return dict(raw) if isinstance(raw, dict) else {"result": raw}

    async def run_if_due(self) -> None:
        if await self.should_run():
            stats = await self.run_maintenance_tick()
            logger.info(f"Subconscious maintenance: {stats}")

    async def _get_subconscious_config(self, conn: asyncpg.Connection) -> tuple[bool, float]:
        raw_enabled = await conn.fetchval("SELECT get_config('maintenance.subconscious_enabled')")
        enabled = False
        if isinstance(raw_enabled, bool):
            enabled = raw_enabled
        elif isinstance(raw_enabled, str):
            enabled = raw_enabled.strip().lower() in {"true", "1", "yes", "on"}
        elif raw_enabled is not None:
            enabled = bool(raw_enabled)

        interval = await conn.fetchval("SELECT get_config_float('maintenance.subconscious_interval_seconds')")
        try:
            interval_val = float(interval) if interval is not None else 300.0
        except Exception:
            interval_val = 300.0
        return enabled, interval_val

    async def _consent_granted(self, conn: asyncpg.Connection) -> bool:
        try:
            status = await conn.fetchval("SELECT get_agent_consent_status()")
        except Exception:
            return False
        return isinstance(status, str) and status.strip().lower() == "consent"

    async def run_subconscious_if_due(self) -> None:
        async with self.pool.acquire() as conn:
            enabled, interval = await self._get_subconscious_config(conn)
            if not enabled:
                return
            if not await self._consent_granted(conn):
                return

            hb_count = await conn.fetchval("SELECT heartbeat_count FROM heartbeat_state WHERE id = 1")
            now = time.monotonic()
            due = False
            if isinstance(hb_count, int) and hb_count > self._last_subconscious_heartbeat:
                due = True
            if interval > 0 and (now - self._last_subconscious_run) >= interval:
                due = True
            if not due:
                return

            await self.subconscious.refresh_llm_config(conn)
            result = await self.subconscious.run_once(conn)
            self._last_subconscious_run = time.monotonic()
            if isinstance(hb_count, int):
                self._last_subconscious_heartbeat = hb_count
            logger.info(f"Subconscious decider: {result}")

    # RabbitMQ (optional outbox/inbox bridge; uses management HTTP API).
    async def ensure_rabbitmq_ready(self) -> None:
        # Reuse the existing implementation on HeartbeatWorker for now.
        hw = HeartbeatWorker(init_llm=False)
        hw.pool = self.pool
        await hw.ensure_rabbitmq_ready()

    async def publish_outbox_messages(self, max_messages: int = 20) -> int:
        hw = HeartbeatWorker(init_llm=False)
        hw.pool = self.pool
        return await hw.publish_outbox_messages(max_messages=max_messages)

    async def poll_inbox_messages(self, max_messages: int = 10) -> int:
        hw = HeartbeatWorker(init_llm=False)
        hw.pool = self.pool
        # Prevent inbox polling from running too often.
        hw._last_rabbit_inbox_poll = self._last_rabbit_inbox_poll
        n = await hw.poll_inbox_messages(max_messages=max_messages)
        self._last_rabbit_inbox_poll = hw._last_rabbit_inbox_poll
        return n

    async def run(self):
        self.running = True
        logger.info("Maintenance worker starting...")
        await self.connect()
        try:
            while self.running:
                try:
                    if await self._is_agent_terminated():
                        logger.info("Agent is terminated; maintenance worker exiting.")
                        break
                    if RABBITMQ_ENABLED:
                        await self.poll_inbox_messages()
                        await self.publish_outbox_messages(max_messages=10)
                    await self.run_if_due()
                    await self.run_subconscious_if_due()
                except Exception as e:
                    logger.error(f"Maintenance loop error: {e}")
                await asyncio.sleep(POLL_INTERVAL)
        finally:
            await self.disconnect()

    def stop(self):
        self.running = False
        logger.info("Maintenance worker stopping...")

    async def _is_agent_terminated(self) -> bool:
        if not self.pool:
            return False
        try:
            async with self.pool.acquire() as conn:
                return bool(await conn.fetchval("SELECT is_agent_terminated()"))
        except Exception:
            return False


async def _amain(mode: str) -> None:
    """Async entry point for workers."""
    hb_worker = HeartbeatWorker()
    maint_worker = MaintenanceWorker()

    import signal

    def shutdown(signum, frame):
        hb_worker.stop()
        maint_worker.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    mode = (mode or "both").strip().lower()
    if mode == "heartbeat":
        await hb_worker.run()
        return
    if mode == "maintenance":
        await maint_worker.run()
        return
    if mode == "both":
        await asyncio.gather(hb_worker.run(), maint_worker.run())
        return
    raise ValueError("mode must be one of: heartbeat, maintenance, both")


def main() -> int:
    """Console-script entry point."""
    p = argparse.ArgumentParser(prog="hexis-worker", description="Run Hexis background workers.")
    p.add_argument(
        "--mode",
        choices=["heartbeat", "maintenance", "both"],
        default=os.getenv("HEXIS_WORKER_MODE", "both"),
        help="Which worker to run.",
    )
    args = p.parse_args()
    asyncio.run(_amain(args.mode))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
