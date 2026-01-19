from __future__ import annotations

import json
from typing import Any

from core.llm_config import load_llm_config
from core.llm_json import chat_json
from core.subconscious import apply_subconscious_observations, get_subconscious_context
from services.prompt_resources import load_subconscious_prompt


def _normalize_observations(doc: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    def _as_list(val: Any) -> list[dict[str, Any]]:
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


def _coerce_json(val: Any) -> Any:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return val
    return val


async def _build_context(conn) -> dict[str, Any]:
    raw = await get_subconscious_context(conn)
    context = _coerce_json(raw) if raw is not None else {}
    return context if isinstance(context, dict) else {}


async def run_subconscious_decider(conn) -> dict[str, Any]:
    llm_config = await load_llm_config(conn, "llm.subconscious", fallback_key="llm.heartbeat")
    context = await _build_context(conn)
    user_prompt = f"Context (JSON):\n{json.dumps(context)[:12000]}"
    try:
        doc, raw = await chat_json(
            llm_config=llm_config,
            messages=[
                {"role": "system", "content": load_subconscious_prompt().strip()},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1800,
            response_format={"type": "json_object"},
            fallback={},
        )
    except Exception as exc:
        return {"skipped": True, "reason": str(exc)}

    if not isinstance(doc, dict):
        doc = {}

    observations = _normalize_observations(doc)
    applied = await apply_subconscious_observations(conn, observations)
    return {"applied": applied, "raw_response": raw}
