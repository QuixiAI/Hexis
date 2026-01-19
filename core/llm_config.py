from __future__ import annotations

import json
import os
from typing import Any

from core.llm import normalize_llm_config


DEFAULT_LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")
DEFAULT_LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")


async def load_llm_config(
    conn,
    key: str,
    *,
    default_provider: str = DEFAULT_LLM_PROVIDER,
    default_model: str = DEFAULT_LLM_MODEL,
    fallback_key: str | None = None,
) -> dict[str, Any]:
    cfg = await conn.fetchval("SELECT get_config($1)", key)
    if cfg is None and fallback_key:
        cfg = await conn.fetchval("SELECT get_config($1)", fallback_key)

    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except Exception:
            cfg = None

    if not isinstance(cfg, dict):
        cfg = {}

    if "provider" not in cfg:
        cfg["provider"] = default_provider
    if "model" not in cfg:
        cfg["model"] = default_model

    return normalize_llm_config(cfg, default_model=default_model)
