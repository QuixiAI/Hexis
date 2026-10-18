"""The maintenance worker drains an embedding backlog within one tick instead
of embedding one batch per tick, so new memories become searchable as fast as
they are created."""
from __future__ import annotations

import pytest

from services.worker_service import MaintenanceWorker

pytestmark = pytest.mark.asyncio(loop_scope="session")


class _Pool:
    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _worker() -> MaintenanceWorker:
    worker = MaintenanceWorker.__new__(MaintenanceWorker)
    worker.pool = _Pool()
    return worker


async def test_drain_runs_until_the_backlog_is_empty_and_sums_the_counts():
    results = [
        {"claimed": 50, "embedded": 50, "failed": 0},
        {"claimed": 50, "embedded": 49, "failed": 1},
        {"claimed": 7, "embedded": 7, "failed": 0},
        {"skipped": True, "reason": "no_unembedded_memories"},
    ]
    calls = 0

    async def step(conn):
        nonlocal calls
        calls += 1
        return results[calls - 1]

    totals = await _worker()._drain(step)
    assert calls == 4
    assert totals == {"claimed": 107, "embedded": 106, "failed": 1, "batches": 3}


async def test_drain_reports_an_empty_backlog_as_skipped():
    async def step(conn):
        return {"skipped": True, "reason": "no_unembedded_memories"}

    assert (await _worker()._drain(step)) == {"skipped": True, "reason": "no_unembedded_memories"}


async def test_drain_stops_when_its_time_budget_is_spent():
    calls = 0

    async def step(conn):
        nonlocal calls
        calls += 1
        return {"claimed": 1, "embedded": 1, "failed": 0}

    totals = await _worker()._drain(step, budget_seconds=0.0)
    assert calls == 1
    assert totals == {"claimed": 1, "embedded": 1, "failed": 0, "batches": 1}
