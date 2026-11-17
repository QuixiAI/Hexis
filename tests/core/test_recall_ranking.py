"""Recall hands callers its best memories first.

recmem_recall_context returns rows grouped by tier name, and hydrate keeps
the first few, so the episodic tier ("I read 'Slack #sales — …'") filled
every slot and ingested facts (semantic) never reached the chat context:
"Who is Sara Lin?" got "I don't have anything on her" while a semantic
memory naming her scored highest.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from core.cognitive_memory_api import CognitiveMemory

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


def _row(tier, memory_type, score, content):
    return {
        "tier": tier, "item_id": uuid.uuid4(), "content": content, "memory_type": memory_type,
        "score": score, "source_unit_ids": [], "source_attribution": None,
        "created_at": datetime.now(timezone.utc), "trust_level": 0.5, "fidelity": 1.0,
        "strength": 1.0, "emotional_intensity": None, "confidence": None, "retrieval_source": "vector",
    }


class _Conn:
    def __init__(self, rows):
        self.rows = rows

    async def fetchrow(self, *args):
        return {"sub": 10, "epi": 5, "sem": 10, "know": 5}

    async def fetch(self, *args):
        return self.rows

    async def execute(self, *args):
        return None

    async def fetchval(self, *args):
        return None


async def test_recalled_memories_are_ranked_by_score_not_tier():
    rows = [  # the SQL's order: by tier name
        *[_row("episodic", "episodic", 0.59 - i / 100, f"I read 'Slack #sales — day {i}'.") for i in range(8)],
        _row("knowledge", "worldview", 0.68, "My name is Alex."),
        _row("semantic", "semantic", 0.74, "Priya and Alex agreed not to chase the RFP."),
    ]
    memories = await CognitiveMemory._recall_recmem(object.__new__(CognitiveMemory), _Conn(rows), "Who is Priya?", 10)
    assert memories[0].content == "Priya and Alex agreed not to chase the RFP."
    scores = [m.similarity for m in memories]
    assert scores == sorted(scores, reverse=True)
