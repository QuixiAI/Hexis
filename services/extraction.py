"""Conscious-episode memory formation (#37): the subconscious observer.

Sweeps unprocessed conscious episodes (conversation turns + heartbeat episodes
in ``subconscious_units``) and selectively encodes durable memories: claim a
batch → one ``chat_json`` extraction pass → ``apply_conscious_extraction``
(routes facts through the ingest dedup/corroborate policy) or
``fail_conscious_extraction``. Mirrors ``services/summarization.py``
(claim → chat_json → apply/fail). Gated by config ``extraction.enabled``.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from core.llm_config import load_llm_config
from core.llm_json import chat_json

logger = logging.getLogger("extraction")

_UNIT_TEXT_BUDGET = 3000
_CONTEXT_TEXT_BUDGET = 800


def _format_unit(row: Any, previous: str | None = None) -> str:
    metadata = row["metadata"]
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            metadata = {}
    kind = (metadata or {}).get("kind", "conversation_turn")
    # The turn before, so "their CIO" can be resolved to "Nishi Bank's CIO";
    # facts come only from this episode's own content.
    earlier = (
        f"earlier in this conversation (context only; extract nothing from it):\n{previous[:_CONTEXT_TEXT_BUDGET]}\n"
        if previous else ""
    )
    return (
        f"episode id: {row['id']}\n"
        f"kind: {kind}\n"
        f"speaker/source: {row['source_identity'] or 'user'}\n"
        f"at: {row['turn_at']}\n"
        f"{earlier}"
        f"content:\n{(row['content'] or '')[:_UNIT_TEXT_BUDGET]}"
    )


async def _previous_turns(conn, rows: list[Any]) -> dict[str, str]:
    """The turn just before each episode in the same session, for context."""
    previous: dict[str, str] = {}
    for row in rows:
        if not row["session_id"]:
            continue
        text = await conn.fetchval(
            """
            SELECT content FROM subconscious_units
            WHERE session_id = $1 AND turn_at < $2 AND id <> $3
            ORDER BY turn_at DESC LIMIT 1
            """,
            row["session_id"], row["turn_at"], row["id"],
        )
        if text:
            previous[str(row["id"])] = text
    return previous


async def _supersede_updated_facts(conn, facts: list[Any], started: Any) -> int:
    """A fact that replaces an older one ("she moved to Tokyo") marks the older
    memory as no longer current: valid_until now, so recall and routing stop
    treating it as true while episodes keep the history. Only a close match
    counts, and never a memory written in this same pass."""
    threshold = float(await conn.fetchval(
        "SELECT COALESCE(get_config_float('memory.ingest_theta_related'), 0.8)") or 0.8)
    count = 0
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        old = str(fact.get("updates") or "").strip()
        new = str(fact.get("content") or "").strip()
        if not old or not new:
            continue
        raw = await conn.fetchval("SELECT ingest_route_embedding((get_embedding(ARRAY[$1]))[1])", old)
        routed = json.loads(raw) if isinstance(raw, str) else (raw or {})
        target = routed.get("matched_memory_id")
        if not target or float(routed.get("similarity") or 0) < threshold:
            continue
        updated = await conn.fetchval(
            """
            UPDATE memories
            SET valid_until = CURRENT_TIMESTAMP,
                metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
                    'superseded', jsonb_build_object('by', $2::text, 'at', CURRENT_TIMESTAMP)),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = $1::uuid AND created_at < $3 AND status = 'active'
            RETURNING id
            """,
            target, new, started,
        )
        if updated:
            count += 1
    return count


async def run_conscious_extraction_step(conn) -> dict[str, Any]:
    """Claim and extract one batch of conscious episodes."""
    enabled = await conn.fetchval(
        "SELECT COALESCE(get_config_bool('extraction.enabled'), false)"
    )
    if not enabled:
        return {"skipped": True, "reason": "disabled"}

    rows = await conn.fetch("SELECT * FROM claim_conscious_extraction_batch()")
    if not rows:
        return {"skipped": True, "reason": "no_pending_units"}
    unit_ids = [str(row["id"]) for row in rows]

    try:
        llm_config = await load_llm_config(conn, "llm.extraction", fallback_key="llm.subconscious")
        system = await conn.fetchval(
            "SELECT content FROM prompt_modules WHERE key = 'conscious_extraction'"
        )
        # The extractor knows who "I" is and who the user is (#56/#82) — the
        # DB's label resolution, so facts carry real names and a first-person
        # self.
        labels_raw = await conn.fetchval("SELECT get_turn_labels()")
        labels = json.loads(labels_raw) if isinstance(labels_raw, str) else (labels_raw or {})
        naming = (
            f"You are {labels.get('agent_label', 'Assistant')}. "
            f"The user in these conversations is {labels.get('user_label', 'User')}."
        )
        previous = await _previous_turns(conn, rows)
        user_payload = naming + "\n\n---\n\n" + "\n\n---\n\n".join(
            _format_unit(row, previous.get(str(row["id"]))) for row in rows
        )
        doc, _raw = await chat_json(
            llm_config=llm_config,
            messages=[
                {"role": "system", "content": (system or "").strip()},
                {"role": "user", "content": user_payload},
            ],
            max_tokens=1500,
            temperature=0.2,
            response_format={"type": "json_object"},
            fallback={"facts": []},
        )
        facts = doc.get("facts") if isinstance(doc, dict) else []
        if not isinstance(facts, list):
            facts = []
        started = await conn.fetchval("SELECT clock_timestamp()")
        raw_result = await conn.fetchval(
            "SELECT apply_conscious_extraction($1::uuid[], $2::jsonb)",
            unit_ids,
            json.dumps(facts),
        )
        result = json.loads(raw_result) if isinstance(raw_result, str) else (raw_result or {})
        superseded = await _supersede_updated_facts(conn, facts, started)
        return {"skipped": False, **result, **({"superseded": superseded} if superseded else {})}
    except Exception as exc:
        logger.error("conscious extraction failed for %d units: %s", len(unit_ids), exc)
        try:
            await conn.fetchval(
                "SELECT fail_conscious_extraction($1::uuid[], $2::text)",
                unit_ids,
                str(exc),
            )
        except Exception:
            logger.exception("could not record extraction failure")
        return {"skipped": False, "failed_units": len(unit_ids), "error": str(exc)}
