"""An interrupted response stays available until a subsequent successful turn."""

import uuid
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_failed_reply_survives_age_and_clears_after_success(db_pool):
    session = uuid.uuid4()
    other = uuid.uuid4()
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO agent_turns(mode,session_id,status,created_at)
                VALUES ('chat',$1,'completed',NOW()-INTERVAL '3 years'),
                       ('chat',$1,'failed',NOW()-INTERVAL '2 years'),
                       ('chat',$1,'failed',NOW()-INTERVAL '1 year'),
                       ('chat',$2,'failed',NOW())
            """,
                session,
                other,
            )
            context = await conn.fetchval(
                "SELECT render_chat_runtime_context($1)", str(session)
            )
            assert "2 previous response attempt(s)" in context
            assert "do not assume it completed" in context
            assert (
                await conn.fetchval(
                    "SELECT render_chat_runtime_context($1)", str(uuid.uuid4())
                )
                == ""
            )
            await conn.execute(
                "INSERT INTO agent_turns(mode,session_id,status,created_at) VALUES ('chat',$1,'completed',NOW())",
                session,
            )
            assert (
                await conn.fetchval(
                    "SELECT render_chat_runtime_context($1)", str(session)
                )
                == ""
            )
            assert "1 previous response attempt(s)" in await conn.fetchval(
                "SELECT render_chat_runtime_context($1)", str(other)
            )
