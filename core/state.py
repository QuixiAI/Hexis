from __future__ import annotations

import json
from typing import Any


def _coerce_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


async def run_heartbeat(conn) -> str | None:
    return await conn.fetchval("SELECT run_heartbeat()")


async def apply_heartbeat_decision(
    conn,
    *,
    heartbeat_id: str,
    decision: dict[str, Any],
    start_index: int,
) -> dict[str, Any]:
    raw = await conn.fetchval(
        "SELECT apply_heartbeat_decision($1::uuid, $2::jsonb, $3::int)",
        heartbeat_id,
        json.dumps(decision),
        start_index,
    )
    return _coerce_json(raw)


async def run_maintenance_if_due(conn, stats_hint: dict[str, Any] | None = None) -> dict[str, Any] | None:
    raw = await conn.fetchval(
        "SELECT run_maintenance_if_due($1::jsonb)",
        json.dumps(stats_hint or {}),
    )
    if raw is None:
        return None
    return _coerce_json(raw)


async def should_run_subconscious_decider(conn) -> bool:
    return bool(await conn.fetchval("SELECT should_run_subconscious_decider()"))


async def mark_subconscious_decider_run(conn) -> None:
    await conn.execute("SELECT mark_subconscious_decider_run()")


async def is_agent_terminated(conn) -> bool:
    return bool(await conn.fetchval("SELECT is_agent_terminated()"))
