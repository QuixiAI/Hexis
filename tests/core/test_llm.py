"""core.llm: every provider through LiteLLM, with Hexis's own sign-ins kept."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core import llm

pytestmark = pytest.mark.core


@pytest.fixture(autouse=True)
def _reset_caches():
    llm._endpoint_schema_support.clear()
    llm._reasoning_rejected.clear()
    yield
    llm._endpoint_schema_support.clear()
    llm._reasoning_rejected.clear()


# ---------------------------------------------------------------------------
# Config normalisation
# ---------------------------------------------------------------------------


def test_normalize_provider_variants():
    assert llm.normalize_provider(None) == "openai"
    assert llm.normalize_provider("OpenAI") == "openai"
    assert llm.normalize_provider("openai_chat_completions_endpoint") == "openai-chat-completions-endpoint"
    assert llm.normalize_provider("openai_codex") == "openai-codex"
    assert llm.normalize_provider("github_copilot") == "github-copilot"
    assert llm.normalize_provider("qwen_portal") == "qwen-portal"
    assert llm.normalize_provider("minimax_portal") == "minimax-portal"
    assert llm.normalize_provider("google_gemini_cli") == "google-gemini-cli"
    assert llm.normalize_provider("google_antigravity") == "google-antigravity"
    assert llm.normalize_provider("xai_oauth") == "xai-oauth"


def test_supported_providers_keep_the_companion_sign_ins_and_add_the_clouds():
    assert llm.SUPPORTED_PROVIDERS == {
        # API keys and local servers
        "openai", "openai_compatible", "openai-chat-completions-endpoint", "grok", "anthropic", "gemini",
        # Subscription sign-ins
        "openai-codex", "chutes", "github-copilot", "qwen-portal", "minimax-portal",
        "google-gemini-cli", "google-antigravity", "xai-oauth",
        # Clouds and gateways
        "azure_openai", "openrouter", "bedrock", "vertex", "zai", "deepseek", "qwen", "qwen_coding", "meta", "minimax",
    }


def test_normalize_endpoint_defaults():
    assert llm.normalize_endpoint("openai", "  ") is None
    assert llm.normalize_endpoint("openai", " https://example.com ") == "https://example.com"
    assert llm.normalize_endpoint("anthropic", None) is None
    assert llm.normalize_endpoint("grok", None) == "https://api.x.ai/v1"
    assert llm.normalize_endpoint("chutes", None) == "https://api.chutes.ai/v1"
    assert llm.normalize_endpoint("qwen-portal", None) == "https://portal.qwen.ai/v1"
    assert llm.normalize_endpoint("minimax-portal", None) == "https://api.minimax.io/anthropic"
    assert llm.normalize_endpoint("google-gemini-cli", None) == "https://cloudcode-pa.googleapis.com"
    assert llm.normalize_endpoint("openrouter", None) == "https://openrouter.ai/api/v1"
    assert llm.normalize_endpoint("bedrock", None) == "us-east-1"


def test_subscription_tokens_only_go_to_their_own_backend():
    # A stale API-key endpoint from a previous provider choice is never inherited.
    assert llm.normalize_endpoint("openai-codex", "https://api.openai.com/v1") == "https://chatgpt.com/backend-api"
    assert llm.normalize_endpoint("xai-oauth", "https://evil.example/v1") == "https://api.x.ai/v1"


def test_azure_endpoint_is_the_v1_api_under_the_resource():
    assert llm.normalize_endpoint("azure_openai", "https://res.openai.azure.com/") == "https://res.openai.azure.com/openai/v1"
    assert llm.normalize_endpoint("azure_openai", "https://res.openai.azure.com/openai/v1/") == "https://res.openai.azure.com/openai/v1"


def test_vertex_endpoint_parsing():
    assert llm.parse_vertex_endpoint("projects/bank-ai/locations/asia-northeast1") == ("bank-ai", "asia-northeast1")
    assert llm.parse_vertex_endpoint("projects/bank-ai/locations/") == ("bank-ai", "us-central1")
    assert llm.parse_vertex_endpoint("") == ("", "us-central1")


def test_normalize_llm_config_preserves_auth_mode():
    cfg = llm.normalize_llm_config({
        "provider": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "auth_mode": "setup-token",
    })
    assert cfg["auth_mode"] == "setup-token"
    assert "auth_mode" not in llm.normalize_llm_config({"provider": "anthropic", "model": "m"})


def test_resolve_api_key_from_env(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "abc123")
    assert llm.resolve_api_key("TEST_API_KEY") == "abc123"
    assert llm.resolve_api_key("  ") is None


def test_normalize_llm_config_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "envkey")
    assert llm.normalize_llm_config({"provider": "openai", "model": "gpt-4o"})["api_key"] == "envkey"
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    assert llm.normalize_llm_config({"provider": "openrouter", "model": "m"})["api_key"] == "or-key"


def test_openai_compatible_does_not_consume_ambient_openai_key(monkeypatch):
    """A local server must never silently pick up an OPENAI_API_KEY (Bar #5)."""
    monkeypatch.setenv("OPENAI_API_KEY", "ambient")
    cfg = llm.normalize_llm_config({"provider": "openai_compatible", "model": "m", "endpoint": "http://box:8000/v1"})
    assert cfg["api_key"] is None


def test_keyless_openai_compatible_sends_a_placeholder_key():
    target, connection = llm._litellm_target("openai_compatible", "qwen3", "http://host.docker.internal:8000/v1", None)
    assert target == "openai/qwen3"
    assert connection == {"api_base": "http://host.docker.internal:8000/v1", "api_key": "not-needed"}


# ---------------------------------------------------------------------------
# Message shaping
# ---------------------------------------------------------------------------


def test_extract_system_prompt():
    prompt, rest = llm._extract_system_prompt([
        {"role": "system", "content": "A"},
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "B"},
    ])
    assert prompt == "A\n\nB"
    assert rest == [{"role": "user", "content": "hi"}]


def test_openai_tool_call_parsing():
    class DummyFn:
        def __init__(self):
            self.name = "recall"
            self.arguments = '{"query":"hi"}'

    class DummyCall:
        def __init__(self):
            self.id = "call-1"
            self.function = DummyFn()

    parsed = llm._openai_tool_calls([DummyCall()])  # noqa: SLF001
    assert parsed == [{"id": "call-1", "name": "recall", "arguments": {"query": "hi"}}]


def test_image_parts_become_openai_image_urls():
    messages = [{"role": "user", "content": [
        {"type": "input_text", "text": "look"},
        {"type": "input_image", "image_url": "data:image/png;base64,AAAA", "detail": "high"},
    ]}]
    out = llm._litellm_messages(messages, provider="openai", model="gpt-4o", auth_mode=None)
    assert out[0]["content"] == [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA", "detail": "high"}},
    ]


_SPLIT = [
    {"role": "system", "content": "STABLE"},
    {"role": "system", "content": "VOLATILE"},
    {"role": "user", "content": "hi"},
]


def test_other_providers_keep_their_system_messages_in_order():
    out = llm._litellm_messages(_SPLIT, provider="openai", model="gpt-4o", auth_mode=None)
    assert out == _SPLIT
    out = llm._litellm_messages(_SPLIT, provider="gemini", model="gemini-2.5-flash", auth_mode=None)
    assert out == _SPLIT


@pytest.mark.parametrize("provider,model", [
    ("anthropic", "claude-sonnet-4-5"),
    ("minimax-portal", "MiniMax-M2"),
    ("bedrock", "anthropic.claude-sonnet-4-5-v1:0"),
    ("vertex", "claude-sonnet-4-5@20250929"),
])
def test_anthropic_targets_get_one_system_message_with_a_cache_breakpoint(provider, model):
    out = llm._litellm_messages(_SPLIT, provider=provider, model=model, auth_mode=None)
    assert [m["role"] for m in out] == ["system", "user"]
    blocks = out[0]["content"]
    assert blocks == [
        {"type": "text", "text": "STABLE", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "VOLATILE"},
    ]


def test_a_single_system_part_stays_a_plain_string():
    out = llm._litellm_messages(
        [{"role": "system", "content": "ONLY"}, {"role": "user", "content": "hi"}],
        provider="anthropic", model="claude-sonnet-4-5", auth_mode=None,
    )
    assert out[0] == {"role": "system", "content": "ONLY"}
    # Bedrock with a non-Claude model is not Anthropic Messages: untouched.
    out = llm._litellm_messages(_SPLIT, provider="bedrock", model="amazon.nova-pro-v1:0", auth_mode=None)
    assert out == _SPLIT


def test_setup_token_leads_with_the_claude_code_identity():
    out = llm._litellm_messages(_SPLIT, provider="anthropic", model="claude-sonnet-4-5", auth_mode="setup-token")
    blocks = out[0]["content"]
    assert blocks[0]["text"] == llm._CLAUDE_CODE_IDENTITY
    assert blocks[1] == {"type": "text", "text": "STABLE", "cache_control": {"type": "ephemeral"}}
    assert "cache_control" not in blocks[2]
    # With one part there is nothing to cache against: the shape stays a string.
    out = llm._litellm_messages(
        [{"role": "system", "content": "ONE"}, {"role": "user", "content": "hi"}],
        provider="anthropic", model="claude-sonnet-4-5", auth_mode="setup-token",
    )
    assert out[0]["content"] == f"{llm._CLAUDE_CODE_IDENTITY}\n\nONE"


def test_oauth_identity_is_canonical_claude_code_string():
    assert llm._CLAUDE_CODE_IDENTITY == "You are Claude Code, Anthropic's official CLI for Claude."


def test_oauth_mcp_tool_names_are_rewritten_and_restored():
    tools = [
        {"type": "function", "function": {"name": "mcp_server_tool", "parameters": {}}},
        {"type": "function", "function": {"name": "recall", "parameters": {}}},
    ]
    renamed, restore = llm._oauth_tools(tools)
    assert [t["function"]["name"] for t in renamed] == ["mcp__server_tool", "recall"]
    assert restore == {"mcp__server_tool": "mcp_server_tool"}
    assert llm._restore_tool_names([{"id": "1", "name": "mcp__server_tool", "arguments": {}}], restore) == [
        {"id": "1", "name": "mcp_server_tool", "arguments": {}}
    ]
    assert llm._oauth_tools(None) == (None, {})


def test_strip_reasoning_removes_leaked_thoughts():
    assert llm.strip_reasoning("<think>hmm</think>Answer") == "Answer"
    assert llm.strip_reasoning("```thought\nplan\n```\n\nAnswer") == "Answer"
    assert llm.strip_reasoning("clean") == "clean"
    assert llm.strip_reasoning("") == ""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_unknown_provider_fails_with_next_step():
    with pytest.raises(ValueError, match="hexis init"):
        await llm.chat_completion(
            provider="unknown-provider", model="m", endpoint=None, api_key="k",
            messages=[{"role": "user", "content": "hi"}],
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_stream_text_completion_unsupported_provider():
    with pytest.raises(ValueError):
        async for _ in llm.stream_text_completion(
            provider="unknown", model="x", endpoint=None, api_key=None,
            messages=[{"role": "user", "content": "hi"}],
        ):
            pass


@pytest.mark.asyncio(loop_scope="session")
async def test_chat_completion_requires_litellm(monkeypatch):
    monkeypatch.setattr(llm, "litellm", None)
    with pytest.raises(RuntimeError, match="litellm"):
        await llm.chat_completion(
            provider="openai", model="x", endpoint=None, api_key=None,
            messages=[{"role": "user", "content": "hi"}],
        )


# ---------------------------------------------------------------------------
# A fake litellm.acompletion
# ---------------------------------------------------------------------------


def _message(content="ok", tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls or [])


def _response(content="ok", tool_calls=None, finish_reason="stop", usage=None):
    choice = SimpleNamespace(message=_message(content, tool_calls), finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def _bad_request(message: str):
    return llm.litellm.BadRequestError(message=message, model="m", llm_provider="openai")


def _fake_acompletion(calls, *, rejects=lambda request: None, reply=None):
    async def acompletion(**request):
        calls.append(request)
        error = rejects(request)
        if error:
            raise _bad_request(error)
        return reply(request) if reply else _response('{"ok": true}')
    return acompletion


@pytest.mark.asyncio(loop_scope="session")
async def test_chat_completion_sends_openai_format_to_litellm(monkeypatch):
    calls = []
    call = SimpleNamespace(id="call-1", function=SimpleNamespace(name="recall", arguments='{"q": 1}'))
    monkeypatch.setattr(llm.litellm, "acompletion", _fake_acompletion(
        calls, reply=lambda r: _response("", [call], "tool_calls")))
    tools = [{"type": "function", "function": {"name": "recall", "parameters": {"type": "object"}}}]
    result = await llm.chat_completion(
        provider="anthropic", model="claude-sonnet-4-5", endpoint=None, api_key="k",
        messages=[{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}],
        tools=tools, temperature=0.2, max_tokens=50, reasoning_effort="low",
    )
    assert result["tool_calls"] == [{"id": "call-1", "name": "recall", "arguments": {"q": 1}}]
    assert result["finish_reason"] == "tool_calls"
    sent = calls[0]
    assert sent["model"] == "anthropic/claude-sonnet-4-5"
    assert sent["api_key"] == "k"
    assert sent["messages"][0] == {"role": "system", "content": "be brief"}
    assert sent["tools"] == tools and sent["tool_choice"] == "auto"
    assert sent["reasoning_effort"] == "low"
    assert sent["drop_params"] is True
    assert (sent["temperature"], sent["max_tokens"]) == (0.2, 50)


@pytest.mark.asyncio(loop_scope="session")
async def test_chat_completion_strips_leaked_reasoning_and_returns_the_raw_response(monkeypatch):
    calls = []
    raw = _response("<think>plan</think>Hello", usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2))
    monkeypatch.setattr(llm.litellm, "acompletion", _fake_acompletion(calls, reply=lambda r: raw))
    result = await llm.chat_completion(
        provider="openai_compatible", model="local", endpoint="http://box:8000/v1", api_key=None,
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result["content"] == "Hello"
    assert result["raw"] is raw
    assert result["finish_reason"] == "stop"


@pytest.mark.asyncio(loop_scope="session")
async def test_setup_token_calls_carry_the_claude_code_headers_and_restore_tool_names(monkeypatch):
    calls = []
    call = SimpleNamespace(id="c1", function=SimpleNamespace(name="mcp__srv_tool", arguments="{}"))
    monkeypatch.setattr(llm.litellm, "acompletion", _fake_acompletion(
        calls, reply=lambda r: _response("", [call], "tool_calls")))
    tools = [{"type": "function", "function": {"name": "mcp_srv_tool", "parameters": {"type": "object"}}}]
    result = await llm.chat_completion(
        provider="anthropic", model="claude-sonnet-4-5", endpoint=None, api_key="sk-ant-oat01-token",
        auth_mode="setup-token", messages=_SPLIT, tools=tools,
    )
    sent = calls[0]
    assert sent["api_key"] == "sk-ant-oat01-token"
    assert sent["extra_headers"]["anthropic-beta"] == "claude-code-20250219,oauth-2025-04-20"
    assert sent["extra_headers"]["user-agent"].startswith("claude-cli/")
    assert sent["tools"][0]["function"]["name"] == "mcp__srv_tool"
    assert sent["messages"][0]["content"][0]["text"] == llm._CLAUDE_CODE_IDENTITY
    assert result["tool_calls"] == [{"id": "c1", "name": "mcp_srv_tool", "arguments": {}}]


_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
_FMT = {"type": "json_schema", "json_schema": {"name": "check", "schema": _SCHEMA}}


def test_schema_from_response_format():
    assert llm._schema_from_response_format(_FMT) == ("check", _SCHEMA)
    assert llm._schema_from_response_format({"type": "json_object"}) is None
    assert llm._schema_from_response_format(None) is None


def test_request_rejection_is_recognised():
    assert llm._is_request_rejected(_bad_request("'response_format' is not supported by this model"))
    assert not llm._is_request_rejected(RuntimeError("response_format"))


@pytest.mark.asyncio(loop_scope="session")
async def test_rejection_in_the_providers_own_words_drops_the_schema(monkeypatch):
    # Bedrock turns response_format into a forced tool_choice and rejects that;
    # the message never says response_format.
    calls = []
    monkeypatch.setattr(llm.litellm, "acompletion", _fake_acompletion(calls, rejects=lambda r: (
        'BedrockException - tool_choice: type "tool" and "any" are not supported for this model.'
        if "response_format" in r else None)))
    kwargs = dict(provider="openai_compatible", endpoint="http://gateway.test/v1", api_key="k",
                  messages=[{"role": "user", "content": "hi"}], response_format=_FMT)

    result = await llm.chat_completion(model="claude-thinking", **kwargs)
    assert result["content"] == '{"ok": true}'
    assert [("response_format" in c) for c in calls] == [True, False]
    # Remembered for that model only.
    await llm.chat_completion(model="claude-thinking", **kwargs)
    assert "response_format" not in calls[2]
    await llm.chat_completion(model="other", **kwargs)
    assert "response_format" in calls[3]


@pytest.mark.asyncio(loop_scope="session")
async def test_rejection_not_caused_by_the_schema_is_raised_and_not_remembered(monkeypatch):
    calls = []
    monkeypatch.setattr(llm.litellm, "acompletion", _fake_acompletion(calls, rejects=lambda r: "context length exceeded"))
    with pytest.raises(llm.litellm.BadRequestError, match="context length"):
        await llm.chat_completion(
            provider="openai_compatible", model="m", endpoint="http://x.test/v1", api_key="k",
            messages=[{"role": "user", "content": "hi"}], response_format=_FMT,
        )
    assert [("response_format" in c) for c in calls] == [True, False]
    assert not llm._schema_support_known_missing("http://x.test/v1", "m")


@pytest.mark.asyncio(loop_scope="session")
async def test_chat_completion_drops_reasoning_effort_when_model_rejects_it(monkeypatch):
    calls = []
    monkeypatch.setattr(llm.litellm, "acompletion", _fake_acompletion(calls, rejects=lambda r: (
        "Unrecognized request argument supplied: reasoning_effort" if "reasoning_effort" in r else None)))
    kwargs = dict(provider="openai_compatible", endpoint="http://reasoning.test/v1", api_key="k",
                  messages=[{"role": "user", "content": "hi"}], reasoning_effort="low")
    await llm.chat_completion(model="plain", **kwargs)
    assert [c.get("reasoning_effort") for c in calls] == ["low", None]

    # Remembered for that model only: no second rejection, and another model
    # on the same endpoint still gets asked.
    await llm.chat_completion(model="plain", **kwargs)
    assert "reasoning_effort" not in calls[2]
    await llm.chat_completion(model="thinker", **kwargs)
    assert calls[3].get("reasoning_effort") == "low"


def test_reasoning_rejection_is_recognised():
    assert llm._is_reasoning_unsupported_error(_bad_request("Unsupported parameter: 'reasoning.effort'"))
    assert not llm._is_reasoning_unsupported_error(_bad_request("context length exceeded"))
    assert not llm._is_reasoning_unsupported_error(RuntimeError("reasoning"))


def test_finish_reason_normalisation():
    assert llm._finish_reason("length") == "length"
    assert llm._finish_reason("max_tokens") == "length"
    assert llm._finish_reason("max_output_tokens") == "length"
    assert llm._finish_reason("end_turn") == "stop"
    assert llm._finish_reason("tool_use") == "tool_calls"
    assert llm._finish_reason(None) == "stop"

    class _Enum:
        value = "MAX_TOKENS"

    assert llm._finish_reason(_Enum()) == "length"


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


def _chunk(content=None, tool_calls=None, finish_reason=None, reasoning=None, usage=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls, reasoning_content=reasoning)
    chunk = SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)])
    if usage is not None:
        chunk.usage = usage
    return chunk


def _tool_delta(index, id=None, name=None, arguments=None):
    return SimpleNamespace(index=index, id=id, function=SimpleNamespace(name=name, arguments=arguments))


def _streaming(chunks, sent=None):
    async def acompletion(**request):
        if sent is not None:
            sent.update(request)

        async def events():
            for c in chunks:
                yield c
        return events()
    return acompletion


@pytest.mark.asyncio(loop_scope="session")
async def test_stream_accumulates_text_and_tool_calls(monkeypatch):
    chunks = [
        _chunk("Hel"), _chunk("lo"),
        _chunk(tool_calls=[_tool_delta(0, id="call-1", name="recall", arguments='{"q":')]),
        _chunk(tool_calls=[_tool_delta(0, arguments=' "hi"}')]),
        _chunk(finish_reason="tool_calls"),
    ]
    sent = {}
    monkeypatch.setattr(llm.litellm, "acompletion", _streaming(chunks, sent))
    deltas = []
    result = await llm.stream_chat_completion(
        provider="gemini", model="gemini-2.5-flash", endpoint=None, api_key="k",
        messages=[{"role": "user", "content": "hi"}], on_text_delta=deltas.append,
    )
    assert sent["model"] == "gemini/gemini-2.5-flash" and sent["stream"] is True
    assert sent["stream_options"] == {"include_usage": True}
    assert deltas == ["Hel", "lo"]
    assert result["content"] == "Hello"
    assert result["tool_calls"] == [{"id": "call-1", "name": "recall", "arguments": {"q": "hi"}}]
    assert result["finish_reason"] == "tool_calls"
    assert result["raw"] is None


@pytest.mark.asyncio(loop_scope="session")
async def test_stream_separates_reasoning_from_the_answer(monkeypatch):
    chunks = [_chunk(reasoning="thinking"), _chunk("Hi"), _chunk(finish_reason="stop")]
    monkeypatch.setattr(llm.litellm, "acompletion", _streaming(chunks))
    text, reasoning = [], []
    result = await llm.stream_chat_completion(
        provider="openai_compatible", model="m", endpoint="http://box:8000/v1", api_key=None,
        messages=[{"role": "user", "content": "hi"}],
        on_text_delta=text.append, on_reasoning_delta=reasoning.append,
    )
    assert reasoning == ["thinking"]
    assert text == ["Hi"]
    assert result["content"] == "Hi"


@pytest.mark.asyncio(loop_scope="session")
async def test_stream_keeps_the_final_usage_for_metering(monkeypatch):
    from core.usage import extract_usage

    usage = SimpleNamespace(
        prompt_tokens=7000, completion_tokens=20, total_tokens=7020,
        prompt_tokens_details=SimpleNamespace(cached_tokens=6400),
    )
    chunks = [_chunk("Hello"), _chunk(finish_reason="stop"), SimpleNamespace(choices=[], usage=usage)]
    monkeypatch.setattr(llm.litellm, "acompletion", _streaming(chunks))
    result = await llm.stream_chat_completion(
        provider="openai", model="gpt-4o", endpoint=None, api_key="k",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result["content"] == "Hello"
    assert extract_usage("openai", result["raw"]) == {
        "input_tokens": 7000, "output_tokens": 20, "cache_read_tokens": 6400, "cache_write_tokens": 0,
    }


@pytest.mark.asyncio(loop_scope="session")
async def test_stream_text_completion_yields_deltas(monkeypatch):
    monkeypatch.setattr(llm.litellm, "acompletion", _streaming([_chunk("a"), _chunk("b")]))
    out = [c async for c in llm.stream_text_completion(
        provider="openai", model="gpt-4o", endpoint=None, api_key="k",
        messages=[{"role": "user", "content": "hi"}],
    )]
    assert out == ["a", "b"]


@pytest.mark.asyncio(loop_scope="session")
async def test_stream_text_completion_surfaces_the_providers_error(monkeypatch):
    async def acompletion(**request):
        raise llm.litellm.AuthenticationError(message="bad key", model="m", llm_provider="openai")

    monkeypatch.setattr(llm.litellm, "acompletion", acompletion)
    with pytest.raises(llm.litellm.AuthenticationError):
        async for _ in llm.stream_text_completion(
            provider="openai", model="gpt-4o", endpoint=None, api_key="k",
            messages=[{"role": "user", "content": "hi"}],
        ):
            pass


@pytest.mark.parametrize("first", [_chunk("partial"), _chunk(reasoning="private thought")])
@pytest.mark.asyncio(loop_scope="session")
async def test_stream_is_not_retried_once_anything_reached_the_caller(monkeypatch, first):
    attempts = []

    async def acompletion(**request):
        attempts.append(1)

        async def events():
            yield first
            raise llm.litellm.RateLimitError(message="rate limit", model="m", llm_provider="openai")
        return events()

    monkeypatch.setattr(llm.litellm, "acompletion", acompletion)
    with pytest.raises(llm.litellm.RateLimitError):
        await llm.stream_chat_completion(
            provider="openai", model="gpt-4o", endpoint=None, api_key="k",
            messages=[{"role": "user", "content": "hi"}],
            on_text_delta=lambda t: None, on_reasoning_delta=lambda t: None,
        )
    assert len(attempts) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_stream_is_retried_before_anything_reached_the_caller(monkeypatch):
    attempts = []

    async def acompletion(**request):
        attempts.append(1)
        if len(attempts) == 1:
            raise llm.litellm.RateLimitError(message="rate limit", model="m", llm_provider="openai")

        async def events():
            yield _chunk("ok")
        return events()

    import asyncio

    async def _no_sleep(seconds):
        pass

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(llm.litellm, "acompletion", acompletion)
    result = await llm.stream_chat_completion(
        provider="openai", model="gpt-4o", endpoint=None, api_key="k",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result["content"] == "ok" and len(attempts) == 2


# ---------------------------------------------------------------------------
# Google Code Assist keeps its own client behind the same door
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_code_assist_routes_to_its_own_client():
    messages = [{"role": "system", "content": "S"}, {"role": "user", "content": "hello"}]
    with patch(
        "core.providers.google_code_assist.google_code_assist_completion",
        new_callable=AsyncMock, return_value={"content": "ok", "tool_calls": [], "raw": None},
    ) as completion:
        result = await llm.chat_completion(
            provider="google-antigravity", model="gemini-2.5-flash", endpoint=None,
            api_key='{"token": "tok", "projectId": "proj"}', messages=messages,
        )
    kwargs = completion.await_args.kwargs
    assert (kwargs["access_token"], kwargs["project_id"], kwargs["is_antigravity"]) == ("tok", "proj", True)
    assert kwargs["endpoint"] == "https://cloudcode-pa.googleapis.com"
    assert kwargs["messages"] == messages
    assert result == {"content": "ok", "tool_calls": [], "raw": None, "finish_reason": "stop"}


@pytest.mark.asyncio(loop_scope="session")
async def test_code_assist_stream_delivers_text_deltas():
    async def fake_stream(**kwargs):
        await kwargs["on_text_delta"]("Hel")
        await kwargs["on_text_delta"]("lo")
        return {"content": "Hello", "tool_calls": [], "raw": None}

    deltas = []
    with patch("core.providers.google_code_assist.stream_google_code_assist_completion", side_effect=fake_stream):
        result = await llm.stream_chat_completion(
            provider="google-gemini-cli", model="gemini-2.5-flash", endpoint=None,
            api_key='{"token": "tok", "projectId": "proj"}',
            messages=[{"role": "user", "content": "hi"}], on_text_delta=deltas.append,
        )
    assert deltas == ["Hel", "lo"]
    assert result["content"] == "Hello"
