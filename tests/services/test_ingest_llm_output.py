"""Ingestion model output: schema-constrained, validated once, never silently empty."""

from __future__ import annotations

import logging

import pytest

from services.ingest.config import Appraisal, Config, DocumentInfo, IngestionMode, Section
from services.ingest.llm import (
    APPRAISAL_SCHEMA,
    EXTRACTION_SCHEMA,
    Appraiser,
    IngestLLM,
    IngestLLMOutputError,
    KnowledgeExtractor,
    response_format_for,
)


def test_extraction_parse_defaults_bad_scores_without_guessing(caplog):
    raw = {
        "items": [
            {"content": "Jen owns Aflac dashboard access.", "importance": "low", "confidence": 7},
            {"content": "  ", "importance": 0.9},
            {"content": "Tinybox does 151 tok/s.", "category": "statistic", "importance": 1.4, "confidence": -0.2,
             "why": None, "connections": ["a", 3, " "], "supports": "", "contradicts": None, "concepts": ["tinybox"]},
            "not an object",
        ]
    }
    with caplog.at_level(logging.WARNING):
        items = KnowledgeExtractor._parse(raw, max_items=20)
    assert [i.content for i in items] == ["Jen owns Aflac dashboard access.", "Tinybox does 151 tok/s."]
    first, second = items
    # A word is not a score; it gets the default, not a lookup table.
    assert first.importance == 0.5
    assert first.confidence == 1.0  # numbers are clamped, never rescaled
    assert first.category == "fact"
    assert second.importance == 1.0 and second.confidence == 0.0
    assert second.connections == ["a"] and second.supports is None and second.concepts == ["tinybox"]
    assert "importance='low'" in caplog.text


def test_extraction_parse_respects_max_items_and_missing_items():
    assert KnowledgeExtractor._parse({"facts": []}, max_items=5) == []
    raw = {"items": [{"content": f"fact {i}"} for i in range(10)]}
    assert len(KnowledgeExtractor._parse(raw, max_items=3)) == 3


def test_appraisal_parse_defaults_everything_unusable():
    apr = Appraiser._parse({
        "valence": "positive", "arousal": 5, "primary_emotion": "", "intensity": None,
        "goal_relevance": [{"goal": "sell", "strength": "high"}, {"strength": 0.2}, "x"],
        "worldview_tension": True, "summary": 42,
    })
    assert apr == Appraisal(
        valence=0.0, arousal=1.0, primary_emotion="neutral", intensity=0.0,
        goal_relevance=[{"goal": "sell", "strength": 0.5}], worldview_tension=0.0, curiosity=0.0, summary="",
    )


def test_schemas_bound_every_score():
    def scores(schema):
        props = schema["properties"]
        for key, spec in props.items():
            if spec.get("type") == "number":
                assert spec["minimum"] < spec["maximum"], key
    scores(APPRAISAL_SCHEMA)
    scores(EXTRACTION_SCHEMA["properties"]["items"]["items"])
    assert "content" in EXTRACTION_SCHEMA["properties"]["items"]["items"]["required"]
    fmt = response_format_for("extraction", EXTRACTION_SCHEMA)
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["schema"] is EXTRACTION_SCHEMA


class _Recorder(IngestLLM):
    """Scripted completions: a str reply, or (reply, finish_reason)."""

    def __init__(self, replies: list, retries: int = 1, max_tokens: int = 1000):
        self._cfg = {"provider": "stub", "model": "stub"}
        self._config_ref = Config(
            llm_config={"provider": "openai", "model": "stub", "api_key": "x"},
            llm_json_retries=retries, llm_max_tokens=max_tokens,
        )
        self.call_count = 0
        self.replies = list(replies)
        self.formats: list[dict | None] = []
        self.budgets: list[int | None] = []

    async def complete_with_meta(self, messages, temperature=0.3, *, response_format=None, max_tokens=None):
        self.call_count += 1
        self.formats.append(response_format)
        self.budgets.append(max_tokens)
        reply = self.replies.pop(0)
        text, finish = reply if isinstance(reply, tuple) else (reply, "stop")
        return {"content": text, "tool_calls": [], "raw": None, "finish_reason": finish}


async def test_complete_json_sends_schema_and_parses_fenced_output():
    llm = _Recorder(['Sure:\n```json\n{"items": [{"content": "x"}]}\n```'])
    out = await llm.complete_json([{"role": "user", "content": "go"}], schema=EXTRACTION_SCHEMA, name="extraction")
    assert out == {"items": [{"content": "x"}]}
    assert llm.formats[0]["json_schema"]["name"] == "extraction"
    assert llm.formats[0]["json_schema"]["schema"] is EXTRACTION_SCHEMA


async def test_complete_json_reasks_once_then_raises():
    llm = _Recorder(["I cannot do that.", "<think>still thinking</think>"])
    with pytest.raises(IngestLLMOutputError, match="extraction"):
        await llm.complete_json([{"role": "user", "content": "go"}], schema=EXTRACTION_SCHEMA, name="extraction")
    assert llm.call_count == 2


async def test_complete_json_accepts_empty_object_as_an_answer():
    llm = _Recorder(['{"items": []}'])
    assert await llm.complete_json([], schema=EXTRACTION_SCHEMA) == {"items": []}
    assert llm.call_count == 1


async def test_extractor_round_trip_uses_schema():
    llm = _Recorder(['{"items": [{"content": "Aflac contact is Jen.", "category": "fact", "confidence": 0.9, '
                     '"importance": 0.6, "why": null, "connections": [], "supports": null, "contradicts": null, "concepts": ["Aflac"]}]}'])
    section = Section(title="s", content="Jen: route Aflac tickets to me.", index=0, char_start=0, char_end=30)
    doc = DocumentInfo(path="text:1", title="d", file_type="text", source_type="connector_conversation_day", word_count=6, content_hash="1")
    items = await KnowledgeExtractor(llm).extract(section=section, doc=doc, appraisal=Appraisal(), mode=IngestionMode.FAST, max_items=5)
    assert len(items) == 1 and items[0].concepts == ["Aflac"] and items[0].importance == 0.6
    assert llm.formats == [response_format_for("extraction", EXTRACTION_SCHEMA)]


async def test_truncated_output_is_reasked_with_a_bigger_budget():
    llm = _Recorder([('{"items": [{"content": "half a fa', "length"), '{"items": [{"content": "whole fact"}]}'], max_tokens=1000)
    out = await llm.complete_json([], schema=EXTRACTION_SCHEMA, name="extraction")
    assert out == {"items": [{"content": "whole fact"}]}
    assert llm.budgets == [1000, 2000]


async def test_truncation_is_named_in_the_error_and_budget_is_capped():
    llm = _Recorder([('{"items": [', "length")] * 4, retries=3, max_tokens=1000)
    with pytest.raises(IngestLLMOutputError, match="truncated at 4000 output tokens"):
        await llm.complete_json([], schema=EXTRACTION_SCHEMA, name="extraction")
    assert llm.budgets == [1000, 2000, 4000, 4000]


@pytest.mark.parametrize(("setting", "sent"), [(None, None), ("", None), ("none", "none"), (" low ", "low")])
async def test_reading_calls_send_the_configured_reasoning_effort(monkeypatch, setting, sent):
    seen = {}

    async def fake_chat_completion(**kwargs):
        seen.update(kwargs)
        return {"content": "{}", "tool_calls": [], "raw": None, "finish_reason": "stop"}

    async def no_usage(**kwargs):
        return None

    monkeypatch.setattr("core.llm.chat_completion", fake_chat_completion)
    monkeypatch.setattr("core.usage.record_llm_usage", no_usage)
    llm = IngestLLM(Config(
        llm_config={"provider": "openai", "model": "stub", "api_key": "x"}, llm_reasoning_effort=setting,
    ))
    await llm.complete_with_meta([{"role": "user", "content": "go"}])
    assert seen["reasoning_effort"] == sent
