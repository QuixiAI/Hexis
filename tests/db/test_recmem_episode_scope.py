"""RecMem episodes stay within one person's conversation and keep turns that produced facts."""
from __future__ import annotations

import pytest

from tests.utils import get_test_identifier

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


class _Rollback(Exception):
    pass


async def test_scene_keeps_a_turn_that_only_produced_a_fact(db_pool):
    marker = get_test_identifier("scene")
    scene_units: list = []
    ids: list = []
    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                assert await conn.fetchval(
                    "SELECT _recmem_speaker('Mia Tanaka (Slack user U1): hi\n\nHexis: hello')"
                ) == "Mia Tanaka (Slack user U1)"
                session = await conn.fetchval("INSERT INTO chat_sessions (title) VALUES ($1) RETURNING id", marker)
                for i, text in enumerate(("hello", "the review is due Nov 14", "already in an episode")):
                    ids.append(await conn.fetchval(
                        """INSERT INTO subconscious_units (content, idempotency_key, session_id, turn_at,
                                                          route_status, embedding_status, status)
                           VALUES ($1, $2, $3, CURRENT_TIMESTAMP - INTERVAL '2 hours', 'raw_only', 'embedded', 'active')
                           RETURNING id""",
                        f"Mia Tanaka: {text}", f"{marker}-{i}", session))
                fact = await conn.fetchval("SELECT create_semantic_memory($1, 0.9)", f"{marker} review due")
                episode = await conn.fetchval("SELECT create_episodic_memory($1)", f"{marker} episode")
                await conn.fetchval("SELECT link_memory_to_source_unit($1, $2, 'source')", fact, ids[1])
                await conn.fetchval("SELECT link_memory_to_source_unit($1, $2, 'source')", episode, ids[2])
                rows = await conn.fetch("SELECT session_id, unit_ids FROM find_idle_scene_sessions(1000)")
                scene_units = next((r["unit_ids"] for r in rows if r["session_id"] == session), [])
                raise _Rollback  # shared database: leave it as it was
    except _Rollback:
        pass
    # The fact's turn stays in the scene; the episode's turn does not.
    assert scene_units == [ids[0], ids[1]]
