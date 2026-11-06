from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _coerce_json(val: Any) -> Any:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return val
    return val


def character_block(profile: Any) -> str:
    """Who the appraisal is for, in a line.

    The appraiser is told whose gut it is (name and description), so its
    instincts come out as that character's reactions rather than a generic
    assistant's. The subconscious is low-level work (salience, emotion, what
    to remember and retrieve), so it gets the name and description only; the
    voice belongs to the conscious reply.
    """
    profile = _coerce_json(profile)
    persona = profile.get("persona") if isinstance(profile, dict) else None
    if not isinstance(persona, dict):
        return ""
    name = str(persona.get("name") or "").strip()
    if not name:
        return ""
    description = str(persona.get("description") or "").strip()
    who = f"{name}: {description}" if description else name
    return (
        f"You are the subconscious of {who}\n"
        "Instincts are signals about what matters, not scripts for the reply: "
        "never suggest how to phrase it, and never suggest presenting a menu or overview of capabilities."
    )


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
