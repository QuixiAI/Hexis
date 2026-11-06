"""The ChatGPT (Codex) sign-in stays Hexis's; LiteLLM's chatgpt/ provider only reads it."""
from __future__ import annotations

import asyncio
import base64
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core import llm
from core.auth import openai_codex as codex
from core.auth import store

pytestmark = [pytest.mark.core, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture(autouse=True)
def isolated_auth(tmp_path, monkeypatch):
    """Never touch the real auth store or LiteLLM's real token file."""
    monkeypatch.setattr(store, "AUTH_DIR", tmp_path / "auth")
    monkeypatch.setenv("CHATGPT_TOKEN_DIR", str(tmp_path / "litellm-chatgpt"))


def credentials(expires=None):
    payload = base64.urlsafe_b64encode(
        json.dumps({"https://api.openai.com/auth": {"chatgpt_account_id": "test"}}).encode()
    ).decode().rstrip("=")
    return codex.OpenAICodexCredentials(f"x.{payload}.x", "refresh-secret", expires or int(time.time() * 1000) + 600000, "test")


async def test_refresh_is_serialized_without_blocking_event_loop(monkeypatch):
    codex.save_openai_codex_credentials(credentials(expires=1))
    refreshed = credentials()

    async def refresh(token):
        assert token == "refresh-secret"
        await asyncio.sleep(.02)
        return refreshed

    mock = AsyncMock(side_effect=refresh)
    monkeypatch.setattr(codex, "refresh_openai_codex_token", mock)
    results = await asyncio.wait_for(asyncio.gather(*(codex.ensure_fresh_openai_codex_credentials() for _ in range(3))), 2)
    assert results == [refreshed] * 3
    mock.assert_awaited_once()


async def test_a_missing_sign_in_names_the_command():
    with pytest.raises(RuntimeError, match="hexis auth openai-codex login"):
        await codex.ensure_fresh_openai_codex_credentials()


async def test_codex_calls_go_through_litellm_chatgpt_after_a_fresh_refresh(monkeypatch):
    fresh = AsyncMock(return_value=credentials())
    monkeypatch.setattr(codex, "ensure_fresh_openai_codex_credentials", fresh)
    calls = []

    def chunk(content=None, tool_calls=None, finish_reason=None):
        delta = SimpleNamespace(content=content, tool_calls=tool_calls, reasoning_content=None)
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)])

    async def acompletion(**request):
        calls.append(request)
        if request.get("stream"):
            async def events():
                yield chunk("Hello")
            return events()
        call = SimpleNamespace(id="call1", function=SimpleNamespace(name="recall", arguments='{"query":"test"}'))
        message = SimpleNamespace(content="", tool_calls=[call])
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="tool_calls")])

    monkeypatch.setattr(llm.litellm, "acompletion", acompletion)
    args = dict(provider="openai_codex", model="m", endpoint="https://wrong.test", api_key="stale",
                messages=[{"role": "user", "content": "hello"}])
    assert (await llm.chat_completion(**args))["tool_calls"] == [{"id": "call1", "name": "recall", "arguments": {"query": "test"}}]
    delta = []
    assert (await llm.stream_chat_completion(**args, on_text_delta=delta.append))["content"] == "Hello"
    assert delta == ["Hello"]
    assert [c async for c in llm.stream_text_completion(**args)] == ["Hello"]
    assert fresh.await_count == 3
    for request in calls:
        # Always the Responses route, and never a typed key or custom endpoint:
        # LiteLLM reads the refreshed token from its own file.
        assert request["model"] == "chatgpt/responses/m"
        assert "api_key" not in request and "api_base" not in request


async def test_tokens_are_written_where_litellms_chatgpt_reader_finds_them(tmp_path):
    from litellm.llms.chatgpt.authenticator import Authenticator

    creds = credentials()
    codex.save_openai_codex_credentials(creds)
    auth_file = tmp_path / "litellm-chatgpt" / "auth.json"
    assert auth_file.stat().st_mode & 0o777 == 0o600
    # LiteLLM's own reader takes our token and account without a login or refresh.
    reader = Authenticator()
    reader._login_device_code = lambda: pytest.fail("LiteLLM tried to log in by itself")
    reader._refresh_tokens = lambda *_: pytest.fail("LiteLLM tried to refresh by itself")
    assert reader.get_access_token() == creds.access
    assert reader.get_account_id() == "test"

    # A call refreshes through us and rewrites LiteLLM's copy if it went missing.
    auth_file.unlink()
    await codex.ensure_fresh_openai_codex_credentials()
    assert json.loads(auth_file.read_text())["access_token"] == creds.access

    codex.delete_openai_codex_credentials()
    assert not auth_file.exists()


async def test_litellm_never_waits_for_a_device_code_login(monkeypatch):
    """A worker has nobody to enter a login code; a missing token file fails the call instead of hanging."""
    from litellm.llms.chatgpt.authenticator import Authenticator

    auth = Authenticator()
    monkeypatch.setattr(auth, "_request_device_code", lambda: pytest.fail("asked for a device code"))
    with pytest.raises(RuntimeError, match="hexis auth openai-codex login"):
        auth.get_access_token()


async def test_an_unchanged_token_file_is_not_rewritten(tmp_path):
    creds = credentials()
    codex._write_litellm_tokens(creds)
    target = tmp_path / "litellm-chatgpt" / "auth.json"
    before = target.stat().st_ino
    codex._write_litellm_tokens(creds)
    assert target.stat().st_ino == before


async def test_the_codex_prompt_is_replaced_by_a_persona_agnostic_one():
    import os

    instructions = os.environ["CHATGPT_DEFAULT_INSTRUCTIONS"]
    assert "Codex" not in instructions and "coding agent" not in instructions
    assert "Hexis" in instructions
