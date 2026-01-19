from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from services import chat as chat_mod
from core.cognitive_memory_api import HydratedContext

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.core]


async def test_estimate_importance_uses_learning_signals():
    score = chat_mod._estimate_importance("remember this", "ok")  # noqa: SLF001
    assert score >= 0.8


async def test_build_system_prompt_includes_profile():
    prompt = chat_mod._build_system_prompt({"name": "Hexis"})  # noqa: SLF001
    assert "Hexis" in prompt


async def test_chat_turn_basic_flow(monkeypatch):
    class DummyMem:
        def __init__(self):
            self.remembered = []
            self.touched = []

        async def hydrate(self, *_args, **_kwargs):
            return HydratedContext(
                memories=[],
                partial_activations=[],
                identity=[],
                worldview=[],
                emotional_state=None,
                goals=None,
                urgent_drives=[],
            )

        async def touch_memories(self, ids):
            self.touched.extend(ids)

        async def remember(self, content, **_kwargs):
            self.remembered.append(content)
            return uuid4()

    mem = DummyMem()

    @asynccontextmanager
    async def fake_connect(_dsn, **_kwargs):
        yield mem

    async def fake_chat_completion(**_kwargs):
        return {"content": "hello there", "tool_calls": []}

    monkeypatch.setattr(chat_mod.CognitiveMemory, "connect", fake_connect)
    monkeypatch.setattr(chat_mod, "chat_completion", fake_chat_completion)
    async def fake_agent_profile(_dsn):
        return {}

    monkeypatch.setattr(chat_mod, "get_agent_profile_context", fake_agent_profile)

    result = await chat_mod.chat_turn(
        user_message="hi",
        history=[],
        llm_config={"provider": "openai", "model": "gpt-4o"},
        dsn="postgresql://unused",
    )

    assert result["assistant"] == "hello there"
    assert mem.remembered


async def test_chat_turn_tool_loop(monkeypatch):
    class DummyMem:
        async def hydrate(self, *_args, **_kwargs):
            return HydratedContext(
                memories=[],
                partial_activations=[],
                identity=[],
                worldview=[],
                emotional_state=None,
                goals=None,
                urgent_drives=[],
            )

        async def touch_memories(self, _ids):
            return None

        async def remember(self, *_args, **_kwargs):
            return uuid4()

    mem = DummyMem()

    @asynccontextmanager
    async def fake_connect(_dsn, **_kwargs):
        yield mem

    responses = [
        {"content": "", "tool_calls": [{"id": "tool-1", "name": "recall", "arguments": {"query": "x"}}]},
        {"content": "final response", "tool_calls": []},
    ]
    tool_calls = []

    async def fake_chat_completion(**_kwargs):
        return responses.pop(0)

    async def fake_execute_tool(name, arguments, **_kwargs):
        tool_calls.append((name, arguments))
        return {"ok": True}

    monkeypatch.setattr(chat_mod.CognitiveMemory, "connect", fake_connect)
    monkeypatch.setattr(chat_mod, "chat_completion", fake_chat_completion)
    monkeypatch.setattr(chat_mod, "execute_tool", fake_execute_tool)
    async def fake_agent_profile(_dsn):
        return {}

    monkeypatch.setattr(chat_mod, "get_agent_profile_context", fake_agent_profile)

    result = await chat_mod.chat_turn(
        user_message="hi",
        history=[],
        llm_config={"provider": "openai", "model": "gpt-4o"},
        dsn="postgresql://unused",
    )

    assert result["assistant"] == "final response"
    assert tool_calls == [("recall", {"query": "x"})]
