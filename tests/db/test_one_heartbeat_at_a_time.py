"""Only one heartbeat runs at a time, however it is started."""
import asyncio

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


async def _reset(conn, active=None, started="CURRENT_TIMESTAMP"):
    await conn.execute(
        f"UPDATE heartbeat_state SET is_paused = FALSE, current_energy = 20, active_heartbeat_id = $1::uuid,"
        f" last_heartbeat_at = {started} WHERE id = 1",
        active,
    )


async def test_two_heartbeats_started_together_only_one_runs(db_pool):
    async with db_pool.acquire() as a, db_pool.acquire() as b:
        ready = await a.fetchval("SELECT is_agent_configured() AND is_init_complete()")
        if not ready:
            pytest.skip("start_heartbeat needs a configured, initialized agent")
        await _reset(a)
        try:
            results = await asyncio.gather(a.fetchval("SELECT start_heartbeat()"), b.fetchval("SELECT start_heartbeat()"))
            started = [r for r in results if r is not None]
            assert len(started) == 1, results
            assert await a.fetchval("SELECT heartbeat_in_progress()") is True
            assert await a.fetchval("SELECT start_heartbeat()") is None  # still running: refused
        finally:
            await _reset(a)


async def test_a_claim_counts_until_the_longest_run_plus_ten_minutes(db_pool):
    async with db_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("SELECT set_config('heartbeat.claim_timeout_s', '60'::jsonb)")
            await conn.execute("SELECT set_config('rlm.heartbeat.timeout_seconds', '1200'::jsonb)")
            await conn.execute("SELECT set_config('rlm.heartbeat.user_goal_timeout_seconds', '300'::jsonb)")
            await _reset(conn, "00000000-0000-0000-0000-0000000000aa", "CURRENT_TIMESTAMP - interval '25 minutes'")
            assert await conn.fetchval("SELECT heartbeat_in_progress()") is True
            await _reset(conn, "00000000-0000-0000-0000-0000000000aa", "CURRENT_TIMESTAMP - interval '31 minutes'")
            assert await conn.fetchval("SELECT heartbeat_in_progress()") is False  # abandoned
        finally:
            await tx.rollback()
