"""Migration 0259: a connector item's document and the copy reading made of it
become one row that keeps the copy's chunks and memories."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.evals.test_user_model_trajectories import _connect_slack, _j, _source_item
from tests.utils import get_test_identifier

pytestmark = [pytest.mark.asyncio(loop_scope="session")]

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "db" / "migrations" / "0259_google_drive_outreach_destination_and_connector_fixes.sql"
).read_text()


async def test_duplicate_connector_document_is_merged_into_the_read_copy(db_pool):
    marker = get_test_identifier("dupdoc")
    account_key = f"T{marker}:U1"
    content = f"[10:00] Alex: merge test {marker}"
    async with db_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await _connect_slack(conn, marker, account_key)
            item = await _source_item(conn, account_key=account_key, provider_item_id=f"C1:{marker}",
                                      content=content, offset_seconds=0)
            stored_id = item["document_id"]
            stored_hash = await conn.fetchval("SELECT content_hash FROM source_documents WHERE id = $1::uuid", stored_id)
            copy_hash = f"{marker.replace('-', '')[:64]:0<64}"
            copy = _j(await conn.fetchval(
                "SELECT upsert_source_document($1, 'connector_message', $2, $3, '.md', $4, 5,"
                " $5::jsonb, '{}'::jsonb)",
                f"Slack message {marker}", copy_hash, f"text:{copy_hash[:12]}", content,
                json.dumps({"kind": "connector_message", "content_hash": copy_hash}),
            ))
            copy_id = copy["document_id"]
            await conn.execute(
                "INSERT INTO source_document_chunks (source_document_id, chunk_index, content, content_hash, char_start, char_end)"
                " VALUES ($1::uuid, 0, $2, md5($2), 0, $3)", copy_id, content, len(content),
            )
            await conn.execute(
                "INSERT INTO ingestion_receipts (doc_ref, section_hash, memories_created) VALUES ($1, $1, 1), ($1, 'enc:' || $1, 0)",
                copy_hash,
            )
            memory_id = await conn.fetchval(
                "INSERT INTO memories (type, content, embedding, source_attribution, metadata)"
                " VALUES ('semantic', $1, array_fill(0.01, ARRAY[embedding_dimension()])::vector, $2::jsonb, $3::jsonb)"
                " RETURNING id",
                f"fact {marker}",
                json.dumps({"content_hash": copy_hash, "source_document_id": copy_id}),
                json.dumps({"source_references": [{"content_hash": copy_hash, "source_document_id": copy_id}]}),
            )

            await conn.execute(_MIGRATION)
            await conn.execute(_MIGRATION)  # idempotent

            docs = await conn.fetch("SELECT id::text, content_hash FROM source_documents WHERE content = $1", content)
            assert [(d["id"], d["content_hash"]) for d in docs] == [(copy_id, stored_hash)]
            linked = await conn.fetchval(
                "SELECT source_document_id::text FROM connector_source_items WHERE id = $1::uuid", item["source_item_id"],
            )
            assert linked == copy_id
            receipts = {tuple(r) for r in await conn.fetch(
                "SELECT doc_ref, section_hash FROM ingestion_receipts WHERE doc_ref IN ($1, $2)", copy_hash, stored_hash,
            )}
            assert receipts == {(stored_hash, stored_hash), (stored_hash, f"enc:{stored_hash}")}
            memory = await conn.fetchrow("SELECT source_attribution, metadata FROM memories WHERE id = $1", memory_id)
            assert _j(memory["source_attribution"])["content_hash"] == stored_hash
            assert _j(memory["metadata"])["source_references"][0]["content_hash"] == stored_hash
        finally:
            await tx.rollback()
