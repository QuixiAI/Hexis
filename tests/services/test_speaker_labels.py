"""Channel speakers carry their address, and a first meeting is always encoded."""
from __future__ import annotations

import uuid

import pytest

from channels.base import ChannelMessage
from channels.conversation import speaker_label

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _msg(**over) -> ChannelMessage:
    base = dict(
        channel_type="slack",
        channel_id="C1",
        sender_id="U081GMQP0JV",
        sender_name="Ana Ruiz",
        content="hi",
        message_id="m1",
    )
    base.update(over)
    return ChannelMessage(**base)


def test_channel_speakers_are_labelled_with_their_address():
    assert speaker_label(_msg()) == "Ana Ruiz (Slack user U081GMQP0JV)"
    assert speaker_label(_msg(channel_type="telegram", sender_id="12345")) == "Ana Ruiz (Telegram user 12345)"


def test_bare_names_when_there_is_no_separate_address():
    assert speaker_label(_msg(channel_type="web", sender_id="u1")) == "Ana Ruiz"
    assert speaker_label(_msg(sender_id="")) == "Ana Ruiz"
    assert speaker_label(_msg(sender_name="", sender_id="U1")) == "U1"


async def _unit(conn, content: str, importance: float):
    return await conn.fetchval(
        """
        INSERT INTO subconscious_units (content, user_text, assistant_text, importance,
                                        status, idempotency_key)
        VALUES ($1, $1, '', $2, 'active', $3)
        RETURNING id
        """,
        content,
        importance,
        f"test:{uuid.uuid4().hex}",
    )


async def test_first_meeting_is_encoded_below_the_importance_floor(db_pool):
    speaker_id = "U" + uuid.uuid4().hex[:10].upper()
    async with db_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("SELECT set_config('extraction.min_importance', '0.4'::jsonb)")
            # Only this test's units compete for the batch.
            await conn.execute(
                "UPDATE subconscious_units SET extraction_status = 'skipped' WHERE extraction_status = 'pending'"
            )
            first = await _unit(conn, f"Ana Ruiz (Slack user {speaker_id}): hi there", 0.2)
            plain = await _unit(conn, "User: hi there", 0.2)
            claimed = {
                r["id"] for r in await conn.fetch("SELECT id FROM claim_conscious_extraction_batch(50)")
            }
            assert first in claimed, "a speaker nobody remembers yet is encoded regardless of importance"
            assert plain not in claimed
            assert await conn.fetchval(
                "SELECT extraction_status FROM subconscious_units WHERE id = $1", plain
            ) == "skipped"

            # Once the ID is remembered, the usual floor applies again.
            await conn.fetchval(
                """
                INSERT INTO memories (type, content, embedding, importance, trust_level, status)
                VALUES ('semantic', $1, array_fill(0.1, ARRAY[embedding_dimension()])::vector, 0.8, 0.9, 'active')
                RETURNING id
                """,
                f"Ana Ruiz's Slack user ID is {speaker_id}.",
            )
            again = await _unit(conn, f"Ana Ruiz (Slack user {speaker_id}): thanks", 0.2)
            claimed = {
                r["id"] for r in await conn.fetch("SELECT id FROM claim_conscious_extraction_batch(50)")
            }
            assert again not in claimed
        finally:
            await tx.rollback()


async def test_user_label_falls_back_to_the_channel_name_of_the_configured_user(db_pool):
    sender_id = "U" + uuid.uuid4().hex[:10].upper()
    async with db_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(
                "SELECT set_config('channel.slack.operator_user_id', to_jsonb($1::text))", sender_id
            )
            assert await conn.fetchval("SELECT get_user_channel_name()") is None
            await conn.execute(
                """
                INSERT INTO channel_sessions (channel_type, channel_id, sender_id, sender_name)
                VALUES ('slack', 'D123', $1, 'Ana Ruiz')
                """,
                sender_id,
            )
            assert await conn.fetchval("SELECT get_user_channel_name()") == "Ana Ruiz"
        finally:
            await tx.rollback()
