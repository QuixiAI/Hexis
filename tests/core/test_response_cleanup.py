"""Chat-only compatibility cleanup, including the original #144 regressions."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core import agent_loop, llm
from core.agent_loop import AgentEvent, AgentLoop, AgentLoopConfig
from core.response_cleanup import LeadingDividerFilter, strip_leading_divider
from core.tools.base import ToolContext


def test_strip_leading_divider_removes_opening_rule():
    assert strip_leading_divider("---\nHere is the reply.") == "Here is the reply."
    assert strip_leading_divider("***\nHere is the reply.") == "Here is the reply."
    assert strip_leading_divider("___\nHere is the reply.") == "Here is the reply."
    assert strip_leading_divider("- - -\nHere is the reply.") == "Here is the reply."


def test_strip_leading_divider_removes_stacked_rules_and_whitespace():
    assert strip_leading_divider("\n---\n\n---\n\nReply.") == "Reply."


def test_strip_leading_divider_keeps_internal_rules():
    content = "First section.\n\n---\n\nSecond section."
    assert strip_leading_divider(content) == content


def test_strip_leading_divider_is_a_no_op_on_clean_content():
    for content in ("", "Plain reply.", "# Heading\n\nBody.", "-- not a rule"):
        assert strip_leading_divider(content) == content


def test_strip_leading_divider_returns_empty_when_output_was_only_a_rule():
    assert strip_leading_divider("---") == ""
    assert strip_leading_divider("\n---\n") == ""


def test_strip_leading_divider_does_not_eat_a_list_item():
    content = "- first item\n- second item"
    assert strip_leading_divider(content) == content


@pytest.mark.parametrize(
    "content",
    [
        '    print("hello")\n',
        "\n\nPlain reply.",
        "\tcode",
        "   ",
        "---\ntitle: Example\n---\nBody",
        "---\n# Document comment\ntitle: X\n---\n",
        "---\n  nested: true\n---\n",
        "---\n- entry\n---\n",
        "*-_\nReply",
        "-\n-\n-\nReply",
        "-- not a rule",
        "    ---\nindented code",
        "```yaml\n---\ntitle: X\n---\n```",
        "First section.\n\n---\n\nSecond section.",
    ],
)
def test_preserves_meaningful_content(content):
    assert strip_leading_divider(content) == content


@pytest.mark.parametrize(
    "content, expected",
    [
        ("---\nReply.", "Reply."),
        ("\n---\n\n***\nReply.", "Reply."),
        ("* * *\r\nReply.\r\n", "Reply.\r\n"),
        ("___", ""),
        ("---\n\n", ""),
        ("- - -\nReply.\n---\nNext.", "Reply.\n---\nNext."),
        ("---\ntitle: Example\n---\nBody", "---\ntitle: Example\n---\nBody"),
        ("*\n*\n*\nReply", "*\n*\n*\nReply"),
        ("---\n" + "x" * 5000, "---\n" + "x" * 5000),
    ],
)
def test_stream_cleanup_is_independent_of_chunk_boundaries(content, expected):
    assert strip_leading_divider(content) == expected
    for split in range(len(content) + 1):
        cleaner = LeadingDividerFilter()
        assert (
            cleaner.feed(content[:split])
            + cleaner.feed(content[split:])
            + cleaner.finish()
            == expected
        )
    cleaner = LeadingDividerFilter()
    assert (
        "".join(cleaner.feed(char) for char in content) + cleaner.finish() == expected
    )


def test_stream_releases_body_after_first_line_without_waiting_for_completion():
    cleaner = LeadingDividerFilter()
    assert cleaner.feed("---\n") == ""
    assert cleaner.feed("A reply") == ""
    assert cleaner.feed(".\n") == "A reply.\n"
    assert cleaner.feed("Continuing") == "Continuing"
    assert cleaner.finish() == ""


@pytest.mark.parametrize(
    "setting, expected",
    [
        (None, None),
        (True, None),
        ("model", None),
        (["model", 1, False, ""], ["model"]),
    ],
)
def test_normalize_keeps_only_explicit_model_list(setting, expected):
    config = llm.normalize_llm_config(
        {
            "provider": "openai_compatible",
            "model": "model",
            "strip_leading_divider_models": setting,
        }
    )
    assert config.get("strip_leading_divider_models") == expected


def _loop(
    monkeypatch,
    *,
    streaming=False,
    models=None,
    model="affected",
    context=ToolContext.CHAT,
):
    events = []

    async def on_event(event):
        events.append(event)

    config = AgentLoopConfig(
        tool_context=context,
        system_prompt="Test",
        llm_config=llm.normalize_llm_config(
            {
                "provider": "openai_compatible",
                "model": model,
                "endpoint": "http://local.test/v1",
                "strip_leading_divider_models": models,
            }
        ),
        registry=None,
        pool=None,
        on_event=on_event,
    )
    loop = AgentLoop(config)
    loop._streaming = streaming
    monkeypatch.setattr(agent_loop, "record_llm_usage", AsyncMock())
    return loop, events


def _reply(text, tools=None):
    return {
        "content": text,
        "tool_calls": tools or [],
        "raw": object(),
        "finish_reason": "stop",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "models, model, context, expected",
    [
        (None, "affected", ToolContext.CHAT, "---\nReply."),
        (["affected"], "different", ToolContext.CHAT, "---\nReply."),
        ("affected", "affected", ToolContext.CHAT, "---\nReply."),
        (["affected"], "affected", ToolContext.HEARTBEAT, "---\nReply."),
        (["affected"], "affected", ToolContext.CHAT, "Reply."),
    ],
)
async def test_agent_cleanup_requires_exact_opt_in_chat_model(
    monkeypatch, streaming, models, model, context, expected
):
    loop, events = _loop(
        monkeypatch, streaming=streaming, models=models, model=model, context=context
    )
    raw = _reply("---\nReply.")

    async def completion(**kwargs):
        if streaming:
            for part in ("-", "--\n", "Re", "ply."):
                await kwargs["on_text_delta"](part)
        return raw

    monkeypatch.setattr(
        agent_loop,
        "stream_chat_completion" if streaming else "chat_completion",
        completion,
    )
    result = await loop._llm_call([{"role": "user", "content": "Hi"}])
    assert result["content"] == expected
    assert raw["content"] == "---\nReply."
    assert result["raw"] is raw["raw"]
    if streaming:
        visible = "".join(
            e.data["text"] for e in events if e.event == AgentEvent.TEXT_DELTA
        )
        assert visible == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("second", ["---", "", "---\nRecovered."])
async def test_divider_only_answer_recovers_once_then_fails_visibly(
    monkeypatch, streaming, second
):
    loop, events = _loop(monkeypatch, streaming=streaming, models=["affected"])
    calls = []

    async def completion(**kwargs):
        calls.append(kwargs)
        text = "---" if len(calls) == 1 else second
        if streaming:
            await kwargs["on_text_delta"](text)
        return _reply(text)

    monkeypatch.setattr(
        agent_loop,
        "stream_chat_completion" if streaming else "chat_completion",
        completion,
    )
    messages = [{"role": "user", "content": "Hi"}]
    if second in ("---", ""):
        with pytest.raises(RuntimeError, match="produced no answer"):
            await loop._llm_call(messages)
    else:
        result = await loop._llm_call(messages)
        assert result["content"] == "Recovered."
    assert len(calls) == 2
    assert len(messages) == 1
    assert "only Markdown dividers" in calls[1]["messages"][-1]["content"]
    # Both calls are metered, but no divider reaches the person waiting.
    assert len([e for e in events if e.event == AgentEvent.LLM_RESPONSE]) == 2
    if streaming:
        visible = "".join(
            e.data["text"] for e in events if e.event == AgentEvent.TEXT_DELTA
        )
        assert visible == ("Recovered." if second.endswith("Recovered.") else "")


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_divider_only_tool_turn_is_not_retried(monkeypatch, streaming):
    loop, _ = _loop(monkeypatch, streaming=streaming, models=["affected"])
    tool_calls = [{"id": "call-1", "name": "recall", "arguments": {"query": "test"}}]

    async def completion(**kwargs):
        if streaming:
            await kwargs["on_text_delta"]("---")
        return _reply("---", tool_calls)

    mock = AsyncMock(side_effect=completion)
    monkeypatch.setattr(
        agent_loop, "stream_chat_completion" if streaming else "chat_completion", mock
    )
    result = await loop._llm_call([{"role": "user", "content": "Hi"}])
    assert result["content"] == ""
    assert result["tool_calls"] == tool_calls
    assert mock.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_default_divider_only_reply_is_preserved_without_retry(
    monkeypatch, streaming
):
    loop, _ = _loop(monkeypatch, streaming=streaming)

    async def completion(**kwargs):
        if streaming:
            await kwargs["on_text_delta"]("---")
        return _reply("---")

    mock = AsyncMock(side_effect=completion)
    monkeypatch.setattr(
        agent_loop, "stream_chat_completion" if streaming else "chat_completion", mock
    )
    assert (await loop._llm_call([]))["content"] == "---"
    assert mock.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_model_config_through_provider_to_chat_events(monkeypatch, streaming):
    """Exercise the real LLM adapter as well as the agent's response boundary."""
    loop, events = _loop(monkeypatch, streaming=streaming, models=["affected"])
    sent = []

    async def chunks():
        for text in ("---\n", "An answer.\n", "More content."):
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=text, tool_calls=None),
                        finish_reason="stop",
                    )
                ],
                usage=None,
            )

    async def acompletion(**kwargs):
        sent.append(kwargs)
        if kwargs.get("stream"):
            return chunks()
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="---\nAn answer.\nMore content.", tool_calls=None
                    ),
                    finish_reason="stop",
                )
            ]
        )

    monkeypatch.setattr(llm.litellm, "acompletion", acompletion)
    result = await loop._llm_call([{"role": "user", "content": "Hi"}])
    assert result["content"] == "An answer.\nMore content."
    assert "strip_leading_divider_models" not in sent[0]
    if streaming:
        assert (
            "".join(e.data["text"] for e in events if e.event == AgentEvent.TEXT_DELTA)
            == result["content"]
        )


@pytest.mark.asyncio
async def test_stream_that_only_returns_final_content_is_not_lost(monkeypatch):
    loop, events = _loop(monkeypatch, streaming=True, models=["affected"])
    monkeypatch.setattr(
        agent_loop,
        "stream_chat_completion",
        AsyncMock(return_value=_reply("---\nAnswer.")),
    )
    result = await loop._llm_call([])
    assert result["content"] == "Answer."
    assert (
        "".join(e.data["text"] for e in events if e.event == AgentEvent.TEXT_DELTA)
        == "Answer."
    )


@pytest.mark.asyncio
async def test_recovery_failure_reaches_the_existing_chat_error_flow(monkeypatch):
    loop, events = _loop(monkeypatch, models=["affected"])
    loop._next_agent_step = AsyncMock(
        return_value={"action": "call_llm", "iteration": 1, "messages": []}
    )
    loop._get_messages = AsyncMock(return_value=[])
    loop._apply_llm_result = AsyncMock()
    completion = AsyncMock(return_value=_reply("---"))
    monkeypatch.setattr(agent_loop, "chat_completion", completion)
    result = await loop._execute_loop([])
    assert result.stopped_reason == "error"
    assert completion.await_count == 2
    loop._apply_llm_result.assert_not_awaited()
    errors = [e.data for e in events if e.event == AgentEvent.ERROR]
    assert len(errors) == 1
    assert "Try again or select another model" in errors[0]["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer, enabled, expected",
    [
        ("---\nAnswer.", True, "Answer."),
        ("---\nAnswer.", False, "---\nAnswer."),
        ("---\ntitle: Example\n---\nBody", True, "---\ntitle: Example\n---\nBody"),
        ("---", True, None),
    ],
)
async def test_rlm_cleans_only_the_final_answer_without_replaying_actions(
    monkeypatch, answer, enabled, expected
):
    from unittest.mock import MagicMock
    from services import hexis_rlm as rlm

    runs = []

    def run_loop(*args):
        runs.append(args)
        return {"final_answer": answer, "iterations": 1, "message_count": 1}

    monkeypatch.setattr(rlm, "_run_loop", run_loop)
    monkeypatch.setattr(rlm, "MemoryRepo", MagicMock())
    monkeypatch.setattr(rlm, "HexisLocalREPL", MagicMock())
    monkeypatch.setattr(rlm, "_cleanup_stale_sessions", AsyncMock())
    config = {"provider": "openai_compatible", "model": "affected"}
    if enabled:
        config["strip_leading_divider_models"] = ["affected"]
    if expected is None:
        with pytest.raises(RuntimeError, match="produced no answer"):
            await rlm.run_chat_turn(user_message="Hi", llm_config=config, dsn="unused")
    else:
        result = await rlm.run_chat_turn(
            user_message="Hi", llm_config=config, dsn="unused"
        )
        assert result["response"] == expected
    assert len(runs) == 1
