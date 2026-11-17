"""Transient-error retry for LLM calls (the 429 that killed a live turn).

_retry_on_transient recognizes status codes attached to provider errors,
honors Retry-After, and refuses to replay a stream once tokens were delivered.
"""

from __future__ import annotations

import pytest

from core.llm import _retry_on_transient, is_transient_error

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


class _Resp:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self.headers = headers or {}


def _http_error(resp: _Resp, body: bytes) -> RuntimeError:
    """Shape of an SDK/HTTP provider error: status_code + optional retry_after."""
    err = RuntimeError(f"HTTP {resp.status_code}: {body.decode('utf-8', errors='replace')}")
    err.status_code = resp.status_code
    retry_after = resp.headers.get("retry-after")
    if retry_after:
        err.retry_after = float(retry_after)
    return err


async def test_retries_429_and_succeeds(monkeypatch):
    waits: list[float] = []

    async def _no_sleep(seconds):
        waits.append(seconds)

    import asyncio
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(_Resp(429, {"retry-after": "5"}), b'{"detail":"Rate limit exceeded"}')
        return {"content": "ok"}

    result = await _retry_on_transient(factory)
    assert result == {"content": "ok"}
    assert calls["n"] == 3
    # Retry-After 5s is honored (wait >= 5 on both retries).
    assert len(waits) == 2
    assert all(w >= 5.0 for w in waits)


async def test_non_transient_raises_immediately():
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        raise _http_error(_Resp(401), b"unauthorized")

    with pytest.raises(RuntimeError, match="HTTP 401"):
        await _retry_on_transient(factory)
    assert calls["n"] == 1


async def test_should_retry_refuses_stream_replay():
    """Once tokens reached the consumer, a transient error must surface
    instead of replaying the stream (duplicate text)."""
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        raise _http_error(_Resp(429), b"limited")

    with pytest.raises(RuntimeError, match="HTTP 429"):
        await _retry_on_transient(factory, should_retry=lambda: False)
    assert calls["n"] == 1


async def test_litellm_errors_are_classified_by_status():
    import litellm

    assert is_transient_error(litellm.RateLimitError(message="slow down", model="m", llm_provider="openai"))
    assert is_transient_error(litellm.ServiceUnavailableError(message="down", model="m", llm_provider="openai"))
    assert is_transient_error(litellm.Timeout(message="timed out", model="m", llm_provider="openai"))
    assert not is_transient_error(litellm.AuthenticationError(message="bad key", model="m", llm_provider="openai"))
    assert not is_transient_error(litellm.BadRequestError(message="bad schema", model="m", llm_provider="openai"))
