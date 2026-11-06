"""Work on the person's goals is free; the agent's own goals cost energy."""
from __future__ import annotations

import asyncio
import json

import pytest

from core.tools.base import ToolResult
from core.tools.repl_bridge import ReplToolBridge

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


async def _goal(conn, title: str, by_user: bool, priority: str = "queued") -> str:
    if not by_user:
        return str(await conn.fetchval("SELECT create_goal($1, NULL, 'curiosity', $2::goal_priority)", title, priority))
    return str(await conn.fetchval(
        "SELECT create_goal($1, NULL, 'user_request', $2::goal_priority, NULL, NULL, 'user_request'::goal_source)",
        title, priority,
    ))


async def test_goals_record_whether_the_person_set_them(db_pool):
    async with db_pool.acquire() as conn:
        ids = {
            "own": await _goal(conn, "zz goal by the agent", False),
            "user": await _goal(conn, "zz goal by the person", True),
        }
        try:
            got = {k: await conn.fetchval("SELECT goal_origin::text FROM memories WHERE id = $1::uuid", v)
                   for k, v in ids.items()}
        finally:
            await conn.execute("DELETE FROM memories WHERE id = ANY($1::uuid[])", list(ids.values()))
    assert got == {"own": "derived", "user": "user_request"}


class _Registry:
    """Executes any tool at 3 energy, like the shell."""

    def __init__(self, pool):
        self.pool = pool
        self.seen_energy: list[int | None] = []

    async def execute(self, name, args, context):
        self.seen_energy.append(context.energy_available)
        result = ToolResult.success_result({"ran": name})
        result.energy_spent = 3
        return result


async def test_work_on_the_persons_goal_costs_no_energy_and_own_goals_do(db_pool):
    async with db_pool.acquire() as conn:
        theirs = await _goal(conn, "zz the person's goal", True)
        own = await _goal(conn, "zz the agent's goal", False)
    registry = _Registry(db_pool)
    bridge = ReplToolBridge(registry=registry, loop=asyncio.get_running_loop(), initial_energy=5)
    try:
        def run():
            focus = bridge.tool_use("work_on_goal", {"goal_id": theirs})
            calls = [bridge.tool_use("shell", {"command": "true"}) for _ in range(4)]  # 12 energy, more than 5
            switch = bridge.tool_use("work_on_goal", {"goal_id": own})
            paid = bridge.tool_use("shell", {"command": "true"})
            return focus, calls, switch, paid

        focus, calls, switch, paid = await asyncio.to_thread(run)
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM memories WHERE id = ANY($1::uuid[])", [theirs, own])
    assert focus["output"]["free"] is True and focus["output"]["created_by"] == "user"
    assert all(c["success"] and c["energy_spent"] == 0 for c in calls)
    assert switch["output"]["free"] is False and switch["output"]["created_by"] == "agent"
    assert paid["energy_spent"] == 3
    assert bridge.energy_remaining() == 2  # only the agent's own call was charged


async def test_the_daily_cap_ends_free_work(db_pool):
    async with db_pool.acquire() as conn:
        goal = await _goal(conn, "zz capped goal of the person", True)
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("SELECT set_config('heartbeat.free_work_daily_token_cap', '1'::jsonb)")
            await conn.execute(
                "INSERT INTO api_usage (provider, model, operation, input_tokens, output_tokens, source)"
                " VALUES ('test', 'test', 'test', 5, 5, 'heartbeat')")
            status = await conn.fetchval("SELECT free_goal_work_status()")
        finally:
            await tx.rollback()
            await conn.execute("DELETE FROM memories WHERE id = $1::uuid", goal)
    assert json.loads(status)["free"] is False


async def test_an_active_goal_of_the_person_gets_the_larger_heartbeat_limits(db_pool):
    from services.hexis_rlm import _heartbeat_limits

    class Registry:
        pool = db_pool

    async with db_pool.acquire() as conn:
        base = await _heartbeat_limits(Registry())
        goal = await _goal(conn, "zz active goal of the person", True, "active")
        try:
            bigger = await _heartbeat_limits(Registry())
        finally:
            await conn.execute("DELETE FROM memories WHERE id = $1::uuid", goal)
    assert bigger[0] > base[0] and bigger[1] > base[1]
