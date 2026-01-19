from __future__ import annotations

import json
from typing import Any


def _coerce_json(val: Any) -> Any:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return val
    return val


async def get_subconscious_context(conn) -> dict[str, Any]:
    raw = await conn.fetchval("SELECT get_subconscious_context()")
    context = _coerce_json(raw) if raw is not None else {}
    return context if isinstance(context, dict) else {}


async def apply_subconscious_observations(conn, observations: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    raw = await conn.fetchval(
        "SELECT apply_subconscious_observations($1::jsonb)",
        json.dumps(observations),
    )
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {"error": raw}
    return dict(raw) if isinstance(raw, dict) else {"result": raw}
