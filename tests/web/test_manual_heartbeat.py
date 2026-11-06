"""A manual heartbeat finishes even when the page that started it closes."""
import asyncio
import json
from contextlib import asynccontextmanager

import pytest

import apps.hexis_api as api

pytestmark = pytest.mark.asyncio(loop_scope="session")


class _Conn:
    def __init__(self, calls):
        self.calls = calls

    async def fetchrow(self, query, *args):
        return {
            "configured": True,
            "initialized": True,
            "terminated": False,
            "is_paused": False,
            "running": False,
        }

    async def fetchval(self, query, *args):
        self.calls.append(query.split("(")[0].replace("SELECT ", "").strip())
        if "start_heartbeat" in query:
            return json.dumps({"heartbeat_id": "hb-1", "heartbeat_number": 7})
        return None


class _Pool:
    def __init__(self):
        self.calls = []

    @asynccontextmanager
    async def acquire(self):
        yield _Conn(self.calls)


async def _fake_registry(pool):
    return object()


async def test_heartbeat_completes_after_the_stream_is_closed(monkeypatch):
    import services.heartbeat_agentic as agentic

    finished = asyncio.Event()

    async def fake_run(conn, **kwargs):
        await asyncio.sleep(0.05)
        return {"text": "done", "tool_calls_made": [], "energy_spent": 1}

    async def fake_finalize(conn, *, heartbeat_id, result):
        finished.set()
        return {"memory_id": "m-1"}

    monkeypatch.setattr(agentic, "run_agentic_heartbeat", fake_run)
    monkeypatch.setattr(agentic, "finalize_heartbeat", fake_finalize)
    monkeypatch.setattr(api, "create_full_registry", _fake_registry)

    pool = _Pool()
    stream = api._stream_manual_heartbeat(pool)
    first = await stream.__anext__()
    assert "heartbeat_start" in first
    await stream.aclose()  # the page goes away

    await asyncio.wait_for(finished.wait(), timeout=2)
    assert "start_heartbeat" in pool.calls
    assert "release_active_heartbeat" not in pool.calls


async def test_failed_heartbeat_releases_its_claim(monkeypatch):
    import services.heartbeat_agentic as agentic

    async def failing_run(conn, **kwargs):
        raise RuntimeError("model unreachable")

    monkeypatch.setattr(agentic, "run_agentic_heartbeat", failing_run)
    monkeypatch.setattr(api, "create_full_registry", _fake_registry)

    pool = _Pool()
    messages = [m async for m in api._stream_manual_heartbeat(pool)]
    assert any("model unreachable" in m for m in messages)
    assert "release_active_heartbeat" in pool.calls
