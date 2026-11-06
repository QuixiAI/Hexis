"""Raw transcripts are deleted after their retention limits; memories and ingested documents are not."""
from __future__ import annotations

import pytest

from tests.utils import get_test_identifier

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


class _Rollback(Exception):
    pass


async def test_old_transcripts_go_and_everything_else_stays(db_pool):
    marker = get_test_identifier("ret")
    old, new = "CURRENT_TIMESTAMP - INTERVAL '400 days'", "CURRENT_TIMESTAMP - INTERVAL '10 days'"
    remaining: dict[str, bool] = {}
    result = {}
    ids = {}
    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                for name, age in (("trace_old", "CURRENT_TIMESTAMP - INTERVAL '40 days'"), ("trace_new", new)):
                    ids[name] = await conn.fetchval(
                        f"INSERT INTO agent_turns (mode, status, completed_at) VALUES ('chat', 'completed', {age}) RETURNING id")
                ids["trace_running"] = await conn.fetchval(
                    "INSERT INTO agent_turns (mode, status, updated_at) VALUES ('chat', 'running', CURRENT_TIMESTAMP - INTERVAL '40 days') RETURNING id")
                for name, age in (("chat_old", old), ("chat_new", new)):
                    ids[name] = await conn.fetchval(
                        f"INSERT INTO chat_sessions (title, last_active_at) VALUES ($1, {age}) RETURNING id", marker)
                    await conn.execute(
                        "INSERT INTO chat_messages (session_id, ordinal, role, content) VALUES ($1, 0, 'user', 'hi')", ids[name])
                for name, age in (("channel_old", old), ("channel_new", new)):
                    ids[name] = await conn.fetchval(
                        f"INSERT INTO channel_sessions (channel_type, channel_id, sender_id, last_active) "
                        f"VALUES ('telegram', $1, $1, {age}) RETURNING id", f"{marker}-{name}")
                    await conn.execute(
                        "INSERT INTO channel_messages (session_id, direction, content) VALUES ($1, 'inbound', 'hi')", ids[name])
                for name, kind, age in (("doc_message_old", "channel_message", old),
                                        ("doc_message_new", "channel_message", new),
                                        ("doc_file_old", "file", old)):
                    ids[name] = await conn.fetchval(
                        f"INSERT INTO source_documents (title, source_type, content_hash, content, created_at) "
                        f"VALUES ($1, $2, $3, 'x', {age}) RETURNING id", marker, kind, f"{marker}-{name}")
                for name, age, extraction, route, pinned in (
                    ("raw_settled_old", old, "extracted", "raw_only", "NULL"),
                    ("raw_unfinished_old", old, "pending", "unrouted", "NULL"),
                    ("raw_pinned_old", old, "extracted", "episode_created", "CURRENT_TIMESTAMP"),
                    ("raw_settled_new", new, "extracted", "raw_only", "NULL"),
                ):
                    ids[name] = await conn.fetchval(
                        f"INSERT INTO subconscious_units (content, idempotency_key, turn_at, extraction_status, route_status, pinned_at) "
                        f"VALUES ('hi', $1, {age}, $2, $3, {pinned}) RETURNING id", f"{marker}-{name}", extraction, route)

                result = await conn.fetchval("SELECT run_transcript_retention()")
                tables = {"trace": "agent_turns", "chat": "chat_sessions", "channel": "channel_sessions",
                          "doc": "source_documents", "raw": "subconscious_units"}
                for name, row_id in ids.items():
                    table = tables[name.split("_")[0]]
                    remaining[name] = await conn.fetchval(f"SELECT EXISTS (SELECT 1 FROM {table} WHERE id = $1)", row_id)
                remaining["chat_old_messages"] = await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM chat_messages WHERE session_id = $1)", ids["chat_old"])
                raise _Rollback  # the test database is shared; leave it as it was
    except _Rollback:
        pass

    assert remaining == {
        "trace_old": False, "trace_new": True, "trace_running": True,
        "chat_old": False, "chat_new": True, "chat_old_messages": False,
        "channel_old": False, "channel_new": True,
        "doc_message_old": False, "doc_message_new": True, "doc_file_old": True,
        "raw_settled_old": False, "raw_unfinished_old": True, "raw_pinned_old": True, "raw_settled_new": True,
    }, result


async def test_the_switch_and_forever_keep_everything(db_pool):
    off = forever = ""
    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('retention.transcripts_enabled', 'false'::jsonb)")
                off = await conn.fetchval("SELECT run_transcript_retention()")
                await conn.execute("SELECT set_config('retention.transcripts_enabled', 'true'::jsonb)")
                for key in ("turn_trace_days", "conversation_days", "raw_turn_days"):
                    await conn.execute(f"SELECT set_config('retention.{key}', '0'::jsonb)")
                forever = await conn.fetchval("SELECT run_transcript_retention()")
                raise _Rollback
    except _Rollback:
        pass
    assert '"skipped": true' in off
    assert all(f'"{k}": 0' in forever for k in ("turn_traces", "chat_sessions", "channel_sessions", "message_documents", "raw_turns"))
