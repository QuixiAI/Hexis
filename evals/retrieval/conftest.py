"""Retrieval-eval fixtures: a per-module temp DB (reusing the main test
harness), a generated corpus ingested once, chunks embedded via the real
maintenance step, and a JSON report written at session end."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# Reuse the whole DB test harness: temp_test_db (module-scoped, autouse),
# db_pool, migration replay, AGE setup.
from tests.conftest import *  # noqa: F401,F403
from tests.utils import _db_dsn

from evals.retrieval.corpus_gen import GOLD, build_corpus
from evals.retrieval.harness import ReportCollector


class _StubLLM:
    """No-op extraction LLM: evals exercise retrieval, not distillation."""

    call_count = 0

    async def complete_json(self, messages, temperature=0.2, **_kwargs):
        text = str(messages[-1].get("content", ""))
        if "key 'items'" in text:
            return {"items": []}
        return {"valence": 0.0, "arousal": 0.2, "primary_emotion": "neutral",
                "intensity": 0.1, "summary": "Corpus material."}


async def _stub_get_embedding(conn) -> None:
    """Deterministic hash-projection embeddings (the CI fake): vector search
    stays alive but only lexical matches are semantically meaningful."""
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


@pytest.fixture(scope="module")
async def corpus(db_pool, tmp_path_factory):
    """Generate + ingest the fixture corpus once; returns paths and handles."""
    from services.ingest import Config, IngestionPipeline
    from services.source_chunks import run_source_chunk_embed_step

    corpus_dir = tmp_path_factory.mktemp("retrieval-corpus")
    paths = build_corpus(corpus_dir)

    async with db_pool.acquire() as conn:
        await _stub_get_embedding(conn)

    def _pipeline(**overrides) -> IngestionPipeline:
        config = Config(
            dsn=_db_dsn(os.environ.get("POSTGRES_DB")),
            llm_config={"provider": "openai", "model": "stub", "api_key": "stub"},
            verbose=False,
            **overrides,
        )
        pipeline = IngestionPipeline(config)
        pipeline.llm = _StubLLM()
        pipeline.appraiser.llm = pipeline.llm
        pipeline.extractor.llm = pipeline.llm
        return pipeline

    public = _pipeline()
    try:
        for name in ("spec", "doc_b", "pdf", "xlsx", "mbox", "web"):
            await public.ingest_file(paths[name])
        await public.ingest_directory(paths["dupes"])
        # Corrupt DOCX: extraction fails loud; the artifact must survive.
        await public.ingest_file(paths["corrupt"])
    finally:
        await public.close()

    private = _pipeline(sensitivity="private")
    try:
        await private.ingest_file(paths["private"])
    finally:
        await private.close()

    # Drain the chunk-embedding queue exactly the way the worker does.
    async with db_pool.acquire() as conn:
        for _ in range(50):
            result = await run_source_chunk_embed_step(conn)
            if result.get("skipped"):
                break

    docs: dict[str, dict] = {}
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id::text AS id, title, path, source_attribution FROM source_documents WHERE status = 'active'"
        )
    for row in rows:
        for name, path in paths.items():
            if row["path"] == str(path):
                attribution = row["source_attribution"]
                if isinstance(attribution, str):
                    attribution = json.loads(attribution)
                docs[name] = {"id": row["id"], "title": row["title"],
                              "path": row["path"], "attribution": attribution}
    return {"paths": paths, "docs": docs, "gold": GOLD, "dir": corpus_dir}


@pytest.fixture(scope="module")
def report() -> ReportCollector:
    return ReportCollector()


@pytest.fixture(scope="module", autouse=True)
def _write_report(report, request):
    yield
    if report.records:
        target = report.write()
        print(f"\nretrieval eval report: {target}")
