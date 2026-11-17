"""Heartbeat actions that carry text fail alone when the text is missing (0031)."""
import json

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.parametrize(
    ("action", "params", "missing"),
    [
        ("synthesize", {"topic": "New-rep ramp framework"}, "params.content"),
        ("reach_out_user", {"intent": "nudge"}, "params.message"),
        ("synthesize", {"content": "   "}, "params.content"),
    ],
)
async def test_action_without_its_text_returns_an_error(db_pool, action, params, missing):
    async with db_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(
                "SELECT set_config('heartbeat.allowed_actions', $1::jsonb)", json.dumps([action])
            )
            result = json.loads(await conn.fetchval(
                "SELECT execute_heartbeat_action(gen_random_uuid(), $1, $2::jsonb)",
                action, json.dumps(params),
            ))
            assert result["success"] is False
            assert missing in result["error"]
        finally:
            await tx.rollback()


async def test_synthesize_with_content_still_creates_a_memory(db_pool):
    async with db_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("SELECT set_config('heartbeat.allowed_actions', '[\"synthesize\"]'::jsonb)")
            result = json.loads(await conn.fetchval(
                "SELECT execute_heartbeat_action(gen_random_uuid(), 'synthesize', $1::jsonb)",
                json.dumps({"content": "Ramp plans work best with a first-week shadow call.", "topic": "ramp"}),
            ))
            assert result["success"] is True
            assert result["result"]["synthesis_memory_id"]
        finally:
            await tx.rollback()
