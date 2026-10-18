"""Tests for RLM chat integration."""

import asyncio
import json

import pytest


class TestChatRLMFlagDefault:
    """chat.use_rlm is off by default: the REPL prompt carries no persona or
    memory context, so every chat turn goes through the agent loop. The RLM
    chat path stays available as an opt-in."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_chat_rlm_flag_defaults_false(self, db_pool):
        async with db_pool.acquire() as conn:
            result = await conn.fetchval("SELECT get_config_bool('chat.use_rlm')")
            assert result is False


class TestRLMChatSession:
    """Unit tests for RLM chat session management."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_session_cleanup(self):
        """Stale sessions are cleaned up."""
        import time
        from services.hexis_rlm import _chat_sessions, _session_last_used, _cleanup_stale_sessions, _SESSION_TTL
        from services.rlm_repl import HexisLocalREPL

        # Create a fake stale session
        session_id = "test_stale_session"
        repl = HexisLocalREPL()
        repl.setup(context_payload="test")
        _chat_sessions[session_id] = repl
        _session_last_used[session_id] = time.time() - _SESSION_TTL - 10

        await _cleanup_stale_sessions()

        assert session_id not in _chat_sessions
        assert session_id not in _session_last_used


class TestRLMChatParsing:
    """Test that chat-oriented FINAL parsing works correctly."""

    def test_final_with_plain_text(self):
        """Chat FINAL contains plain text, not JSON."""
        from services.hexis_rlm import find_final_answer

        text = "FINAL(Hello! I remember you mentioned enjoying hiking last time.)"
        answer = find_final_answer(text)
        assert answer is not None
        assert "hiking" in answer

    def test_final_multiline(self):
        """FINAL can span multiple lines."""
        from services.hexis_rlm import find_final_answer

        text = """FINAL(Hello there!

I found some interesting memories about our previous conversations.
Let me share what I discovered.)"""
        answer = find_final_answer(text)
        assert answer is not None
        assert "interesting memories" in answer


class TestRLMChatLoop:
    """Regression tests for chat-specific RLM loop behavior."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_missing_final_var_is_repaired_not_returned(self, monkeypatch):
        import services.hexis_rlm as rlm
        from services.rlm_repl import HexisLocalREPL

        responses = iter([
            "FINAL_VAR(response)",
            "FINAL(Hi hon.)",
        ])

        async def fake_completion(messages, llm_config, max_tokens=4096):
            return next(responses)

        monkeypatch.setattr(rlm, "_llm_completion", fake_completion)

        repl = HexisLocalREPL()
        repl.setup(context_payload="hi hon")
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                rlm._run_loop,
                repl,
                {"provider": "test", "model": "test"},
                loop,
                "system",
                3,
                False,
            )
        finally:
            repl.cleanup()

        assert result["final_answer"] == "Hi hon."
        assert "Variable 'response' not found" not in result["final_answer"]
        assert result["iterations"] == 2

    @pytest.mark.asyncio(loop_scope="session")
    async def test_same_turn_final_var_is_accepted_for_chat(self, monkeypatch):
        import services.hexis_rlm as rlm
        from services.rlm_repl import HexisLocalREPL

        async def fake_completion(messages, llm_config, max_tokens=4096):
            return """```repl
response = "Hi hon."
```
FINAL_VAR(response)"""

        monkeypatch.setattr(rlm, "_llm_completion", fake_completion)

        repl = HexisLocalREPL()
        repl.setup(context_payload="hi hon")
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                rlm._run_loop,
                repl,
                {"provider": "test", "model": "test"},
                loop,
                "system",
                3,
                False,
                True,
            )
        finally:
            repl.cleanup()

        assert result["final_answer"] == "Hi hon."
        assert result["iterations"] == 1
