"""Ingestion jobs keep retrying through service outages and fail only on bad input."""

from __future__ import annotations

import json
import os

import pytest

from services.ingest import Config
from services.ingest.jobs import _is_transient_failure, run_ingestion_jobs_step
from services.ingest.llm import IngestLLMOutputError
from services.ingest.pipeline import IngestSectionError
from tests.utils import _db_dsn, get_test_identifier

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


def test_outages_are_transient_and_bad_input_is_not():
    assert _is_transient_failure(RuntimeError(
        "Failed to get embeddings: Embedding service not available after 30 seconds: Operation timed out"))
    assert _is_transient_failure(ConnectionError("reset by peer"))
    wrapped = RuntimeError("extraction failed")
    wrapped.__cause__ = TimeoutError()
    assert _is_transient_failure(wrapped)
    assert _is_transient_failure(IngestSectionError("2 of 5 sections failed", causes=[TimeoutError(), TimeoutError()]))

    assert _is_transient_failure(RuntimeError("Error code: 429 - rate limit exceeded"))

    class ServiceUnavailable(Exception):
        status_code = 503

    assert _is_transient_failure(ServiceUnavailable("upstream down"))

    # A document that fails these ways retries without limit, so bad input must not look like an outage.
    assert not _is_transient_failure(FileNotFoundError("/tmp/upload.pdf"))
    assert not _is_transient_failure(PermissionError("artifact file not readable"))
    assert not _is_transient_failure(RuntimeError("Could not generate a summary: separate sections disagree"))
    assert not _is_transient_failure(RuntimeError("database connection pool is closed"))
    assert not _is_transient_failure(IngestLLMOutputError("extraction: model output not a JSON object"))
    assert not _is_transient_failure(RuntimeError("Unsupported file type: .xyz"))
    assert not _is_transient_failure(IngestSectionError("2 of 5 sections failed",
                                                        causes=[TimeoutError(), IngestLLMOutputError("bad")]))


@pytest.mark.parametrize(("error", "status"), [
    ("Embedding service not available after 30 seconds: timed out", "pending"),
    ("Unsupported file type: .xyz", "failed"),
])
async def test_last_attempt_fails_only_for_bad_input(db_pool, monkeypatch, error, status):
    marker = get_test_identifier("transient")
    async with db_pool.acquire() as conn:
        job_id = await conn.fetchval(
            "SELECT enqueue_ingestion_job('text', $1::jsonb, $2, $3, 1)",
            json.dumps({"title": f"Job {marker}", "mode": "fast"}), f"Body {marker}", f"hash:{marker}",
        )

    async def failing_ingest_text(self, content, **kwargs):
        raise RuntimeError(error)

    from services.ingest.pipeline import IngestionPipeline

    monkeypatch.setattr(IngestionPipeline, "ingest_text", failing_ingest_text)
    config = Config(
        dsn=_db_dsn(os.environ.get("POSTGRES_DB")),
        llm_config={"provider": "openai", "model": "stub", "api_key": "stub"},
        verbose=False,
    )
    try:
        await run_ingestion_jobs_step(db_pool, config_override=config)
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT status, error FROM ingestion_jobs WHERE id = $1::uuid", job_id)
        assert row["status"] == status
        assert row["error"] == error
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM ingestion_jobs WHERE id = $1::uuid", job_id)
