from __future__ import annotations

import json
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.db]


def _j(value):
    return json.loads(value) if isinstance(value, str) else value


async def test_channel_messages_preserve_raw_source_and_queue_inbound_ingestion(db_pool):
    marker = uuid4().hex
    content = f"Channel source artifact {marker}: remember the cedar clause."

    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            # Off by default (live turns are remembered once, by the encoder);
            # this test covers the queueing path when it is switched on.
            await conn.execute(
                "SELECT set_config('channel.source_artifacts_enqueue_inbound', 'true'::jsonb)"
            )
            prepared = _j(await conn.fetchval(
                "SELECT prepare_channel_turn($1::jsonb)",
                json.dumps({
                    "channel_type": "telegram",
                    "channel_id": f"group-{marker}",
                    "sender_id": f"user-{marker}",
                    "sender_name": "Channel Tester",
                    "content": content,
                    "message_id": f"in-{marker}",
                    "reply_to_id": "prior-message",
                    "thread_id": "topic-1",
                    "attachments": [{"filename": "note.txt", "size": 12}],
                    "metadata": {"is_group": True, "adapter": "unit"},
                    "is_group": True,
                    "timestamp": "2026-07-20T10:00:00+00:00",
                }),
            ))

            inbound = await conn.fetchrow(
                """
                SELECT csi.channel_message_id::text AS channel_message_id,
                       csi.source_document_id::text AS source_document_id,
                       csi.ingestion_job_id::text AS ingestion_job_id,
                       csi.content_hash,
                       csi.sensitivity,
                       csi.raw_metadata,
                       d.content,
                       d.path,
                       d.source_attribution,
                       d.metadata AS document_metadata,
                       j.status AS job_status,
                       j.content AS job_content,
                       j.payload AS job_payload
                FROM channel_source_items csi
                JOIN source_documents d ON d.id = csi.source_document_id
                LEFT JOIN ingestion_jobs j ON j.id = csi.ingestion_job_id
                WHERE csi.session_id = $1::uuid
                  AND csi.direction = 'inbound'
                """,
                prepared["session_id"],
            )
            opened = _j(await conn.fetchval(
                "SELECT open_source_document($1::uuid)",
                inbound["source_document_id"],
            ))
            replay = _j(await conn.fetchval(
                "SELECT upsert_channel_source_item($1::uuid)",
                inbound["channel_message_id"],
            ))
            item_count = await conn.fetchval(
                "SELECT COUNT(*) FROM channel_source_items WHERE channel_message_id = $1::uuid",
                inbound["channel_message_id"],
            )

            history = [
                {"role": "user", "content": content},
                {"role": "assistant", "content": "noted"},
            ]
            await conn.fetchval(
                "SELECT finalize_channel_turn($1::uuid, $2, $3, $4::jsonb)",
                prepared["session_id"],
                content,
                "noted",
                json.dumps({"history": history, "metadata": {"channel_type": "telegram"}}),
            )
            outbound = await conn.fetchrow(
                """
                SELECT csi.ingestion_job_id::text AS ingestion_job_id,
                       d.content,
                       d.source_attribution
                FROM channel_source_items csi
                JOIN source_documents d ON d.id = csi.source_document_id
                WHERE csi.session_id = $1::uuid
                  AND csi.direction = 'outbound'
                """,
                prepared["session_id"],
            )
        finally:
            await tr.rollback()

    inbound_attr = _j(inbound["source_attribution"])
    inbound_doc_meta = _j(inbound["document_metadata"])
    inbound_raw_meta = _j(inbound["raw_metadata"])
    job_payload = _j(inbound["job_payload"])
    outbound_attr = _j(outbound["source_attribution"])

    assert prepared["allowed"] is True
    assert inbound["content"] == content
    assert opened["content"] == content
    assert inbound["path"].endswith(f"/inbound/in-{marker}")
    assert inbound["content_hash"].startswith("channel:")
    assert inbound["sensitivity"] == "shared"
    assert inbound_attr["kind"] == "channel_message"
    assert inbound_attr["platform_message_id"] == f"in-{marker}"
    assert inbound_attr["sensitivity"] == "shared"
    assert inbound_raw_meta["attachments"][0]["filename"] == "note.txt"
    assert inbound_doc_meta["raw_metadata"]["thread_id"] == "topic-1"
    assert inbound["ingestion_job_id"] is not None
    assert inbound["job_status"] == "pending"
    assert inbound["job_content"] == content
    assert job_payload["source_document_id"] == inbound["source_document_id"]
    assert job_payload["source_type"] == "channel_message"
    assert job_payload["acquisition"] == "connector"
    assert replay["ingestion_job_id"] == inbound["ingestion_job_id"]
    assert int(item_count) == 1
    assert outbound["content"] == "noted"
    assert outbound["ingestion_job_id"] is None
    assert outbound_attr["direction"] == "outbound"


async def test_channel_source_artifact_enqueue_can_be_disabled_by_config(db_pool):
    marker = uuid4().hex

    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(
                "SELECT set_config('channel.source_artifacts_enqueue_inbound', 'false'::jsonb)"
            )
            prepared = _j(await conn.fetchval(
                "SELECT prepare_channel_turn($1::jsonb)",
                json.dumps({
                    "channel_type": "signal",
                    "channel_id": f"direct-{marker}",
                    "sender_id": f"user-{marker}",
                    "sender_name": "Direct Tester",
                    "content": f"Config disabled artifact {marker}",
                    "message_id": f"in-{marker}",
                    "metadata": {"is_private": True},
                }),
            ))
            row = await conn.fetchrow(
                """
                SELECT csi.ingestion_job_id::text AS ingestion_job_id,
                       csi.sensitivity,
                       d.content
                FROM channel_source_items csi
                JOIN source_documents d ON d.id = csi.source_document_id
                WHERE csi.session_id = $1::uuid
                  AND csi.direction = 'inbound'
                """,
                prepared["session_id"],
            )
        finally:
            await tr.rollback()

    assert row["content"] == f"Config disabled artifact {marker}"
    assert row["sensitivity"] == "private"
    assert row["ingestion_job_id"] is None
