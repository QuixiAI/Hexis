"""Reconsolidation sweep service.

After a worldview belief transforms, this service re-evaluates memories that
were connected to the old belief using batched LLM calls.  Two directions:

  1. CONTESTED_BECAUSE → belief: rejected because of old belief, may now accept.
  2. SUPPORTS → belief: supported old belief, may now contradict.

Called by the maintenance worker when ``has_pending_reconsolidation()`` is true.
Follows the same pattern as ``services/subconscious.py``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.chat_priority import wait_for_chat_idle
from core.llm_config import load_llm_config
from core.llm_json import chat_json
from services.prompt_resources import load_reconsolidation_prompt

logger = logging.getLogger("reconsolidation")

BATCH_SIZE = 8  # memories per LLM call


def _coerce_json(val: Any) -> Any:
    """Parse a JSON string into a Python object if needed."""
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return val
    return val


def _normalize_verdicts(raw: Any) -> list[dict[str, Any]]:
    """Extract and validate the verdicts array from the LLM response."""
    if not isinstance(raw, dict):
        return []
    verdicts = raw.get("verdicts", [])
    if not isinstance(verdicts, list):
        return []
    valid = []
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        if "memory_id" not in v or "verdict" not in v:
            continue
        if v["verdict"] not in ("accept", "still_contested", "newly_contested", "keep"):
            continue
        valid.append(v)
    return valid


async def run_reconsolidation_step(conn) -> dict[str, Any]:
    """Process one reconsolidation task (all batches within it).

    Called by the maintenance worker each tick when pending tasks exist.
    Returns a summary dict describing what happened.
    """
    # 1. Claim a task
    raw = await conn.fetchval("SELECT claim_reconsolidation_task()")
    if raw is None:
        return {"skipped": True, "reason": "no_pending_tasks"}

    task = _coerce_json(raw)
    if not isinstance(task, dict) or "task_id" not in task:
        return {"skipped": True, "reason": "invalid_task"}

    task_id = str(task["task_id"])
    belief_id = str(task["belief_id"])
    old_content = task["old_content"]
    new_content = task["new_content"]

    # 2. Load LLM config
    try:
        llm_config = await load_llm_config(
            conn, "llm.reconsolidation", fallback_key="llm.heartbeat",
        )
    except Exception as exc:
        await conn.execute(
            "SELECT fail_reconsolidation($1::uuid, $2)",
            task_id, f"llm_config_error: {exc}",
        )
        return {"error": str(exc), "task_id": task_id}

    system_prompt = load_reconsolidation_prompt().strip()
    offset = 0
    total_processed = 0

    try:
        while True:
            # 3. Fetch candidates in batches
            raw_candidates = await conn.fetchval(
                "SELECT get_reconsolidation_candidates($1::uuid, $2, $3)",
                belief_id, BATCH_SIZE, offset,
            )
            candidates = _coerce_json(raw_candidates)
            if not isinstance(candidates, dict):
                break

            contested = candidates.get("contested_candidates", [])
            if not isinstance(contested, list):
                contested = []
            supporting = candidates.get("supporting_candidates", [])
            if not isinstance(supporting, list):
                supporting = []
            batch = contested + supporting

            if not batch:
                break

            # 4. Build LLM prompt for this batch
            user_prompt = json.dumps({
                "old_belief": old_content,
                "new_belief": new_content,
                "memories": [
                    {
                        "memory_id": str(m["id"]),
                        "content": m["content"],
                        "type": m.get("type", "semantic"),
                        "trust_level": m.get("trust_level", 0.5),
                        "direction": m.get("direction", "unknown"),
                        "is_contested": m.get("is_contested", "false"),
                    }
                    for m in batch
                ],
            })

            # 5. LLM call
            await wait_for_chat_idle(conn)  # chat comes first
            doc, _raw_response = await chat_json(
                llm_config=llm_config,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=2000,
                temperature=0.1,
                response_format={"type": "json_object"},
                fallback={"verdicts": []},
            )

            verdicts = _normalize_verdicts(doc)

            # 6. Apply verdicts in DB
            if verdicts:
                await conn.fetchval(
                    "SELECT apply_reconsolidation_verdict($1::uuid, $2::jsonb)",
                    task_id, json.dumps(verdicts),
                )

            total_processed += len(batch)
            offset += BATCH_SIZE

            # If batch was smaller than BATCH_SIZE, we've exhausted candidates
            if len(batch) < BATCH_SIZE:
                break

        # 7. Complete the task
        raw_result = await conn.fetchval(
            "SELECT complete_reconsolidation($1::uuid)", task_id,
        )
        result = _coerce_json(raw_result)
        logger.info("Reconsolidation completed: task=%s result=%s", task_id, result)
        return {"completed": True, "task_id": task_id, "result": result}

    except Exception as exc:
        logger.error("Reconsolidation failed: task=%s error=%s", task_id, exc)
        await conn.execute(
            "SELECT fail_reconsolidation($1::uuid, $2)",
            task_id, str(exc),
        )
        return {"error": str(exc), "task_id": task_id}
