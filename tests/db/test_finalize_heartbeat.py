import json

import pytest

from tests.utils import get_test_identifier

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.db]


async def test_finalize_heartbeat_applies_goal_changes(db_pool, ensure_embedding_service):
    test_id = get_test_identifier("finalize_heartbeat")
    goal_title = f"Finalize Goal {test_id}"

    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE heartbeat_state SET current_energy = 20, is_paused = FALSE WHERE id = 1")

        goal_id = await conn.fetchval(
            "SELECT create_goal($1, $2, $3, $4, $5, $6)",
            goal_title,
            "test goal",
            "curiosity",
            "queued",
            None,
            None,
        )
        assert goal_id is not None

        hb_payload = await conn.fetchval("SELECT start_heartbeat()")
        if isinstance(hb_payload, str):
            hb_payload = json.loads(hb_payload)
        hb_id = hb_payload.get("heartbeat_id")
        assert hb_id is not None

        goal_changes = [
            {"goal_id": str(goal_id), "change": "completed", "reason": "done"}
        ]

        memory_id = await conn.fetchval(
            """
            SELECT finalize_heartbeat($1::uuid, $2, $3::jsonb, $4::jsonb, NULL)
            """,
            hb_id,
            "test finalize",
            json.dumps([]),
            json.dumps(goal_changes),
        )
        assert memory_id is not None

        goal_row = await conn.fetchrow(
            "SELECT status, metadata->>'priority' as priority FROM memories WHERE id = $1::uuid",
            goal_id,
        )
        assert goal_row is not None
        assert goal_row["priority"] == "completed"
        assert goal_row["status"] == "archived"

        hb_row = await conn.fetchrow(
            "SELECT metadata#>>'{context,heartbeat_id}' as heartbeat_id FROM memories WHERE id = $1::uuid",
            memory_id,
        )
        assert hb_row is not None
        assert hb_row["heartbeat_id"] == str(hb_id)
