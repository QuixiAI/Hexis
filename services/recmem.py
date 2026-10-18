"""RecMem nearline and consolidation workers."""

from __future__ import annotations

import json
import logging
from typing import Any

from core.chat_priority import wait_for_chat_idle
from core.llm_config import load_llm_config
from core.llm_json import chat_json
from services.prompt_resources import (
    load_recmem_episode_create_prompt,
    load_recmem_episode_merge_prompt,
    load_recmem_semantic_refine_prompt,
)

logger = logging.getLogger("recmem")


def _coerce_json(val: Any) -> Any:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return val
    return val


def _as_list(val: Any) -> list[Any]:
    if isinstance(val, list):
        return val
    return []


async def run_recmem_embed_step(conn) -> dict[str, Any]:
    """Claim and embed one batch of raw units."""
    batch_size = await conn.fetchval("SELECT COALESCE(get_config_int('memory.recmem_embed_batch_size'), 32)")
    raw = await conn.fetchval("SELECT claim_recmem_unembedded_batch($1::int)", int(batch_size or 32))
    items = _as_list(_coerce_json(raw))
    if not items:
        return {"skipped": True, "reason": "no_unembedded_units"}

    embedded = 0
    failed = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        unit_id = item.get("unit_id")
        content = item.get("content") or ""
        try:
            await conn.fetchval("SELECT ensure_recmem_embedding_chunks($1::uuid)", str(unit_id))
            raw_chunks = await conn.fetchval("SELECT claim_recmem_embedding_chunks($1::uuid)", str(unit_id))
            chunks = _as_list(_coerce_json(raw_chunks))
            chunk_ids: list[str] = []
            chunk_contents: list[str] = []
            for chunk in chunks:
                if not isinstance(chunk, dict):
                    continue
                chunk_id = chunk.get("chunk_id")
                chunk_content = chunk.get("content") or ""
                if chunk_id:
                    chunk_ids.append(str(chunk_id))
                    chunk_contents.append(chunk_content)

            if not chunk_ids:
                raise RuntimeError("no RecMem embedding chunks claimed")

            try:
                await conn.fetchval(
                    "SELECT embed_claimed_recmem_chunks($1::uuid[], $2::text[])",
                    chunk_ids,
                    chunk_contents,
                )
            except Exception as exc:
                for chunk_id in chunk_ids:
                    await conn.fetchval("SELECT fail_recmem_embedding_chunk($1::uuid, $2::text)", chunk_id, str(exc))
                raise RuntimeError(f"RecMem embedding chunk batch failed: {exc}") from exc

            finalized_raw = await conn.fetchval("SELECT finalize_recmem_unit_embedding($1::uuid)", str(unit_id))
            finalized = _coerce_json(finalized_raw)
            if isinstance(finalized, dict) and finalized.get("status") == "embedded":
                embedded += 1
            else:
                raise RuntimeError(f"RecMem unit embedding did not finalize: {finalized}")
        except Exception as exc:
            failed += 1
            logger.warning(
                "RecMem embedding failed: unit=%s chars=%s error=%s",
                unit_id,
                len(content),
                exc,
            )
            await conn.fetchval("SELECT fail_recmem_embedding($1::uuid, $2::text)", str(unit_id), str(exc))

    return {"claimed": len(items), "embedded": embedded, "failed": failed}


async def run_recmem_route_step(conn) -> dict[str, Any]:
    """Claim and route one batch of embedded raw units."""
    batch_size = await conn.fetchval("SELECT COALESCE(get_config_int('memory.recmem_route_batch_size'), 32)")
    raw = await conn.fetchval("SELECT claim_recmem_unrouted_batch($1::int)", int(batch_size or 32))
    items = _as_list(_coerce_json(raw))
    if not items:
        return {"skipped": True, "reason": "no_unrouted_units"}

    outcomes: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        unit_id = item.get("unit_id")
        try:
            result_raw = await conn.fetchval("SELECT recmem_route_unit($1::uuid)", str(unit_id))
            result = _coerce_json(result_raw)
            status = result.get("status", "unknown") if isinstance(result, dict) else "unknown"
            outcomes[status] = outcomes.get(status, 0) + 1
        except Exception as exc:
            await conn.fetchval("SELECT fail_recmem_routing($1::uuid, $2::text)", str(unit_id), str(exc))
            outcomes["failed"] = outcomes.get("failed", 0) + 1

    return {"claimed": len(items), "outcomes": outcomes}


async def run_recmem_sweep_step(conn) -> dict[str, Any]:
    raw = await conn.fetchval("SELECT recmem_periodic_sweep()")
    result = _coerce_json(raw)
    return dict(result) if isinstance(result, dict) else {}


async def _naming(conn) -> str:
    """Who is remembering, and who the user is, so episodes are written in the
    agent's own voice and name people (the DB's label resolution)."""
    raw = await conn.fetchval("SELECT get_turn_labels()")
    labels = _coerce_json(raw) if raw is not None else {}
    labels = labels if isinstance(labels, dict) else {}
    me = labels.get("agent_label") or "Assistant"
    user = labels.get("user_label") or "the user"
    return (
        f"I am {me}; lines labelled \"{me}:\" are what I said. The person I live with is {user}; "
        "other speakers are named in their turns.\n\n"
    )


async def _load_task_context(conn, task: dict[str, Any]) -> dict[str, Any]:
    raw = await conn.fetchval("SELECT load_recmem_task_context($1::uuid)", str(task["id"]))
    context = _coerce_json(raw)
    return dict(context) if isinstance(context, dict) else {"task": task, "sources": [], "target_memory": None}


async def _handle_episode_merge(conn, task: dict[str, Any], llm_config: dict[str, Any]) -> dict[str, Any]:
    context = await _load_task_context(conn, task)
    await wait_for_chat_idle(conn)  # chat comes first
    doc, _raw = await chat_json(
        llm_config=llm_config,
        messages=[
            {"role": "system", "content": load_recmem_episode_merge_prompt().strip()},
            {"role": "user", "content": await _naming(conn) + json.dumps(context, default=str)[:20000]},
        ],
        max_tokens=1800,
        temperature=0.1,
        response_format={"type": "json_object"},
        fallback={"should_merge": False},
    )
    if not isinstance(doc, dict):
        doc = {"should_merge": False}
    result_raw = await conn.fetchval(
        "SELECT apply_recmem_episode_merge($1::uuid, $2::text, $3::boolean)",
        str(task["id"]),
        doc.get("content"),
        bool(doc.get("should_merge", False)),
    )
    result = _coerce_json(result_raw)
    return dict(result) if isinstance(result, dict) else {}


async def _handle_episode_create(conn, task: dict[str, Any], llm_config: dict[str, Any]) -> dict[str, Any]:
    context = await _load_task_context(conn, task)
    await wait_for_chat_idle(conn)  # chat comes first
    doc, _raw = await chat_json(
        llm_config=llm_config,
        messages=[
            {"role": "system", "content": load_recmem_episode_create_prompt().strip()},
            {"role": "user", "content": await _naming(conn) + json.dumps(context, default=str)[:24000]},
        ],
        max_tokens=2200,
        temperature=0.1,
        response_format={"type": "json_object"},
        fallback={"episodes": []},
    )
    normalized = await conn.fetchval("SELECT normalize_recmem_episode_output($1::jsonb)", json.dumps(doc or {}))
    episodes = _coerce_json(normalized)
    result_raw = await conn.fetchval(
        "SELECT apply_recmem_episode_create($1::uuid, $2::jsonb)",
        str(task["id"]),
        json.dumps(_as_list(episodes)),
    )
    result = _coerce_json(result_raw)
    return dict(result) if isinstance(result, dict) else {}


async def _handle_semantic_refine(conn, task: dict[str, Any], llm_config: dict[str, Any]) -> dict[str, Any]:
    context = await _load_task_context(conn, task)
    await wait_for_chat_idle(conn)  # chat comes first
    doc, _raw = await chat_json(
        llm_config=llm_config,
        messages=[
            {"role": "system", "content": load_recmem_semantic_refine_prompt().strip()},
            {"role": "user", "content": await _naming(conn) + json.dumps(context, default=str)[:24000]},
        ],
        max_tokens=1800,
        temperature=0.1,
        response_format={"type": "json_object"},
        fallback={"facts": []},
    )
    normalized = await conn.fetchval("SELECT normalize_recmem_fact_output($1::jsonb)", json.dumps(doc or {}))
    facts = _coerce_json(normalized)
    result_raw = await conn.fetchval(
        "SELECT apply_recmem_semantic_facts($1::uuid, $2::jsonb)",
        str(task["id"]),
        json.dumps(_as_list(facts)),
    )
    result = _coerce_json(result_raw)
    return dict(result) if isinstance(result, dict) else {}


async def run_recmem_consolidation_step(conn) -> dict[str, Any]:
    raw = await conn.fetchval("SELECT claim_recmem_consolidation_task()")
    task = _coerce_json(raw)
    if not isinstance(task, dict) or not task.get("id"):
        return {"skipped": True, "reason": "no_pending_tasks"}

    try:
        llm_config = await load_llm_config(conn, "llm.recmem", fallback_key="llm.subconscious")
        task_type = task.get("task_type")
        if task_type == "episode_merge":
            result = await _handle_episode_merge(conn, task, llm_config)
        elif task_type == "episode_create":
            result = await _handle_episode_create(conn, task, llm_config)
        elif task_type == "semantic_refine":
            result = await _handle_semantic_refine(conn, task, llm_config)
        else:
            raise ValueError(f"unknown RecMem task type: {task_type}")
        logger.info("RecMem task completed: %s", result)
        return result
    except Exception as exc:
        logger.error("RecMem task failed: task=%s error=%s", task.get("id"), exc)
        await conn.fetchval("SELECT fail_recmem_consolidation_task($1::uuid, $2::text)", str(task["id"]), str(exc))
        return {"error": str(exc), "task_id": str(task["id"])}
