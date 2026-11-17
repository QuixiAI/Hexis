"""Hexis ingestion — split from the former services/ingest.py (#89).
Module: llm.

Every model call here has a declared output schema. The provider is asked to
constrain generation to it (core.llm forwards the schema to OpenAI, vLLM,
SGLang, Anthropic and Gemini in each one's native form), and the result is
validated once, centrally, below. Only the fact text is load-bearing; every
score is auxiliary and falls back to a default when a model still manages to
send something unusable. Output that cannot be parsed at all is an error the
caller sees, never a silently empty result.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable

from core.llm_json import extract_json_object

from .config import Appraisal, Config, DocumentInfo, Extraction, IngestionMode, Section

logger = logging.getLogger(__name__)


class IngestLLMOutputError(RuntimeError):
    """The model answered, but not with the JSON object the call asked for."""


# =========================================================================
# OUTPUT SCHEMAS
# =========================================================================

def _score(low: float, high: float) -> dict[str, Any]:
    return {"type": "number", "minimum": low, "maximum": high}


def _nullable_string() -> dict[str, Any]:
    return {"type": ["string", "null"]}


def _string_list() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}}


APPRAISAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "valence": _score(-1, 1),
        "arousal": _score(0, 1),
        "primary_emotion": {"type": "string"},
        "intensity": _score(0, 1),
        "goal_relevance": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"goal": {"type": "string"}, "strength": _score(0, 1)},
                "required": ["goal", "strength"],
                "additionalProperties": False,
            },
        },
        "worldview_tension": _score(0, 1),
        "curiosity": _score(0, 1),
        "summary": {"type": "string"},
    },
    "required": [
        "valence", "arousal", "primary_emotion", "intensity",
        "goal_relevance", "worldview_tension", "curiosity", "summary",
    ],
    "additionalProperties": False,
}

EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "category": {"type": "string"},
                    "confidence": _score(0, 1),
                    "importance": _score(0, 1),
                    "why": _nullable_string(),
                    "connections": _string_list(),
                    "supports": _nullable_string(),
                    "contradicts": _nullable_string(),
                    "concepts": _string_list(),
                },
                "required": [
                    "content", "category", "confidence", "importance", "why",
                    "connections", "supports", "contradicts", "concepts",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def response_format_for(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """The OpenAI-style response_format core.llm understands for every provider."""
    return {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}


# =========================================================================
# VALIDATION
# =========================================================================

_WORD_SCORES = {
    "none": 0.0, "very low": 0.1, "low": 0.25, "medium": 0.5, "moderate": 0.5,
    "high": 0.75, "very high": 0.9,
}


def _float_in(value: Any, low: float, high: float, default: float, *, field: str) -> float:
    """A score the schema bounded, or the nearest honest reading of what a
    server that ignored the schema sent back: a word from a small scale
    ("low"), a numeric string, or a 1-10 score (rescaled to the unit range).
    Anything else is defaulted, not guessed."""
    if isinstance(value, str) and value.strip().lower() in _WORD_SCORES:
        return min(max(_WORD_SCORES[value.strip().lower()], low), high)
    if isinstance(value, bool):
        number = None
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        try:
            number = float(value.strip()) if isinstance(value, str) and value.strip() else None
        except ValueError:
            number = None
    if number is None:
        if value is not None:
            logger.warning("ingest: %s=%r is not a score; using %.2f", field, value, default)
        return default
    if number > high and number <= 10 and number.is_integer():
        number = number / 10  # a whole number past the range is a 1-10 score
    return min(max(number, low), high)


def _unit(value: Any, default: float, *, field: str) -> float:
    return _float_in(value, 0.0, 1.0, default, field=field)


def _text(value: Any, default: str = "") -> str:
    return value.strip() if isinstance(value, str) and value.strip() else default


def _optional_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


# =========================================================================
# LLM CLIENT
# =========================================================================


class IngestLLM:
    """Thin LLM wrapper for the ingestion pipeline.

    Uses core.llm.chat_completion() under the hood, supporting all
    configured providers (OpenAI, Anthropic, Gemini, Grok, OpenAI-compatible).
    """

    def __init__(self, config: Config):
        from core.llm import normalize_llm_config

        self._cfg = normalize_llm_config(config.llm_config)
        self._config_ref = config
        self.call_count = 0
        # Set by the job runner: awaited before every model call so reading
        # yields to a live chat turn (core.chat_priority).
        self.before_call: Callable[[], Awaitable[Any]] | None = None

    @property
    def reasoning_effort(self) -> str | None:
        return str(getattr(self._config_ref, "llm_reasoning_effort", None) or "").strip() or None

    @property
    def max_tokens(self) -> int:
        return int(getattr(self._config_ref, "llm_max_tokens", 8000))

    async def complete_with_meta(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        *,
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Async completion via core.llm (#88: one async path, no sync twin).

        Returns core.llm's result dict: content, tool_calls, raw, finish_reason.
        """
        from core.llm import chat_completion
        from core.usage import record_llm_usage

        if self.before_call is not None:
            await self.before_call()
        self.call_count += 1
        result = await chat_completion(
            provider=self._cfg["provider"],
            model=self._cfg["model"],
            endpoint=self._cfg.get("endpoint"),
            api_key=self._cfg.get("api_key"),
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens or self.max_tokens,
            auth_mode=self._cfg.get("auth_mode"),
            response_format=response_format,
            reasoning_effort=self.reasoning_effort,
        )
        await record_llm_usage(
            provider=self._cfg["provider"],
            model=self._cfg["model"],
            raw_response=result.get("raw"),
            source="ingest",
        )
        return result

    async def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        *,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        result = await self.complete_with_meta(messages, temperature, response_format=response_format)
        return result.get("content", "")

    async def complete_json(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        *,
        schema: dict[str, Any],
        name: str = "result",
    ) -> dict[str, Any]:
        """Ask for an object matching `schema`; raise if the model never produces one.

        One (config-owned) re-ask when the completion parses to nothing —
        transient HTTP/network retry already lives at the provider layer.
        """
        retries = max(0, int(getattr(self._config_ref, "llm_json_retries", 1)))
        fmt = response_format_for(name, schema)
        budget = self.max_tokens
        text = ""
        reason = ""
        for attempt in range(retries + 1):
            result = await self.complete_with_meta(
                messages, temperature=temperature, response_format=fmt, max_tokens=budget,
            )
            text = result.get("content", "") or ""
            parsed = self._parse_json(text)
            if parsed is not None:
                return parsed
            truncated = result.get("finish_reason") == "length"
            reason = f"truncated at {budget} output tokens" if truncated else "not a JSON object"
            logger.warning(
                "ingest: %s output %s (attempt %d/%d): %r",
                name, reason, attempt + 1, retries + 1, text[-160:],
            )
            if truncated:
                # Reasoning models spend the budget thinking; give the re-ask
                # room instead of asking the same question the same way.
                budget = min(budget * 2, self.max_tokens * 4)
        raise IngestLLMOutputError(f"{name}: model output {reason} ({text[-120:]!r})")

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any] | None:
        """The JSON object in a completion, or None when there is none.

        Tolerates a code fence and prose around the object; an empty object is
        a valid (if useless) answer and is returned as-is.
        """
        json_text = (text or "").strip()
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", json_text, re.DOTALL)
        if match:
            json_text = match.group(1).strip()
        try:
            data = json.loads(json_text)
        except Exception:
            data = extract_json_object(json_text) or None
        return data if isinstance(data, dict) else None


# =========================================================================
# APPRAISAL + EXTRACTION
# =========================================================================


class Appraiser:
    def __init__(self, llm: IngestLLM):
        self.llm = llm

    @staticmethod
    def _build_messages(content: str, context: dict[str, Any]) -> list[dict[str, str]]:
        system = (
            "You are Hexis' subconscious appraisal system."
            " Provide a brief, honest emotional assessment of the content."
            " If you feel nothing, say so and keep intensity low."
            " Return STRICT JSON only."
        )
        user = (
            "CONTENT SAMPLE:\n"
            f"{content}\n\n"
            "CONTEXT (JSON):\n"
            f"{json.dumps(context)[:8000]}\n\n"
            "Return JSON with keys:"
            " valence (number -1..1), arousal (number 0..1), primary_emotion (string), intensity (number 0..1),"
            " goal_relevance (array of {goal, strength}), worldview_tension (number 0..1), curiosity (number 0..1),"
            " summary (2-3 sentences). Every score is a decimal number, never a word."
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    @staticmethod
    def _parse(raw: dict[str, Any]) -> Appraisal:
        goals = []
        for entry in raw.get("goal_relevance") or []:
            if isinstance(entry, dict) and _optional_text(entry.get("goal")):
                goals.append({
                    "goal": _text(entry.get("goal")),
                    "strength": _unit(entry.get("strength"), 0.5, field="goal_relevance.strength"),
                })
        return Appraisal(
            valence=_float_in(raw.get("valence"), -1.0, 1.0, 0.0, field="valence"),
            arousal=_unit(raw.get("arousal"), 0.3, field="arousal"),
            primary_emotion=_text(raw.get("primary_emotion"), "neutral"),
            intensity=_unit(raw.get("intensity"), 0.0, field="intensity"),
            goal_relevance=goals,
            worldview_tension=_unit(raw.get("worldview_tension"), 0.0, field="worldview_tension"),
            curiosity=_unit(raw.get("curiosity"), 0.0, field="curiosity"),
            summary=_text(raw.get("summary")),
        )

    async def appraise(self, *, content: str, context: dict[str, Any], mode: IngestionMode) -> Appraisal:
        msgs = self._build_messages(content, context)
        raw = await self.llm.complete_json(msgs, temperature=0.2, schema=APPRAISAL_SCHEMA, name="appraisal")
        return self._parse(raw)


class KnowledgeExtractor:
    def __init__(self, llm: IngestLLM):
        self.llm = llm

    @staticmethod
    def _build_messages(
        section: Section, doc: DocumentInfo, appraisal: Appraisal, mode: IngestionMode, max_items: int,
    ) -> list[dict[str, str]]:
        system = (
            "You extract standalone knowledge worth remembering."
            " Be selective. Return STRICT JSON only."
        )
        if doc.source_type == "code":
            guidance = (
                "Focus on what the code does, key interfaces, behaviors, patterns,"
                " and any important constraints or dependencies."
            )
        elif doc.source_type == "data":
            guidance = (
                "Describe the schema, key fields, relationships, and notable values or patterns."
            )
        else:
            guidance = (
                "Extract facts, claims, definitions, procedures, insights, and statistics."
            )
        user = (
            f"DOCUMENT: {doc.title}\n"
            f"SECTION: {section.title}\n"
            f"MODE: {mode.value}\n\n"
            "APPRAISAL:\n"
            f"{json.dumps(appraisal.__dict__, ensure_ascii=False)}\n\n"
            "CONTENT:\n"
            f"{section.extraction_view()}\n\n"
            f"{guidance}\n\n"
            "Return JSON with key 'items' as an array of objects:\n"
            "  {content, category, confidence, importance, why, connections, supports, contradicts, concepts}\n"
            "  - confidence and importance: decimal numbers 0..1, never words\n"
            "  - why, supports, contradicts: string or null\n"
            "  - connections, concepts: arrays of strings; concepts are the key concept/entity names"
            " this knowledge is an instance of\n"
            "Return an empty items array if nothing is worth remembering. Keep at most "
            + str(max_items)
            + " items."
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    @staticmethod
    def _parse(raw: dict[str, Any], max_items: int) -> list[Extraction]:
        items = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            logger.warning("ingest: extraction output had no 'items' array (keys: %s)", sorted(raw)[:8])
            return []
        out: list[Extraction] = []
        for item in items[:max_items]:
            if not isinstance(item, dict):
                continue
            content = _text(item.get("content"))
            if not content:
                continue
            out.append(
                Extraction(
                    content=content,
                    category=_text(item.get("category"), "fact"),
                    confidence=_unit(item.get("confidence"), 0.5, field="confidence"),
                    importance=_unit(item.get("importance"), 0.5, field="importance"),
                    why=_optional_text(item.get("why")),
                    connections=_text_list(item.get("connections")),
                    supports=_optional_text(item.get("supports")),
                    contradicts=_optional_text(item.get("contradicts")),
                    concepts=_text_list(item.get("concepts")),
                )
            )
        return out

    async def extract(
        self,
        *,
        section: Section,
        doc: DocumentInfo,
        appraisal: Appraisal,
        mode: IngestionMode,
        max_items: int,
    ) -> list[Extraction]:
        msgs = self._build_messages(section, doc, appraisal, mode, max_items)
        raw = await self.llm.complete_json(msgs, temperature=0.3, schema=EXTRACTION_SCHEMA, name="extraction")
        return self._parse(raw, max_items)
