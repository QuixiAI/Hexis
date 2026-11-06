"""A chat turn that produced no answer keeps the question, marked unanswered."""
from __future__ import annotations

import json
import uuid

import pytest

from services.chat import keep_unanswered_question

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_unanswered_question_is_kept_in_the_transcript(db_pool):
    session_id = str(uuid.uuid4())
    kept = await keep_unanswered_question(
        db_pool,
        session_id=session_id,
        user_message="what did we decide about the launch?",
        surface="api",
        reason="The model returned no answer.",
    )
    assert kept is True
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT role, content, metadata FROM chat_messages WHERE session_id = $1::uuid ORDER BY ordinal",
            session_id,
        )
    assert [r["role"] for r in rows] == ["user"]
    assert rows[0]["content"] == "what did we decide about the launch?"
    metadata = rows[0]["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    assert metadata["unanswered"] is True
    assert metadata["error"] == "The model returned no answer."


async def test_nothing_is_kept_without_a_session(db_pool):
    assert (
        await keep_unanswered_question(
            db_pool, session_id=None, user_message="hi", surface="api", reason="x"
        )
        is False
    )
