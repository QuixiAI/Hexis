"""Pipeline-level chunk substrate: ingestion writes durable chunks whose
content is an exact substring of the stored document, memories carry
chunk-grain provenance, and re-ingestion keeps chunk ids stable."""

from __future__ import annotations

import json
import os

import pytest

from services.ingest import Config, IngestionMode, IngestionPipeline, _hash_text
from tests.utils import _db_dsn, get_test_identifier

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


class _StubLLM:
    def __init__(self, marker: str):
        self.marker = marker
        self.call_count = 0

    async def complete_json(self, messages, temperature=0.2, **kwargs):
        self.call_count += 1
        text = str(messages[-1].get("content", ""))
        if "key 'items'" in text:
            return {
                "items": [
                    {
                        "content": f"Chunk provenance survived for {self.marker}.",
                        "confidence": 0.9,
                        "importance": 0.6,
                        "category": "fact",
                    }
                ]
            }
        return {
            "valence": 0.0,
            "arousal": 0.2,
            "primary_emotion": "neutral",
            "intensity": 0.1,
            "summary": "Technical reference material.",
        }


async def _stub_get_embedding(conn):
    await conn.execute(
        """
        CREATE OR REPLACE FUNCTION get_embedding(text_contents TEXT[])
        RETURNS vector[] AS $$
            SELECT COALESCE(
                array_agg((
                    array_fill(0.01::float, ARRAY[2 + abs(hashtext(t)) % (embedding_dimension() - 2)]) ||
                    ARRAY[1.0::float] ||
                    array_fill(0.01::float, ARRAY[embedding_dimension() - 3 - abs(hashtext(t)) % (embedding_dimension() - 2)])
                )::vector),
                ARRAY[]::vector[]
            )
            FROM unnest(text_contents) t
        $$ LANGUAGE sql;
        """
    )


def _build_pipeline(marker: str) -> IngestionPipeline:
    config = Config(
        dsn=_db_dsn(os.environ.get("POSTGRES_DB")),
        llm_config={"provider": "openai", "model": "stub", "api_key": "stub"},
        mode=IngestionMode.FAST,
        verbose=False,
    )
    pipeline = IngestionPipeline(config)
    pipeline.llm = _StubLLM(marker)
    pipeline.appraiser.llm = pipeline.llm
    pipeline.extractor.llm = pipeline.llm
    return pipeline


async def test_ingest_writes_durable_chunks_with_exact_offsets(db_pool):
    marker = get_test_identifier("chunkingest")
    content = (
        f"# Chunk Ingest {marker}\n\n"
        f"The first passage explains the retention window for {marker}.\n\n"
        f"## Details\n\n"
        f"The second passage documents the escalation threshold for {marker}.\n"
    )
    content_hash = _hash_text(content)
    fact = f"Chunk provenance survived for {marker}."

    async with db_pool.acquire() as conn:
        await _stub_get_embedding(conn)

    pipeline = _build_pipeline(marker)
    try:
        count = await pipeline.ingest_text(content, title=f"Chunk Ingest {marker}")
    finally:
        await pipeline.close()

    try:
        assert count >= 1
        async with db_pool.acquire() as conn:
            doc = await conn.fetchrow(
                "SELECT id, content FROM source_documents WHERE content_hash = $1",
                content_hash,
            )
            assert doc is not None
            chunks = await conn.fetch(
                """
                SELECT id::text AS id, chunk_index, content, char_start, char_end,
                       locator_kind, heading_path, embedding_status, chunker_version
                FROM source_document_chunks
                WHERE source_document_id = $1
                ORDER BY chunk_index
                """,
                doc["id"],
            )
            assert len(chunks) >= 2
            for chunk in chunks:
                assert chunk["content"] == doc["content"][chunk["char_start"]:chunk["char_end"]]
                assert chunk["chunker_version"] == "v2"
                assert chunk["embedding_status"] == "pending"
            assert chunks[0]["locator_kind"] == "section"
            details = [c for c in chunks if "Details" in (c["heading_path"] or [])]
            assert details, "the ## Details section carries its heading path"

            # Memories carry chunk-grain provenance through normalization.
            mem = await conn.fetchrow(
                "SELECT source_attribution FROM memories WHERE content = $1", fact
            )
            assert mem is not None
            source = mem["source_attribution"]
            if isinstance(source, str):
                source = json.loads(source)
            assert source.get("chunk_id") in {c["id"] for c in chunks}
            assert isinstance(source.get("chunk_index"), int)

        # Re-ingest: doc receipt short-circuits, chunk ids stay stable.
        pipeline2 = _build_pipeline(marker)
        try:
            count2 = await pipeline2.ingest_text(content, title=f"Chunk Ingest {marker}")
        finally:
            await pipeline2.close()
        assert count2 == 0
        async with db_pool.acquire() as conn:
            after = await conn.fetch(
                """
                SELECT id::text AS id FROM source_document_chunks
                WHERE source_document_id = (SELECT id FROM source_documents WHERE content_hash = $1)
                ORDER BY chunk_index
                """,
                content_hash,
            )
        assert [c["id"] for c in after] == [c["id"] for c in chunks]
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM subconscious_units WHERE source_attribution->>'content_hash' = $1",
                content_hash,
            )
            await conn.execute("DELETE FROM memories WHERE content = $1", fact)
            await conn.execute("DELETE FROM source_documents WHERE content_hash = $1", content_hash)
            await conn.execute("DELETE FROM ingestion_receipts WHERE doc_ref = $1", content_hash)
