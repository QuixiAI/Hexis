from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from core.agent_api import _connect_with_retry, db_dsn_from_env
from core.cognitive_memory_api import CognitiveMemory, MemoryType


def _coerce_json_value(val: Any) -> Any:
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return val
        try:
            return json.loads(s)
        except Exception:
            return val
    return val


async def status_payload(
    dsn: str | None = None,
    *,
    wait_seconds: int = 30,
    include_embedding_health: bool = True,
) -> dict[str, Any]:
    dsn = dsn or db_dsn_from_env()
    conn = await _connect_with_retry(dsn, wait_seconds=wait_seconds)
    try:
        payload: dict[str, Any] = {"dsn": dsn}
        payload["db_time"] = str(await conn.fetchval("SELECT now()"))

        payload["agent_configured"] = bool(await conn.fetchval("SELECT is_agent_configured()"))
        payload["heartbeat_paused"] = bool(await conn.fetchval("SELECT is_paused FROM heartbeat_state WHERE id = 1"))
        payload["should_run_heartbeat"] = bool(await conn.fetchval("SELECT should_run_heartbeat()"))
        try:
            payload["maintenance_paused"] = bool(await conn.fetchval("SELECT is_paused FROM maintenance_state WHERE id = 1"))
            payload["should_run_maintenance"] = bool(await conn.fetchval("SELECT should_run_maintenance()"))
        except Exception:
            payload["maintenance_paused"] = None
            payload["should_run_maintenance"] = None

        payload["pending_external_calls"] = int(
            await conn.fetchval("SELECT COUNT(*) FROM external_calls WHERE status = 'pending'::external_call_status")
        )
        payload["pending_outbox_messages"] = int(
            await conn.fetchval("SELECT COUNT(*) FROM outbox_messages WHERE status = 'pending'")
        )

        payload["embedding_service_url"] = await conn.fetchval("SELECT get_config_text('embedding.service_url')")
        payload["embedding_dimension"] = int(await conn.fetchval("SELECT embedding_dimension()"))

        if include_embedding_health:
            try:
                payload["embedding_service_healthy"] = bool(
                    await conn.fetchval("SELECT check_embedding_service_health()")
                )
            except Exception as exc:
                payload["embedding_service_healthy"] = False
                payload["embedding_service_error"] = repr(exc)

        return payload
    finally:
        await conn.close()


async def config_rows(dsn: str | None = None, *, wait_seconds: int = 30) -> dict[str, Any]:
    dsn = dsn or db_dsn_from_env()
    conn = await _connect_with_retry(dsn, wait_seconds=wait_seconds)
    try:
        rows = await conn.fetch("SELECT key, value FROM config ORDER BY key")
        out: dict[str, Any] = {}
        for r in rows:
            out[str(r["key"])] = _coerce_json_value(r["value"])
        return out
    finally:
        await conn.close()


async def config_validate(dsn: str | None = None, *, wait_seconds: int = 30) -> tuple[list[str], list[str]]:
    dsn = dsn or db_dsn_from_env()
    conn = await _connect_with_retry(dsn, wait_seconds=wait_seconds)
    try:
        errors: list[str] = []
        warnings: list[str] = []

        rows = await conn.fetch("SELECT key, value FROM config ORDER BY key")
        cfg: dict[str, Any] = {str(r["key"]): _coerce_json_value(r["value"]) for r in rows}
        required_keys = [
            "agent.is_configured",
            "agent.objectives",
            "llm.heartbeat",
            "llm.chat",
        ]
        for key in required_keys:
            if key not in cfg:
                errors.append(f"Missing config key: {key}")

        is_conf = cfg.get("agent.is_configured")
        if is_conf is not True:
            if is_conf == "true":
                is_conf = True
        if is_conf is not True:
            errors.append("agent.is_configured is not true (run `hexis init`).")

        objectives = cfg.get("agent.objectives")
        if not isinstance(objectives, list) or not objectives:
            errors.append("agent.objectives must be a non-empty array (run `hexis init`).")

        def _validate_llm(name: str) -> None:
            val = cfg.get(name)
            if not isinstance(val, dict):
                errors.append(f"{name} must be an object (run `hexis init`).")
                return
            provider = str(val.get("provider") or "").strip().lower()
            model = str(val.get("model") or "").strip()
            endpoint = str(val.get("endpoint") or "").strip()
            api_key_env = str(val.get("api_key_env") or "").strip()

            if not provider:
                errors.append(f"{name}.provider is required")
            if not model and provider not in {"ollama"}:
                warnings.append(f"{name}.model is empty (will rely on worker defaults)")

            if provider in {"openai", "anthropic", "openai_compatible"}:
                if api_key_env:
                    if os.getenv(api_key_env) is None:
                        errors.append(f"{name}.api_key_env={api_key_env} is not set in environment")
                else:
                    if not endpoint or ("localhost" not in endpoint and "127.0.0.1" not in endpoint):
                        warnings.append(f"{name}.api_key_env not set (LLM calls may fail)")

        _validate_llm("llm.heartbeat")
        _validate_llm("llm.chat")
        if "llm.subconscious" in cfg:
            _validate_llm("llm.subconscious")

        interval = await conn.fetchval("SELECT get_config_float('heartbeat.heartbeat_interval_minutes')")
        if interval is None or float(interval) <= 0:
            errors.append("heartbeat.heartbeat_interval_minutes must be > 0")

        return errors, warnings
    finally:
        await conn.close()


async def demo(dsn: str | None = None, *, wait_seconds: int = 30) -> dict[str, Any]:
    dsn = dsn or db_dsn_from_env()
    conn = await _connect_with_retry(dsn, wait_seconds=wait_seconds)
    try:
        deadline = time.monotonic() + wait_seconds
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                ok = await conn.fetchval("SELECT check_embedding_service_health()")
                if ok is True:
                    break
            except Exception as exc:  # pragma: no cover (timing-dependent)
                last = exc
            await asyncio.sleep(1)
        else:
            raise TimeoutError(f"Embedding service not healthy after {wait_seconds}s: {last!r}")
    finally:
        await conn.close()

    async with CognitiveMemory.connect(dsn) as mem:
        m1 = await mem.remember("Demo: the user prefers short, direct answers", type=MemoryType.SEMANTIC, importance=0.7)
        m2 = await mem.remember(
            "Demo: the user is working on the Hexis memory system",
            type=MemoryType.EPISODIC,
            importance=0.6,
        )
        held = await mem.hold("Demo: temporary context in working memory", ttl_seconds=600)

        recall = await mem.recall("What do I know about the user's preferences?", limit=5)
        hydrate = await mem.hydrate("Summarize what we know about the user", include_goals=False)
        working_hits = await mem.search_working("temporary context", limit=5)

        return {
            "remembered_ids": [str(m1), str(m2)],
            "working_memory_id": str(held),
            "recall_count": len(recall.memories),
            "hydrate_memory_count": len(hydrate.memories),
            "working_search_count": len(working_hits),
        }
