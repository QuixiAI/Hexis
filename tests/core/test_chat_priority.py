"""Chat comes first: background model calls wait on the chat-turn lease."""

from __future__ import annotations

import pytest

from core import chat_priority

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


class _Pool:
    def __init__(self, active_for: int, max_wait: int | None = 180):
        self.active_for = active_for  # how many polls report an active turn
        self.polls = 0
        self.max_wait = max_wait

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool

            async def __aexit__(self, *exc):
                return False

        return _Ctx()

    async def fetchval(self, query, *args):
        if "chat_turn_active" in query:
            self.polls += 1
            return self.polls <= self.active_for
        if "priority_max_wait_s" in query:
            return self.max_wait
        return None


async def test_waits_until_the_turn_ends(monkeypatch):
    monkeypatch.setattr(chat_priority, "POLL_SECONDS", 0.01)
    pool = _Pool(active_for=3)
    waited = await chat_priority.wait_for_chat_idle(pool)
    assert pool.polls == 4
    assert waited >= 0.03


async def test_does_not_wait_when_no_turn_is_live(monkeypatch):
    pool = _Pool(active_for=0)
    assert await chat_priority.wait_for_chat_idle(pool) < 0.01
    assert pool.polls == 1


async def test_gives_up_after_the_cap(monkeypatch):
    monkeypatch.setattr(chat_priority, "POLL_SECONDS", 0.01)
    pool = _Pool(active_for=10_000)
    waited = await chat_priority.wait_for_chat_idle(pool, max_wait_seconds=0.05)
    assert 0.05 <= waited < 1.0


async def test_gate_never_blocks_on_a_broken_database(monkeypatch):
    class _Broken:
        async def fetchval(self, *args):
            raise RuntimeError("db down")

    assert await chat_priority.wait_for_chat_idle(_Broken(), max_wait_seconds=1) < 0.01
    await chat_priority.begin_chat_turn(_Broken(), "s")  # logs, does not raise
    await chat_priority.end_chat_turn(_Broken(), "s")


async def test_logs_how_long_it_waited_when_the_turn_ended(monkeypatch, caplog):
    import logging

    monkeypatch.setattr(chat_priority, "POLL_SECONDS", 0.01)
    with caplog.at_level(logging.INFO, logger=chat_priority.logger.name):
        await chat_priority.wait_for_chat_idle(_Pool(active_for=2))
        await chat_priority.wait_for_chat_idle(_Pool(active_for=0))
    waits = [r for r in caplog.records if "waited" in r.getMessage()]
    assert len(waits) == 1
    assert "for the chat turn to finish" in waits[0].getMessage()
