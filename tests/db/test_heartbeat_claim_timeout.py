"""A heartbeat claim left by a stopped worker stops blocking, but not while a
heartbeat may still be running (its longest run plus ten minutes)."""
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _claim(conn, started_ago: str) -> None:
    await conn.execute(
        "UPDATE heartbeat_state SET active_heartbeat_id = gen_random_uuid(), "
        f"last_heartbeat_at = CURRENT_TIMESTAMP - INTERVAL '{started_ago}' WHERE id = 1"
    )


async def test_fresh_claim_blocks_and_stale_claim_does_not(db_pool):
    async with db_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("SELECT set_config('heartbeat.claim_timeout_s', '1800'::jsonb)")
            await conn.execute("SELECT set_config('rlm.heartbeat.timeout_seconds', '300'::jsonb)")
            await conn.execute("SELECT set_config('rlm.heartbeat.user_goal_timeout_seconds', '300'::jsonb)")
            await _claim(conn, "5 minutes")
            assert await conn.fetchval("SELECT heartbeat_in_progress()") is True

            await _claim(conn, "31 minutes")
            assert await conn.fetchval("SELECT heartbeat_in_progress()") is False

            await conn.execute("UPDATE heartbeat_state SET active_heartbeat_id = NULL WHERE id = 1")
            assert await conn.fetchval("SELECT heartbeat_in_progress()") is False
        finally:
            await tx.rollback()


async def test_a_long_heartbeat_still_counts_as_running(db_pool):
    async with db_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            # The RLM heartbeat may run 30 minutes: 35 minutes in it may still be finishing.
            await conn.execute("SELECT set_config('heartbeat.claim_timeout_s', '1800'::jsonb)")
            await conn.execute("SELECT set_config('rlm.heartbeat.timeout_seconds', '1800'::jsonb)")
            await conn.execute("SELECT set_config('rlm.heartbeat.user_goal_timeout_seconds', '300'::jsonb)")
            await _claim(conn, "35 minutes")
            assert await conn.fetchval("SELECT heartbeat_in_progress()") is True
            await _claim(conn, "41 minutes")
            assert await conn.fetchval("SELECT heartbeat_in_progress()") is False  # abandoned
        finally:
            await tx.rollback()


async def test_a_heartbeat_on_the_users_goal_still_counts_as_running(db_pool):
    async with db_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            # A heartbeat working on the user's goal may run user_goal_timeout_seconds
            # (30 minutes by default), longer than an ordinary one and the claim timeout.
            await conn.execute("SELECT set_config('heartbeat.claim_timeout_s', '600'::jsonb)")
            await conn.execute("SELECT set_config('rlm.heartbeat.timeout_seconds', '300'::jsonb)")
            await conn.execute("SELECT set_config('rlm.heartbeat.user_goal_timeout_seconds', '1800'::jsonb)")
            await _claim(conn, "25 minutes")
            assert await conn.fetchval("SELECT heartbeat_in_progress()") is True
            await _claim(conn, "39 minutes")
            assert await conn.fetchval("SELECT heartbeat_in_progress()") is True
            await _claim(conn, "41 minutes")
            assert await conn.fetchval("SELECT heartbeat_in_progress()") is False
        finally:
            await tx.rollback()


async def test_stale_claim_no_longer_stops_the_schedule(db_pool):
    async with db_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("SELECT set_config('heartbeat.heartbeat_interval_minutes', '60'::jsonb)")
            await conn.execute("UPDATE heartbeat_state SET is_paused = FALSE, next_heartbeat_at = NULL WHERE id = 1")
            await _claim(conn, "2 hours")
            configured = await conn.fetchval(
                "SELECT is_agent_configured() AND is_init_complete() AND NOT is_agent_terminated()")
            assert await conn.fetchval("SELECT should_run_heartbeat()") is bool(configured)
        finally:
            await tx.rollback()
