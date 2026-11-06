"""The inline appraisal knows whose gut it is, and asks for a quick answer."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from core.subconscious import character_block
from services.agent import run_subconscious_appraisal

pytestmark = pytest.mark.asyncio(loop_scope="session")


def test_character_block_names_the_agent_but_not_its_voice():
    block = character_block(
        {
            "persona": {
                "name": "Samantha",
                "description": "A warm companion who remembers.",
                "voice": "Short and blunt.",
                "values": ["honesty"],
            }
        }
    )
    assert block.startswith("You are the subconscious of Samantha: A warm companion who remembers.")
    assert "not scripts for the reply" in block
    assert "Short and blunt" not in block and "honesty" not in block


def test_character_block_is_empty_without_a_name():
    assert character_block({}) == ""
    assert character_block({"persona": {"description": "x"}}) == ""
    assert character_block("not json") == ""
    assert character_block(json.dumps({"persona": {"name": "Sam"}})) == "You are the subconscious of Sam\n" + (
        "Instincts are signals about what matters, not scripts for the reply: "
        "never suggest how to phrase it, and never suggest presenting a menu or overview of capabilities."
    )


class _Conn:
    async def fetchval(self, query, *args, **kwargs):
        if "get_agent_profile_context" in query:
            return json.dumps({"persona": {"name": "Samantha", "description": "A warm companion."}})
        if "get_appraisal_db_context" in query:
            return json.dumps({"identity": [], "worldview": [], "limits": {}})
        if "normalize_inline_appraisal" in query:
            doc = json.loads(args[0])
            for key in (
                "salient_memories",
                "ignored_memories",
                "memory_expansions",
                "instincts",
                "narrative_observations",
                "relationship_observations",
                "contradiction_observations",
                "emotional_observations",
                "consolidation_observations",
            ):
                doc.setdefault(key, [])
            doc.setdefault("emotional_state", {})
            doc.setdefault("subconscious_response", "")
            return doc
        return None


async def test_appraisal_is_for_the_character_and_asks_for_low_effort():
    captured = {}

    async def fake_chat_json(**kwargs):
        captured.update(kwargs)
        return {"instincts": [], "emotional_state": {}, "subconscious_response": ""}, {"raw": True}

    with patch("services.agent.chat_json", side_effect=fake_chat_json):
        output = await run_subconscious_appraisal(
            _Conn(), "how are you?", llm_config={"provider": "fake", "model": "fake"}
        )

    system = captured["messages"][0]["content"]
    assert system.startswith("You are the subconscious of Samantha: A warm companion.")
    assert captured["reasoning_effort"] == "low"
    assert captured["usage_source"] == "subconscious"
    assert output.request_messages[0]["content"] == system
