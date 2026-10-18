"""The system prompt has a stable prefix that providers can cache.

Providers bill a stable prefix once and reuse it — OpenAI and Gemini 2.5+
automatically, Anthropic via an explicit `cache_control` breakpoint. All need every
volatile part to come *after* everything stable. The prompt previously interleaved
them: a live `## Now` timestamp sat two-thirds of the way up, so the prefix changed
on every single turn and nothing could ever be reused.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core import llm
from core.llm import _anthropic_system_blocks, _extract_system_parts
from core.usage import extract_usage
from services.agent import SystemPrompt


class TestSystemPromptCarrier:
    def test_it_is_still_the_whole_prompt(self):
        """Every existing consumer treats this as a plain string."""
        sp = SystemPrompt("STABLE", "VOLATILE")
        assert isinstance(sp, str)
        assert sp == "STABLE\n\nVOLATILE"
        assert str(sp) == "STABLE\n\nVOLATILE"

    def test_it_carries_the_boundary(self):
        sp = SystemPrompt("STABLE", "VOLATILE")
        assert sp.stable == "STABLE"
        assert sp.volatile == "VOLATILE"

    def test_an_empty_volatile_half_does_not_leave_a_trailing_gap(self):
        assert SystemPrompt("STABLE", "") == "STABLE"


class TestTheCacheableProperty:
    """The one property that matters: the prefix must not change between turns."""

    def _prompt(self, *, now: str, who: str, skills: str) -> SystemPrompt:
        stable = "IDENTITY\n\nWORLDVIEW\n\nPERSONA"
        volatile = f"## Now\n{now}\n\n{who}\n\n{skills}"
        return SystemPrompt(stable, volatile)

    def test_prefix_is_byte_identical_across_turns_that_differ_completely(self):
        first = self._prompt(now="2026-08-23 09:00", who="Eric", skills="calendar")
        second = self._prompt(now="2026-08-23 17:42", who="Sarah Chen", skills="research")

        assert first.stable == second.stable, "the cacheable prefix must not vary"
        assert first.volatile != second.volatile, "the turn-specific half must vary"
        assert first != second


class TestSystemPartsSurviveTheLLMLayer:
    def test_parts_are_kept_separate_rather_than_flattened(self):
        messages = [
            {"role": "system", "content": "STABLE"},
            {"role": "system", "content": "VOLATILE"},
            {"role": "user", "content": "hi"},
        ]
        parts, rest = _extract_system_parts(messages)
        assert parts == ["STABLE", "VOLATILE"]
        assert [m["role"] for m in rest] == ["user"]

    def test_blank_system_messages_are_dropped(self):
        parts, _ = _extract_system_parts(
            [{"role": "system", "content": "  "}, {"role": "system", "content": "REAL"}]
        )
        assert parts == ["REAL"]


_MESSAGES = [
    {"role": "system", "content": "STABLE"},
    {"role": "system", "content": "VOLATILE"},
    {"role": "user", "content": "hello"},
]


class TestAnthropicBreakpoint:
    def test_the_marker_lands_on_the_last_stable_block(self):
        blocks = _anthropic_system_blocks(["STABLE", "VOLATILE"])
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in blocks[1], "the volatile tail is never cached"

    def test_a_single_part_has_nothing_to_cache_against(self):
        blocks = _anthropic_system_blocks(["ONLY"])
        assert blocks == [{"type": "text", "text": "ONLY"}]

    def test_no_system_message_stays_none(self):
        assert _anthropic_system_blocks([]) is None

    @pytest.mark.parametrize("auth_mode", [None, "setup-token"])
    @pytest.mark.asyncio
    async def test_both_anthropic_paths_send_the_breakpoint(self, auth_mode):
        """Both Anthropic paths cache, or OAuth users silently get nothing."""
        sent = {}

        async def acompletion(**request):
            sent.update(request)
            choice = SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=[]), finish_reason="stop")
            return SimpleNamespace(choices=[choice])

        with patch.object(llm.litellm, "acompletion", acompletion):
            await llm.chat_completion(
                provider="anthropic", model="claude-sonnet-4-5", endpoint=None,
                api_key="sk-ant-oat01-x" if auth_mode else "sk-ant-api", auth_mode=auth_mode,
                messages=_MESSAGES,
            )
        blocks = sent["messages"][0]["content"]
        marked = [b for b in blocks if "cache_control" in b]
        assert len(marked) == 1
        assert marked[0]["text"] == "STABLE"
        assert blocks[-1]["text"] == "VOLATILE"
        assert "cache_control" not in blocks[-1]

    @pytest.mark.asyncio
    async def test_the_stream_caches_like_the_plain_call(self):
        sent = {}

        async def acompletion(**request):
            sent.update(request)

            async def events():
                delta = SimpleNamespace(content="ok", tool_calls=None, reasoning_content=None)
                yield SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason="stop")])
            return events()

        with patch.object(llm.litellm, "acompletion", acompletion):
            await llm.stream_chat_completion(
                provider="anthropic", model="claude-sonnet-4-5", endpoint=None, api_key="k", messages=_MESSAGES,
            )
        assert sent["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_a_plain_string_is_passed_through_unchanged(self):
        """No split means no cacheable boundary — so do not reshape the wire.

        The OAuth flow is a validated path: Anthropic checks the identity
        preamble. A prompt with nothing to cache keeps exactly the shape it has
        always had rather than becoming blocks for no gain.
        """
        one = [{"role": "system", "content": "ONE"}, {"role": "user", "content": "hi"}]
        plain = llm._litellm_messages(one, provider="anthropic", model="claude-sonnet-4-5", auth_mode=None)
        assert plain[0]["content"] == "ONE"
        oauth = llm._litellm_messages(one, provider="anthropic", model="claude-sonnet-4-5", auth_mode="setup-token")
        assert isinstance(oauth[0]["content"], str)
        assert oauth[0]["content"].startswith(llm._CLAUDE_CODE_IDENTITY)


class TestOtherProvidersPrefixCaching:
    """OpenAI and Gemini cache a stable prefix on their own: the system parts
    reach LiteLLM in order, stable first, and are never reshuffled."""

    @pytest.mark.asyncio
    async def test_public_api_keeps_stable_prefix_before_volatile_tail(self):
        sent = {}

        async def acompletion(**request):
            sent.update(request)
            choice = SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=[]), finish_reason="stop")
            return SimpleNamespace(choices=[choice])

        with patch.object(llm.litellm, "acompletion", acompletion):
            await llm.chat_completion(
                provider="gemini", model="gemini-2.5-flash", endpoint=None, api_key="test", messages=_MESSAGES,
            )
        assert [m["content"] for m in sent["messages"] if m["role"] == "system"] == ["STABLE", "VOLATILE"]

    @pytest.mark.asyncio
    async def test_stream_preserves_final_usage(self):
        usage = SimpleNamespace(
            prompt_tokens=7000, completion_tokens=20,
            prompt_tokens_details=SimpleNamespace(cached_tokens=6400),
        )

        async def acompletion(**request):
            async def chunks():
                for text in ("Hel", "lo"):
                    delta = SimpleNamespace(content=text, tool_calls=None, reasoning_content=None)
                    yield SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=None)])
                yield SimpleNamespace(choices=[], usage=usage)
            return chunks()

        with patch.object(llm.litellm, "acompletion", acompletion):
            result = await llm.stream_chat_completion(
                provider="gemini", model="gemini-2.5-flash", endpoint=None, api_key="test", messages=_MESSAGES,
            )
        assert result["content"] == "Hello"
        assert extract_usage("gemini", result["raw"]) == {
            "input_tokens": 7000,
            "output_tokens": 20,
            "cache_read_tokens": 6400,
            "cache_write_tokens": 0,
        }

    @pytest.mark.asyncio
    async def test_code_assist_does_not_override_provider_prompt_assembly(self):
        with patch(
            "core.providers.google_code_assist.google_code_assist_completion",
            new_callable=AsyncMock,
            return_value={"content": "ok", "tool_calls": [], "raw": None},
        ) as completion:
            await llm.chat_completion(
                provider="google-gemini-cli",
                model="gemini-2.5-flash",
                endpoint=None,
                api_key=json.dumps({"token": "test", "projectId": "project"}),
                messages=_MESSAGES,
            )

        kwargs = completion.await_args.kwargs
        assert kwargs["messages"] == _MESSAGES
        assert "system_prompt" not in kwargs
