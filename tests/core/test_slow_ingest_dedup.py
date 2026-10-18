"""Slow-ingest corroboration (#34): near-duplicate facts must become evidence
on the matched memory (add_evidence with the encounter as evidence node), not
silently dropped or re-created; failures must be logged, never swallowed.
"""
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.slow_ingest_rlm import run_slow_ingest

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


def _assessment(facts: list[str]) -> dict[str, Any]:
    return {
        "assessment": {
            "acceptance": "accept",
            "trust_assessment": 0.7,
            "importance": 0.6,
            "analysis": "test analysis",
            "extracted_facts": facts,
            "connections": [],
            "rejection_reasons": [],
            "worldview_impact": "neutral",
            "emotional_reaction": {},
        }
    }


def _make_pipeline(plan: list[dict[str, Any]]) -> MagicMock:
    """The pipeline's store is fully async now (#88) — AsyncMock throughout."""
    pipeline = MagicMock()
    pipeline._skip_section.return_value = False
    pipeline.config.cancel_check = None
    pipeline._source_payload.return_value = {
        "kind": "document",
        "ref": "test-doc.md",
        "source_document_id": "00000000-0000-0000-0000-000000000001",
    }
    pipeline._create_encounter_memory = AsyncMock(return_value="encounter-1")
    store = pipeline.store = AsyncMock()
    store.client = object()  # already "connected"
    store.get_receipts.return_value = {}  # fresh document: no receipts yet
    store.fetch_appraisal_context.return_value = {
        "worldview": [], "emotional_state": {}, "goals": [],
    }
    store.route_texts.return_value = plan
    store.create_semantic_memory.return_value = "new-memory-1"
    store.add_evidence.return_value = {"applied": True, "prior": 0.5, "posterior": 0.64}
    return pipeline


def _section() -> MagicMock:
    section = MagicMock()
    section.index = 0
    section.title = "Body"
    section.content = "some section text"
    return section


def _doc() -> MagicMock:
    doc = MagicMock()
    doc.title = "Test Doc"
    doc.document_id = "00000000-0000-0000-0000-000000000001"
    return doc


async def _run(pipeline) -> dict[str, Any]:
    facts = ["fact zero is long enough", "fact one is long enough", "fact two is long enough"]
    with patch(
        "services.slow_ingest_rlm.run_slow_ingest_chunk",
        new=AsyncMock(return_value=_assessment(facts)),
    ):
        return await run_slow_ingest(
            pipeline=pipeline,
            doc=_doc(),
            sections=[_section()],
            llm_config={"provider": "openai", "model": "test"},
            dsn="postgresql://unused",
        )


async def test_slow_path_delegates_to_atomic_sql_pass():
    """The wrapper makes ONE persist_slow_facts call (db/66) carrying the
    facts, assessment, source, encounter, and edge targets — routing,
    corroboration, creation, and every edge kind are DB-owned."""
    pipeline = _make_pipeline([])
    pipeline.store.persist_slow_facts.return_value = {
        "created": ["new-memory-1", "new-memory-2"],
        "corroborated": 1,
    }
    result = await _run(pipeline)

    pipeline.store.persist_slow_facts.assert_called_once()
    call = pipeline.store.persist_slow_facts.call_args
    assert call.args[0] == [
        "fact zero is long enough",
        "fact one is long enough",
        "fact two is long enough",
    ]
    assert call.args[1]["acceptance"] == "accept"
    assert call.kwargs["encounter_id"] == "encounter-1"
    assert call.kwargs["context"] == "slow_ingest"
    assert result["memories_created"] == 2
