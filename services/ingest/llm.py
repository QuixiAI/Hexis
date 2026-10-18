"""Hexis ingestion — split from the former services/ingest.py (#89).
Module: llm.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from uuid import UUID

from .config import Appraisal, Config, DocumentInfo, Extraction, IngestionMode, Section

# =========================================================================
# LLM CLIENT
# =========================================================================


class IngestLLM:
    """Thin LLM wrapper for the ingestion pipeline.

    Uses core.llm.chat_completion() under the hood, supporting all
    configured providers (OpenAI, Anthropic, Codex, Gemini, etc.).
    """

    def __init__(self, config: Config):
        from core.llm import normalize_llm_config

        self._cfg = normalize_llm_config(config.llm_config)
        self._config_ref = config
        self.call_count = 0
        # Set by the job runner: awaited before every model call so reading
        # yields to a live chat turn (core.chat_priority).
        self.before_call: Callable[[], Awaitable[Any]] | None = None

    async def complete(self, messages: list[dict[str, str]], temperature: float = 0.3) -> str:
        """Async completion via core.llm (#88: one async path, no sync twin)."""
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
            max_tokens=1200,
            auth_mode=self._cfg.get("auth_mode"),
        )
        await record_llm_usage(
            provider=self._cfg["provider"],
            model=self._cfg["model"],
            raw_response=result.get("raw"),
            source="ingest",
        )
        return result.get("content", "")

    async def complete_json(self, messages: list[dict[str, str]], temperature: float = 0.2) -> dict[str, Any]:
        # One (config-owned) re-ask when the completion parses to nothing —
        # transient HTTP/network retry already lives at the provider layer.
        retries = max(0, int(getattr(self._config_ref, "llm_json_retries", 1)))
        for attempt in range(retries + 1):
            text = await self.complete(messages, temperature=temperature)
            parsed = self._parse_json(text)
            if parsed or attempt >= retries:
                return parsed
        return {}

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        json_text = text.strip()
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", json_text, re.DOTALL)
        if match:
            json_text = match.group(1).strip()
        if not json_text.startswith("{"):
            start = json_text.find("{")
            if start != -1:
                json_text = json_text[start:]
        try:
            data = json.loads(json_text)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}


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
            " valence (-1..1), arousal (0..1), primary_emotion (string), intensity (0..1),"
            " goal_relevance (array of {goal, strength}), worldview_tension (0..1), curiosity (0..1),"
            " summary (2-3 sentences)."
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    @staticmethod
    def _parse(raw: dict[str, Any]) -> Appraisal:
        return Appraisal(
            valence=float(raw.get("valence", 0.0) or 0.0),
            arousal=float(raw.get("arousal", 0.3) or 0.3),
            primary_emotion=str(raw.get("primary_emotion", "neutral") or "neutral"),
            intensity=float(raw.get("intensity", 0.0) or 0.0),
            goal_relevance=list(raw.get("goal_relevance", []) or []),
            worldview_tension=float(raw.get("worldview_tension", 0.0) or 0.0),
            curiosity=float(raw.get("curiosity", 0.0) or 0.0),
            summary=str(raw.get("summary", "") or ""),
        )

    async def appraise(self, *, content: str, context: dict[str, Any], mode: IngestionMode) -> Appraisal:
        msgs = self._build_messages(content, context)
        raw = await self.llm.complete_json(msgs, temperature=0.2)
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
            "  - concepts: array of key concept/entity names this knowledge is an instance of\n"
            "Keep at most "
            + str(max_items)
            + " items."
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    @staticmethod
    def _parse(raw: dict[str, Any], max_items: int) -> list[Extraction]:
        items = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return []
        out: list[Extraction] = []
        for item in items[:max_items]:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "") or "").strip()
            if not content:
                continue
            out.append(
                Extraction(
                    content=content,
                    category=str(item.get("category", "fact") or "fact"),
                    confidence=float(item.get("confidence", 0.5) or 0.5),
                    importance=float(item.get("importance", 0.5) or 0.5),
                    why=str(item.get("why", "") or "") or None,
                    connections=[str(c).strip() for c in (item.get("connections") or []) if str(c).strip()],
                    supports=item.get("supports"),
                    contradicts=item.get("contradicts"),
                    concepts=[str(c).strip() for c in (item.get("concepts") or []) if str(c).strip()],
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
        raw = await self.llm.complete_json(msgs, temperature=0.3)
        return self._parse(raw, max_items)
