"""Receipt-correct ingestion (#85 stage 4): a crash mid-document resumes —
completed sections skip, the encounter is reused, and only the doc-complete
receipt (the final act) makes a document skip entirely.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from tests.utils import _db_dsn

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.db]

# Three clearly separable sections, each > max_section_chars/2 so the
# sectioner keeps them apart with default settings.
DOC = "\n\n".join(
    f"## Part {i}\n\n" + " ".join(f"Sentence {i}-{j} about topic {i}." for j in range(40))
    for i in range(1, 4)
)


class _StubLLM:
    """Deterministic extractor: one fact per section; optionally explodes on
    its Nth completion to simulate a crash mid-document."""

    def __init__(self, fail_on_call: int | None = None):
        self.call_count = 0
        self.completions = 0
        self.fail_on_call = fail_on_call
        self._cfg = {"provider": "stub", "model": "stub"}

    async def complete(self, messages, temperature=0.3):
        raise AssertionError("complete() unused; extractor uses complete_json")

    async def complete_json(self, messages, temperature=0.2, **kwargs):
        self.call_count += 1
        self.completions += 1
        if self.fail_on_call is not None and self.completions >= self.fail_on_call:
            raise RuntimeError("stub LLM crash")
        import hashlib as _hashlib

        text = str(messages[-1].get("content", ""))
        part = next((f"Part {i}" for i in range(1, 4) if f"Part {i}" in text), "Part ?")
        # Content-derived uniqueness: identical fact text across tests would
        # route as corroboration (dedup) instead of creation.
        sig = _hashlib.sha256(text.encode()).hexdigest()[:10]
        return {
            "items": [
                {
                    "content": f"Stub fact {sig} from {part}.",
                    "confidence": 0.9,
                    "importance": 0.6,
                }
            ]
        }


async def _make_pipeline(db_pool, fail_on_call=None):
    from services.ingest import Config, IngestionMode, IngestionPipeline

    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            -- Distinct texts land on distinct AXES (near-orthogonal) so the
            -- similarity router treats them as different claims; a shared
            -- direction would dedup everything to the first fact.
            CREATE OR REPLACE FUNCTION get_embedding(text_contents TEXT[])
            RETURNS vector[] AS $$
                SELECT COALESCE(array_agg((
                    array_fill(0.01::float, ARRAY[2 + abs(hashtext(t)) % (embedding_dimension() - 2)]) ||
                    ARRAY[1.0::float] ||
                    array_fill(0.01::float, ARRAY[embedding_dimension() - 3 - abs(hashtext(t)) % (embedding_dimension() - 2)])
                )::vector), ARRAY[]::vector[])
                FROM unnest(text_contents) t
            $$ LANGUAGE sql;
            """
        )
    config = Config(
        dsn=_db_dsn(os.environ.get("POSTGRES_DB")),
        llm_config={"provider": "openai", "model": "stub", "api_key": "stub"},
        mode=IngestionMode.FAST,
        # Doc-level appraisal only (words > deep_max_words is false here, so
        # force shallow by lowering the threshold): one appraisal call, then
        # one extraction call per section — deterministic call accounting.
        deep_max_words=1,
        verbose=False,
    )
    pipeline = IngestionPipeline(config)
    pipeline.llm = _StubLLM(fail_on_call=fail_on_call)
    pipeline.appraiser.llm = pipeline.llm
    pipeline.extractor.llm = pipeline.llm
    return pipeline


async def test_crash_resumes_and_completes(db_pool):
    # Attempt 1: appraisal (call 1) + section extractions; crash on call 3
    # (mid-extraction) — after at least one section persisted.
    pipeline = await _make_pipeline(db_pool, fail_on_call=3)
    with pytest.raises(RuntimeError, match="stub LLM crash"):
        await pipeline.ingest_text(DOC, title="Resume pin doc")
    await pipeline.close()

    async with db_pool.acquire() as conn:
        after_crash = await conn.fetch(
            "SELECT section_hash FROM ingestion_receipts WHERE source_path LIKE 'text:%' OR section_hash LIKE 'enc:%'"
        )
    hashes = {r["section_hash"] for r in after_crash}
    assert any(h.startswith("enc:") for h in hashes), "encounter sentinel missing"
    doc_ref = next(h for h in hashes if h.startswith("enc:")).removeprefix("enc:")
    assert doc_ref not in hashes, "doc-complete must NOT exist after a crash"

    # Attempt 2: full run — resumes (reuses encounter, skips receipted
    # sections) and finishes with the doc-complete receipt.
    pipeline2 = await _make_pipeline(db_pool)
    count = await pipeline2.ingest_text(DOC, title="Resume pin doc")
    await pipeline2.close()
    assert count >= 1

    async with db_pool.acquire() as conn:
        complete = await conn.fetchval(
            "SELECT COUNT(*) FROM ingestion_receipts WHERE doc_ref = $1 AND section_hash = $1",
            doc_ref,
        )
        encounters = await conn.fetchval(
            "SELECT COUNT(*) FROM ingestion_receipts WHERE doc_ref = $1 AND section_hash = 'enc:' || $1",
            doc_ref,
        )
    assert complete == 1, "doc-complete receipt missing after successful resume"
    assert encounters == 1, "encounter must be reused, not recreated"

    # Attempt 3: doc-complete short-circuits — zero LLM calls.
    pipeline3 = await _make_pipeline(db_pool)
    count3 = await pipeline3.ingest_text(DOC, title="Resume pin doc")
    calls3 = pipeline3.llm.call_count
    await pipeline3.close()
    assert count3 == 0
    assert calls3 == 0, "a completed document must not reach the LLM again"


async def test_worker_step_processes_enqueued_job(db_pool):
    """#87 end to end: enqueue a text job -> one worker step (stub LLM) ->
    completed with memories + receipts; a failing job backs off."""
    import json as _json

    from services.ingest.jobs import run_ingestion_jobs_step

    # Success path
    pipeline_cfg_source = await _make_pipeline(db_pool)
    stub_config = pipeline_cfg_source.config
    await pipeline_cfg_source.close()
    stub_config.llm_config = {"provider": "openai", "model": "stub", "api_key": "stub"}

    async with db_pool.acquire() as conn:
        job_id = await conn.fetchval(
            "SELECT enqueue_ingestion_job('text', $1::jsonb, $2, $3)",
            _json.dumps({"title": "Worker job pin", "mode": "fast"}),
            "# Worker job doc\n\n" + DOC,
            "worker-job-hash-1",
        )

    # Patch IngestLLM inside the pipeline the job builds: config_override
    # carries our stub llm wiring via a pre-built Config whose pipeline gets
    # stubbed after construction — simplest: monkeypatch IngestionPipeline.
    from services.ingest import pipeline as pipeline_mod

    real_init = pipeline_mod.IngestionPipeline.__init__

    def _stubbed_init(self, config):
        real_init(self, config)
        self.llm = _StubLLM()
        self.appraiser.llm = self.llm
        self.extractor.llm = self.llm

    pipeline_mod.IngestionPipeline.__init__ = _stubbed_init
    try:
        processed = await run_ingestion_jobs_step(db_pool, config_override=stub_config)
    finally:
        pipeline_mod.IngestionPipeline.__init__ = real_init

    assert processed == 1
    async with db_pool.acquire() as conn:
        job = _json.loads(await conn.fetchval("SELECT get_ingestion_job($1::uuid)", job_id))
    assert job["status"] == "completed"
    assert job["result"]["memories_created"] >= 1

    # Failure path: crash LLM -> job backs off to pending with error recorded.
    async with db_pool.acquire() as conn:
        fail_id = await conn.fetchval(
            "SELECT enqueue_ingestion_job('text', $1::jsonb, $2, $3)",
            _json.dumps({"title": "Worker fail pin", "mode": "fast"}),
            "# Failing job doc\n\n" + DOC.replace("Sentence", "Line"),
            "worker-job-hash-2",
        )

    def _failing_init(self, config):
        real_init(self, config)
        self.llm = _StubLLM(fail_on_call=1)
        self.appraiser.llm = self.llm
        self.extractor.llm = self.llm

    pipeline_mod.IngestionPipeline.__init__ = _failing_init
    try:
        await run_ingestion_jobs_step(db_pool, config_override=stub_config)
    finally:
        pipeline_mod.IngestionPipeline.__init__ = real_init

    async with db_pool.acquire() as conn:
        failed = _json.loads(await conn.fetchval("SELECT get_ingestion_job($1::uuid)", fail_id))
    assert failed["status"] == "pending"  # backed off, not terminal
    assert "stub LLM crash" in failed["error"]
    assert failed["attempts"] == 1


class _PartFailLLM(_StubLLM):
    """Extraction of one named section fails every time; the others succeed."""

    def __init__(self, failing_part: str | None):
        super().__init__()
        self.failing_part = failing_part

    async def complete_json(self, messages, temperature=0.2, **kwargs):
        text = str(messages[-1].get("content", ""))
        if self.failing_part and self.failing_part in text and "SECTION:" in text:
            self.call_count += 1
            raise RuntimeError("model returned garbage for this section")
        return await super().complete_json(messages, temperature, **kwargs)


async def test_one_bad_section_keeps_the_others_and_retries_only_itself(db_pool):
    from services.ingest.pipeline import IngestSectionError

    partial_doc = DOC.replace("topic", "subject")  # distinct from the doc the crash test completed
    pipeline = await _make_pipeline(db_pool)
    pipeline.llm = _PartFailLLM(failing_part="Part 2")
    pipeline.appraiser.llm = pipeline.llm
    pipeline.extractor.llm = pipeline.llm
    with pytest.raises(IngestSectionError, match=r"1 of 3 sections failed \(2 memories kept\)"):
        await pipeline.ingest_text(partial_doc, title="Partial doc")
    await pipeline.close()

    async with db_pool.acquire() as conn:
        receipts = await conn.fetch(
            "SELECT doc_ref, section_hash, memories_created FROM ingestion_receipts WHERE doc_ref IN "
            "(SELECT doc_ref FROM ingestion_receipts WHERE section_hash LIKE 'enc:%' ORDER BY created_at DESC LIMIT 1)"
        )
    doc_ref = receipts[0]["doc_ref"]
    section_receipts = [r for r in receipts if not r["section_hash"].startswith("enc:") and r["section_hash"] != doc_ref]
    assert len(section_receipts) == 2, "the two good sections must be receipted"
    assert not any(r["section_hash"] == doc_ref for r in receipts), "no doc-complete receipt yet"

    # Second attempt: the failing section is read; nothing else is re-asked.
    pipeline2 = await _make_pipeline(db_pool)
    count = await pipeline2.ingest_text(partial_doc, title="Partial doc")
    await pipeline2.close()
    assert count >= 1
    assert pipeline2.llm.call_count == 2, "one appraisal + the one missing section"
    async with db_pool.acquire() as conn:
        complete = await conn.fetchval(
            "SELECT COUNT(*) FROM ingestion_receipts WHERE doc_ref = $1 AND section_hash = $1", doc_ref
        )
    assert complete == 1


async def test_worker_step_refills_slots_until_the_queue_is_empty(db_pool):
    """One step reads every due document, refilling each slot as it frees,
    instead of one lock-step batch per tick."""
    import json as _json

    from services.ingest import pipeline as pipeline_mod
    from services.ingest.jobs import run_ingestion_jobs_step

    pipeline_cfg_source = await _make_pipeline(db_pool)
    stub_config = pipeline_cfg_source.config
    await pipeline_cfg_source.close()
    stub_config.llm_config = {"provider": "openai", "model": "stub", "api_key": "stub"}

    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE config SET value = '1'::jsonb WHERE key = 'ingest.job_batch_size'")
        for i in range(3):
            await conn.fetchval(
                "SELECT enqueue_ingestion_job('text', $1::jsonb, $2, $3)",
                _json.dumps({"title": f"Refill doc {i}", "mode": "fast"}),
                f"# Refill doc {i}\n\n" + DOC.replace("topic", f"refill{i}"),
                f"refill-hash-{i}",
            )

    real_init = pipeline_mod.IngestionPipeline.__init__

    def _stubbed_init(self, config):
        real_init(self, config)
        self.llm = _StubLLM()
        self.appraiser.llm = self.llm
        self.extractor.llm = self.llm

    pipeline_mod.IngestionPipeline.__init__ = _stubbed_init
    try:
        processed = await run_ingestion_jobs_step(db_pool, config_override=stub_config)
    finally:
        pipeline_mod.IngestionPipeline.__init__ = real_init

    assert processed == 3
    async with db_pool.acquire() as conn:
        statuses = await conn.fetch(
            "SELECT status FROM ingestion_jobs WHERE content_hash LIKE 'refill-hash-%'"
        )
    assert [r["status"] for r in statuses] == ["completed"] * 3


async def _run_ingest_worker_until(db_pool, monkeypatch, done_sql: str, *, recheck_s: float) -> str:
    """Run the ingest worker with stubbed reading until done_sql returns a value."""
    from services import ingest_worker
    from services.ingest import pipeline as pipeline_mod

    monkeypatch.setattr(ingest_worker, "_RECHECK_SECONDS", recheck_s)
    monkeypatch.setattr(ingest_worker.IngestWorker, "_agent_ready", lambda self: asyncio.sleep(0, True))
    real_init = pipeline_mod.IngestionPipeline.__init__

    def _stubbed_init(self, config):
        real_init(self, config)
        self.llm = _StubLLM()
        self.appraiser.llm = self.llm
        self.extractor.llm = self.llm

    monkeypatch.setattr(pipeline_mod.IngestionPipeline, "__init__", _stubbed_init)
    worker = ingest_worker.IngestWorker()
    task = asyncio.ensure_future(worker.run())
    try:
        for _ in range(200):
            async with db_pool.acquire() as conn:
                value = await conn.fetchval(done_sql)
            if value:
                return value
            await asyncio.sleep(0.05)
        raise AssertionError("ingest worker did not finish in time")
    finally:
        worker.stop()
        await asyncio.wait_for(task, 10)


async def test_a_queued_document_wakes_the_ingest_worker(db_pool, monkeypatch):
    """Reading starts when a job is queued (NOTIFY), not on the next timer tick."""
    import json as _json

    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE config SET value = '1'::jsonb WHERE key = 'ingest.job_batch_size'")

    async def enqueue_soon():
        await asyncio.sleep(0.5)  # after the worker has drained an empty queue and gone to sleep
        async with db_pool.acquire() as conn:
            await conn.fetchval(
                "SELECT enqueue_ingestion_job('text', $1::jsonb, $2, $3)",
                _json.dumps({"title": "Woken doc", "mode": "fast"}),
                "# Woken doc\n\n" + DOC.replace("topic", "woken"),
                "woken-hash-1",
            )

    asyncio.ensure_future(enqueue_soon())
    status = await _run_ingest_worker_until(
        db_pool, monkeypatch,
        "SELECT status FROM ingestion_jobs WHERE content_hash = 'woken-hash-1' AND status = 'completed'",
        recheck_s=3600,  # only the notification can wake it in time
    )
    assert status == "completed"


async def test_the_ingest_worker_reclaims_a_job_stranded_by_a_stopped_worker(db_pool, monkeypatch):
    """A job left in_progress past the claim timeout is read again."""
    import json as _json

    async with db_pool.acquire() as conn:
        job_id = await conn.fetchval(
            "SELECT enqueue_ingestion_job('text', $1::jsonb, $2, $3)",
            _json.dumps({"title": "Stranded doc", "mode": "fast"}),
            "# Stranded doc\n\n" + DOC.replace("topic", "stranded"),
            "stranded-hash-1",
        )
        await conn.execute(
            """
            UPDATE ingestion_jobs
            SET status = 'in_progress', attempts = 1,
                claimed_at = CURRENT_TIMESTAMP - make_interval(
                    secs => COALESCE(get_config_int('ingest.job_claim_timeout_s'), 1800) + 60)
            WHERE id = $1::uuid
            """,
            job_id,
        )

    status = await _run_ingest_worker_until(
        db_pool, monkeypatch,
        "SELECT status FROM ingestion_jobs WHERE content_hash = 'stranded-hash-1' AND status = 'completed'",
        recheck_s=0.2,
    )
    assert status == "completed"
