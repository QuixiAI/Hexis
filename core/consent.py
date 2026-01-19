from __future__ import annotations

import json
from typing import Any


async def get_consent_status(conn) -> str | None:
    try:
        status = await conn.fetchval("SELECT get_agent_consent_status()")
    except Exception:
        return None
    return status if isinstance(status, str) else None


async def is_consent_granted(conn) -> bool:
    status = await get_consent_status(conn)
    return isinstance(status, str) and status.strip().lower() == "consent"


async def record_consent_response(conn, payload: dict[str, Any]) -> dict[str, Any]:
    raw = await conn.fetchval(
        "SELECT record_consent_response($1::jsonb)",
        json.dumps(payload),
    )
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    return raw if isinstance(raw, dict) else {}
