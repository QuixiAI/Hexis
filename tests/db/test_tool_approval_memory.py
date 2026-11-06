"""Changing what the agent may do unasked is something it remembers."""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


class _Rollback(Exception):
    pass


async def test_granting_and_revoking_approval_leave_the_agent_a_memory(db_pool):
    notes = []
    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT revoke_tool_approval('zz_test_tool')")
                assert await conn.fetchval("SELECT grant_tool_approval('zz_test_tool')") is True
                assert await conn.fetchval("SELECT grant_tool_approval('zz_test_tool')") is False  # no change, no note
                assert await conn.fetchval("SELECT revoke_tool_approval('zz_test_tool')") is True
                notes = [row["content"] for row in await conn.fetch(
                    "SELECT content FROM memories WHERE type = 'episodic' AND content LIKE '%zz_test_tool%' ORDER BY created_at"
                )]
                raise _Rollback
    except _Rollback:
        pass
    assert len(notes) == 2
    assert "without asking" in notes[0] and "requires approval again" in notes[1]
