"""Provider boundary regressions: instruction priority, recovery, no replay."""

from copy import deepcopy
from types import SimpleNamespace as NS

import pytest
from core import llm

pytestmark = pytest.mark.asyncio(loop_scope="session")


def bad(message):
    return llm.litellm.BadRequestError(
        message=message, model="m", llm_provider="openai"
    )


def answer(text="ok"):
    return NS(
        choices=[NS(message=NS(content=text, tool_calls=[]), finish_reason="stop")]
    )


async def test_codex_lifts_late_instructions_preserving_tool_history():
    original = [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t"}]},
        {"role": "tool", "content": "result", "tool_call_id": "t"},
        {"role": "system", "content": "action receipt"},
        {"role": "developer", "content": [{"type": "text", "text": "runtime"}]},
    ]
    before = deepcopy(original)
    out = llm._litellm_messages(
        original, provider="openai-codex", model="m", auth_mode=None
    )
    assert [x["content"] for x in out[:3]] == ["persona", "action receipt", "runtime"]
    assert all(x["role"] == "system" for x in out[:3])
    assert out[3:] == original[1:4]
    assert original == before


async def test_recovery_removes_rejected_stream_options_and_informs_model(monkeypatch):
    calls, events, tokens = [], [], []

    async def complete(**request):
        calls.append(deepcopy(request))
        if "stream_options" in request:
            raise bad("Unsupported parameter: stream_options")

        async def chunks():
            yield NS(
                choices=[
                    NS(
                        delta=NS(content="recovered", tool_calls=[]),
                        finish_reason="stop",
                    )
                ]
            )

        return chunks()

    monkeypatch.setattr(llm.litellm, "acompletion", complete)
    result = await llm.stream_chat_completion(
        provider="openai",
        model="m",
        endpoint=None,
        api_key="k",
        messages=[{"role": "user", "content": "hello"}],
        on_recovery=events.append,
        on_text_delta=tokens.append,
    )
    assert result["content"] == "recovered"
    assert tokens == ["recovered"]
    assert len(calls) == 2 and events == [{"kind": "stream_options_fallback"}]
    assert "Briefly acknowledge" in calls[1]["messages"][0]["content"]


async def test_stream_falls_back_to_buffered_with_same_tools(monkeypatch):
    calls, events, tokens = [], [], []

    async def complete(**request):
        calls.append(deepcopy(request))
        if request.get("stream"):
            raise bad("Streaming is not supported")
        return answer()

    monkeypatch.setattr(llm.litellm, "acompletion", complete)
    tools = [{"type": "function", "function": {"name": "recall", "parameters": {}}}]
    result = await llm.stream_chat_completion(
        provider="openai",
        model="m",
        endpoint=None,
        api_key="k",
        messages=[{"role": "user", "content": "hello"}],
        tools=tools,
        on_recovery=events.append,
        on_text_delta=tokens.append,
    )
    assert result["content"] == "ok" and tokens == ["ok"]
    assert len(calls) == 2 and calls[1]["tools"] == tools
    assert events == [{"kind": "buffered_response_fallback"}]


async def test_partial_stream_is_never_replayed_or_fallen_back(monkeypatch):
    calls, tokens, events = [], [], []

    async def complete(**request):
        calls.append(request)

        async def chunks():
            yield NS(
                choices=[
                    NS(delta=NS(content="partial", tool_calls=[]), finish_reason=None)
                ]
            )
            raise bad("Streaming is not supported")

        return chunks()

    monkeypatch.setattr(llm.litellm, "acompletion", complete)
    with pytest.raises(llm.litellm.BadRequestError):
        await llm.stream_chat_completion(
            provider="openai",
            model="m",
            endpoint=None,
            api_key="k",
            messages=[{"role": "user", "content": "hello"}],
            on_recovery=events.append,
            on_text_delta=tokens.append,
        )
    assert len(calls) == 1 and tokens == ["partial"] and events == []


async def test_transient_retry_notifies_model_without_echoing_error_body(monkeypatch):
    events, calls = [], []

    async def no_sleep(_):
        pass

    monkeypatch.setattr("asyncio.sleep", no_sleep)

    async def complete(**request):
        calls.append(deepcopy(request))
        if len(calls) == 1:
            raise llm.litellm.RateLimitError(
                message="private-body", model="m", llm_provider="openai"
            )
        return answer()

    monkeypatch.setattr(llm.litellm, "acompletion", complete)
    await llm.chat_completion(
        provider="openai",
        model="m",
        endpoint=None,
        api_key="k",
        messages=[{"role": "user", "content": "hello"}],
        on_recovery=events.append,
    )
    assert events[0]["kind"] == "transient_retry"
    assert "transient_retry" in calls[1]["messages"][0]["content"]
    assert "private-body" not in str(events) + str(calls[1])


async def test_explicit_client_error_is_not_retried_by_incidental_words():
    assert not llm.is_transient_error(
        bad("Generate request invalid: connection configuration")
    )


async def test_configured_fallback_keeps_identity_and_tools(monkeypatch):
    calls, events = [], []

    async def complete(**request):
        calls.append(deepcopy(request))
        if len(calls) == 1:
            raise llm.litellm.AuthenticationError(
                message="expired", model="m", llm_provider="openai"
            )
        return answer("Recovered")

    monkeypatch.setattr(llm.litellm, "acompletion", complete)
    tools = [{"type": "function", "function": {"name": "recall", "parameters": {}}}]
    result = await llm.chat_completion(
        provider="openai",
        model="primary",
        endpoint=None,
        api_key="k",
        messages=[
            {"role": "system", "content": "persona"},
            {"role": "user", "content": "hello"},
        ],
        tools=tools,
        fallbacks=[
            {
                "provider": "openai_compatible",
                "model": "reserve",
                "endpoint": "http://localhost:4000/v1",
                "api_key": "local",
            }
        ],
        on_recovery=events.append,
    )
    assert (
        result["model"] == "reserve"
        and result["recoveries"][0]["kind"] == "model_fallback"
    )
    assert calls[1]["tools"] == tools
    assert [m for m in calls[1]["messages"] if "verified runtime recovery facts" not in str(m.get("content"))] == calls[0]["messages"]
    assert events == result["recoveries"]


async def test_model_fallback_never_replays_partial_stream(monkeypatch):
    calls, tokens = [], []

    async def complete(**request):
        calls.append(request)

        async def chunks():
            yield NS(
                choices=[
                    NS(delta=NS(content="partial", tool_calls=[]), finish_reason=None)
                ]
            )
            raise llm.litellm.AuthenticationError(
                message="expired", model="m", llm_provider="openai"
            )

        return chunks()

    monkeypatch.setattr(llm.litellm, "acompletion", complete)
    with pytest.raises(llm.litellm.AuthenticationError):
        await llm.stream_chat_completion(
            provider="openai",
            model="primary",
            endpoint=None,
            api_key="k",
            messages=[{"role": "user", "content": "hello"}],
            on_text_delta=tokens.append,
            fallbacks=[
                {
                    "provider": "openai_compatible",
                    "model": "reserve",
                    "endpoint": "http://localhost:4000/v1",
                }
            ],
        )
    assert len(calls) == 1 and tokens == ["partial"]


async def test_slow_primary_leaves_time_for_fallback(monkeypatch):
    import asyncio

    calls = []

    async def complete(**request):
        calls.append(request["model"])
        if len(calls) == 1:
            await asyncio.sleep(30)
        return answer("reserve")

    monkeypatch.setattr(llm.litellm, "acompletion", complete)
    result = await asyncio.wait_for(
        llm.chat_completion(
            provider="openai",
            model="primary",
            endpoint=None,
            api_key="k",
            messages=[{"role": "user", "content": "hello"}],
            inference_timeout=0.4,
            fallbacks=[
                {
                    "provider": "openai_compatible",
                    "model": "reserve",
                    "endpoint": "http://localhost:4000/v1",
                }
            ],
        ),
        timeout=2,
    )
    assert result["content"] == "reserve" and len(calls) == 2
    assert result["recoveries"][0]["previous_error_type"] == "TimeoutError"


async def test_empty_success_response_uses_reserve(monkeypatch):
    calls = []

    async def complete(**request):
        calls.append(request["model"])
        return answer("" if len(calls) == 1 else "reserve answer")

    monkeypatch.setattr(llm.litellm, "acompletion", complete)
    result = await llm.chat_completion(
        provider="openai",
        model="primary",
        endpoint=None,
        api_key="k",
        messages=[{"role": "user", "content": "hello"}],
        fallbacks=[
            {
                "provider": "openai_compatible",
                "model": "reserve",
                "endpoint": "http://localhost:4000/v1",
            }
        ],
    )
    assert result["content"] == "reserve answer" and len(calls) == 2
