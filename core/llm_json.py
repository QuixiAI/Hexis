from __future__ import annotations

import json
import re
from typing import Any

from core.llm import chat_completion


def extract_json_object(text: str) -> dict[str, Any]:
    if not text:
        return {}
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    snippet = text[start : end + 1]
    try:
        doc = json.loads(snippet)
    except Exception:
        return {}
    return doc if isinstance(doc, dict) else {}


def parse_json_response(raw: str, fallback: dict[str, Any]) -> dict[str, Any]:
    if not raw:
        return dict(fallback)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return dict(fallback)


async def chat_json(
    *,
    llm_config: dict[str, Any],
    messages: list[dict[str, Any]],
    max_tokens: int,
    temperature: float = 0.2,
    response_format: dict[str, Any] | None = None,
    fallback: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    fallback = fallback or {}
    response = await chat_completion(
        provider=llm_config["provider"],
        model=llm_config["model"],
        endpoint=llm_config.get("endpoint"),
        api_key=llm_config.get("api_key"),
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=response_format,
    )
    raw = response.get("content", "") or ""
    return parse_json_response(raw, fallback), raw
