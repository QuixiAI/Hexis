"""Slow and hybrid reading fail, resume and cancel like fast reading does.

A model outage fails the job (and it retries) instead of completing it with
no memories; a retry reads only the chunks not yet receipted; hybrid keeps a
chunk's fast facts when its slow read yields nothing; cancel is honored
between chunks; a job that cannot be set up, or keeps stopping, does not stay
in_progress; and slow-read model calls go through the pipeline's hook.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os

import pytest

from tests.utils import _db_dsn

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.db]

# Three sections the sectioner keeps apart (as in test_ingest_resume).
DOC = "\n\n".join(
    f"## Part {i}\n\n" + " ".join(f"Sentence {i}-{j} about slowtopic {i}." for j in range(40))
    for i in range(1, 4)
)

_LLM_CFG = {"provider": "openai", "model": "stub", "api_key": "stub"}


def _doc(tag: str) -> str:
    return DOC.replace("slowtopic", f"slowtopic{tag}")


def _part(text: str) -> str:
    return next((f"Part {i}" for i in range(1, 4) if f"Part {i}" in text), "Part ?")


class _StubLLM:
    """Fast-pass extractor stub: one fact per section, importance chosen per
    part; optionally fails on the Nth completion."""

    def __init__(self, importance: float = 0.9, fail_on_call: int | None = None):
        self.call_count = 0
        self.importance = importance
        self.fail_on_call = fail_on_call
        self.before_call = None
        self._cfg = dict(_LLM_CFG)

    async def complete_json(self, messages, temperature=0.2, **kwargs):
        self.call_count += 1
        if self.fail_on_call is not None and self.call_count >= self.fail_on_call:
            raise RuntimeError("stub fast pass crash")
        text = str(messages[-1].get("content", ""))
        sig = hashlib.sha256(text.encode()).hexdigest()[:10]
        return {
            "items": [
                {
                    "content": f"Fast fact {sig} from {_part(text)}.",
                    "confidence": 0.9,
                    "importance": self.importance,
                }
            ]
        }


async def _use_axis_embeddings(db_pool) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
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


def _config(mode):
    from services.ingest import Config

    return Config(
        dsn=_db_dsn(os.environ.get("POSTGRES_DB")),
        llm_config=dict(_LLM_CFG),
        mode=mode,
        verbose=False,
    )


async def _make_pipeline(db_pool, mode, llm: _StubLLM | None = None):
    from services.ingest import IngestionPipeline

    await _use_axis_embeddings(db_pool)
    pipeline = IngestionPipeline(_config(mode))
    pipeline.llm = llm or _StubLLM()
    pipeline.appraiser.llm = pipeline.llm
    pipeline.extractor.llm = pipeline.llm
    return pipeline


def _assessment(facts: list[str]) -> dict:
    return {
        "acceptance": "accept",
        "analysis": "read slowly",
        "emotional_reaction": {"valence": 0.1, "arousal": 0.2, "primary_emotion": "calm"},
        "worldview_impact": "neutral",
        "importance": 0.6,
        "trust_assessment": 0.7,
        "extracted_facts": facts,
        "connections": [],
        "rejection_reasons": [],
    }


class _FakeSlowRead:
    """Stands in for run_slow_ingest_chunk: answers per part, records calls."""

    def __init__(self, answers: dict[str, object]):
        self.answers = answers
        self.parts: list[str] = []

    async def __call__(self, *, chunk_text, **_kwargs):
        part = _part(chunk_text)
        self.parts.append(part)
        answer = self.answers.get(part, [])
        if isinstance(answer, BaseException):
            raise answer
        return {"assessment": _assessment(list(answer)), "metrics": {}}


async def _receipts(db_pool, doc_ref: str) -> set[str]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT section_hash FROM ingestion_receipts WHERE doc_ref = $1", doc_ref)
    return {r["section_hash"] for r in rows}


async def _doc_ref(db_pool, title: str) -> str:
    async with db_pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT content_hash FROM source_documents WHERE title = $1 ORDER BY created_at DESC LIMIT 1",
            title,
        )


def _patch_pipeline_init(monkeypatch, make_llm):
    from services.ingest import pipeline as pipeline_mod

    real_init = pipeline_mod.IngestionPipeline.__init__

    def _stubbed_init(self, config):
        real_init(self, config)
        self.llm = make_llm()
        self.appraiser.llm = self.llm
        self.extractor.llm = self.llm

    monkeypatch.setattr(pipeline_mod.IngestionPipeline, "__init__", _stubbed_init)


async def _job(db_pool, job_id) -> dict:
    async with db_pool.acquire() as conn:
        return json.loads(await conn.fetchval("SELECT get_ingestion_job($1::uuid)", job_id))


async def _enqueue(db_pool, title: str, content: str, mode: str, content_hash: str):
    async with db_pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT enqueue_ingestion_job('text', $1::jsonb, $2, $3)",
            json.dumps({"title": title, "mode": mode}),
            content,
            content_hash,
        )


# --- 1. A model outage fails the job instead of completing it empty -------


async def test_strict_rlm_loop_raises_on_model_failure_and_empty_reply(monkeypatch):
    import services.hexis_rlm as rlm
    from services.rlm_repl import HexisLocalREPL

    loop = asyncio.get_running_loop()

    async def _down(messages, llm_config, max_tokens=4096, **_kwargs):
        raise RuntimeError("provider down")

    async def _empty(messages, llm_config, max_tokens=4096, **_kwargs):
        return ""

    for fake, match in ((_down, "provider down"), (_empty, "returned nothing")):
        monkeypatch.setattr(rlm, "_llm_completion", fake)
        repl = HexisLocalREPL()
        repl.setup(context_payload="chunk")
        try:
            with pytest.raises(rlm.RLMCallError, match=match):
                await loop.run_in_executor(
                    None,
                    lambda: rlm._run_loop(repl, _LLM_CFG, loop, "system", 3, strict=True),
                )
            if fake is _empty:
                # Chat and heartbeat keep their stand-in answer (no raise).
                result = await loop.run_in_executor(
                    None, lambda: rlm._run_loop(repl, _LLM_CFG, loop, "system", 3)
                )
                assert "final_answer" in result
            else:
                # A total model failure still surfaces as RlmLoopFailure (#111)
                # so the heartbeat's legacy fallback runs instead of a
                # fabricated decision.
                with pytest.raises(rlm.RlmLoopFailure, match=match):
                    await loop.run_in_executor(
                        None, lambda: rlm._run_loop(repl, _LLM_CFG, loop, "system", 3)
                    )
        finally:
            repl.cleanup()


async def test_slow_job_during_model_outage_fails_and_retries(db_pool, monkeypatch):
    import services.hexis_rlm as rlm
    from services.ingest import IngestionMode
    from services.ingest.jobs import run_ingestion_jobs_step

    async def _down(**_kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(rlm, "chat_completion", _down)
    _patch_pipeline_init(monkeypatch, _StubLLM)

    title = "Slow outage doc"
    job_id = await _enqueue(db_pool, title, _doc("outage"), "slow", "slow-outage-hash")
    await run_ingestion_jobs_step(db_pool, config_override=_config(IngestionMode.SLOW))

    job = await _job(db_pool, job_id)
    assert job["status"] == "pending", "an outage must back off and retry, not complete"
    assert "provider down" in job["error"]
    doc_ref = await _doc_ref(db_pool, title)
    receipts = await _receipts(db_pool, doc_ref)
    assert doc_ref not in receipts, "no doc-complete receipt for an unread document"
    assert receipts == {f"enc:{doc_ref}"}, "no chunk may be receipted as read"


async def test_slow_answer_that_is_not_an_assessment_raises(monkeypatch):
    import core.usage
    import services.hexis_rlm as rlm
    from services.slow_ingest_rlm import SlowAnswerUnusable, run_slow_ingest_chunk

    async def _prose(**_kwargs):
        return {"content": "FINAL(I could not decide.)", "raw": None}

    async def _no_usage(**_kwargs):
        return None

    monkeypatch.setattr(rlm, "chat_completion", _prose)
    monkeypatch.setattr(core.usage, "record_llm_usage", _no_usage)
    with pytest.raises(SlowAnswerUnusable):
        await run_slow_ingest_chunk(
            chunk_text="Some chunk text.", chunk_index=0, total_chunks=1, source_info={},
            worldview_stubs=[], emotional_state={}, goal_stubs=[], llm_config=_LLM_CFG,
            dsn=_db_dsn(os.environ.get("POSTGRES_DB")),
        )


# --- 2. Hybrid keeps fast facts and does not swallow fast-pass failures ----


async def test_hybrid_keeps_fast_facts_when_the_slow_read_yields_nothing(db_pool, monkeypatch):
    from services.ingest import IngestionMode
    from services.slow_ingest_rlm import SlowAnswerUnusable

    fake = _FakeSlowRead({
        "Part 1": [],  # slow read kept nothing
        "Part 2": SlowAnswerUnusable("not an assessment"),
        "Part 3": ["Slow fact about part three, kept by the slow read."],
    })
    monkeypatch.setattr("services.slow_ingest_rlm.run_slow_ingest_chunk", fake)

    title = "Hybrid fallback doc"
    pipeline = await _make_pipeline(db_pool, IngestionMode.HYBRID, _StubLLM(importance=0.9))
    count = await pipeline.ingest_text(_doc("fallback"), title=title)
    await pipeline.close()

    assert sorted(fake.parts) == ["Part 1", "Part 2", "Part 3"], "all three are high-signal"
    doc_ref = await _doc_ref(db_pool, title)
    async with db_pool.acquire() as conn:
        contents = [
            r["content"] for r in await conn.fetch(
                "SELECT content FROM memories WHERE type = 'semantic' AND source_attribution->>'ref' = $1",
                doc_ref,
            )
        ]
    assert any("Fast fact" in c and "Part 1" in c for c in contents), contents
    assert any("Fast fact" in c and "Part 2" in c for c in contents), contents
    assert any(c.startswith("Slow fact about part three") for c in contents), contents
    assert not any("Fast fact" in c and "Part 3" in c for c in contents), "slow facts replace fast ones"
    assert count == 3
    assert doc_ref in await _receipts(db_pool, doc_ref)


async def test_hybrid_fast_pass_failure_fails_the_document(db_pool, monkeypatch):
    from services.ingest import IngestionMode

    fake = _FakeSlowRead({})
    monkeypatch.setattr("services.slow_ingest_rlm.run_slow_ingest_chunk", fake)

    title = "Hybrid fast crash doc"
    pipeline = await _make_pipeline(db_pool, IngestionMode.HYBRID, _StubLLM(fail_on_call=2))
    with pytest.raises(RuntimeError, match="stub fast pass crash"):
        await pipeline.ingest_text(_doc("fastcrash"), title=title)
    await pipeline.close()

    doc_ref = await _doc_ref(db_pool, title)
    assert doc_ref not in await _receipts(db_pool, doc_ref)
    assert fake.parts == [], "nothing is read slowly from a half-scored document"


# --- 4. Retries read only the chunks not yet receipted ----------------------


async def test_slow_retry_reads_only_unreceipted_chunks(db_pool, monkeypatch):
    from services.ingest import IngestionMode
    from services.slow_ingest_rlm import SlowReadFailed

    title = "Slow resume doc"
    first = _FakeSlowRead({
        "Part 1": [],  # read, nothing worth keeping: still receipted
        "Part 2": ["Slow fact about part two, kept on the first try."],
        "Part 3": SlowReadFailed("timed out"),
    })
    monkeypatch.setattr("services.slow_ingest_rlm.run_slow_ingest_chunk", first)
    pipeline = await _make_pipeline(db_pool, IngestionMode.SLOW)
    with pytest.raises(SlowReadFailed):
        await pipeline.ingest_text(_doc("resume"), title=title)
    await pipeline.close()

    doc_ref = await _doc_ref(db_pool, title)
    receipts = await _receipts(db_pool, doc_ref)
    assert doc_ref not in receipts
    assert len(receipts - {f"enc:{doc_ref}"}) == 2, "parts 1 and 2 are receipted"

    second = _FakeSlowRead({"Part 3": ["Slow fact about part three, kept on the retry."]})
    monkeypatch.setattr("services.slow_ingest_rlm.run_slow_ingest_chunk", second)
    pipeline2 = await _make_pipeline(db_pool, IngestionMode.SLOW)
    await pipeline2.ingest_text(_doc("resume"), title=title)
    await pipeline2.close()

    assert second.parts == ["Part 3"]
    assert doc_ref in await _receipts(db_pool, doc_ref)


async def test_hybrid_retry_reads_only_unreceipted_chunks(db_pool, monkeypatch):
    from services.hexis_rlm import RLMCallError
    from services.ingest import IngestionMode

    title = "Hybrid resume doc"
    first = _FakeSlowRead({
        "Part 1": ["Slow fact about part one, kept on the first try."],
        "Part 2": RLMCallError("provider down"),
    })
    monkeypatch.setattr("services.slow_ingest_rlm.run_slow_ingest_chunk", first)
    pipeline = await _make_pipeline(db_pool, IngestionMode.HYBRID)
    with pytest.raises(RLMCallError):
        await pipeline.ingest_text(_doc("hybridresume"), title=title)
    await pipeline.close()

    second = _FakeSlowRead({
        "Part 2": ["Slow fact about part two, kept on the retry."],
        "Part 3": ["Slow fact about part three, kept on the retry."],
    })
    monkeypatch.setattr("services.slow_ingest_rlm.run_slow_ingest_chunk", second)
    pipeline2 = await _make_pipeline(db_pool, IngestionMode.HYBRID)
    await pipeline2.ingest_text(_doc("hybridresume"), title=title)
    fast_calls = pipeline2.llm.call_count
    await pipeline2.close()

    assert sorted(second.parts) == ["Part 2", "Part 3"]
    assert fast_calls == 2, "the receipted chunk is not scored again"
    doc_ref = await _doc_ref(db_pool, title)
    assert doc_ref in await _receipts(db_pool, doc_ref)


# --- 5. Cancel ---------------------------------------------------------------


async def test_slow_reading_stops_between_chunks_when_cancelled(db_pool, monkeypatch):
    from services.ingest import IngestionMode

    cancel = {"set": False}

    class _CancelAfterFirst(_FakeSlowRead):
        async def __call__(self, **kwargs):
            out = await super().__call__(**kwargs)
            cancel["set"] = True
            return out

    fake = _CancelAfterFirst({})
    monkeypatch.setattr("services.slow_ingest_rlm.run_slow_ingest_chunk", fake)
    for mode, tag in ((IngestionMode.SLOW, "cancelslow"), (IngestionMode.HYBRID, "cancelhybrid")):
        cancel["set"] = False
        fake.parts.clear()
        pipeline = await _make_pipeline(db_pool, mode)
        pipeline.config.cancel_check = lambda: cancel["set"]
        with pytest.raises(RuntimeError, match="Ingestion cancelled"):
            await pipeline.ingest_text(_doc(tag), title=f"Cancel {tag}")
        await pipeline.close()
        assert len(fake.parts) == 1, f"{mode.value}: no chunk is read after cancel"


async def test_job_finishing_after_a_cancel_request_ends_cancelled(db_pool):
    job_id = await _enqueue(db_pool, "Cancel at the end", "text", "fast", "cancel-at-end-hash")
    async with db_pool.acquire() as conn:
        await conn.fetchval("SELECT claim_ingestion_jobs(10)")
        await conn.fetchval("SELECT cancel_ingestion_job($1::uuid)", job_id)
        done = json.loads(await conn.fetchval(
            "SELECT complete_ingestion_job($1::uuid, $2::jsonb)", job_id, json.dumps({"memories_created": 1})
        ))
    assert done["status"] == "cancelled"
    job = await _job(db_pool, job_id)
    assert job["status"] == "cancelled"
    assert job["result"]["memories_created"] == 1


# --- 3. Setup failures and stale jobs never stay in_progress ---------------


async def test_job_whose_setup_fails_backs_off_instead_of_hanging(db_pool, monkeypatch):
    from services.ingest import IngestionMode
    from services.ingest import pipeline as pipeline_mod
    from services.ingest.jobs import run_ingestion_jobs_step

    def _broken_init(self, config):
        raise RuntimeError("pipeline could not be built")

    monkeypatch.setattr(pipeline_mod.IngestionPipeline, "__init__", _broken_init)
    job_id = await _enqueue(db_pool, "Setup fails", "text body", "fast", "setup-fails-hash")
    await run_ingestion_jobs_step(db_pool, config_override=_config(IngestionMode.FAST))

    job = await _job(db_pool, job_id)
    assert job["status"] == "pending"
    assert "pipeline could not be built" in job["error"]


async def test_stale_job_on_its_last_attempt_fails_instead_of_being_reclaimed(db_pool):
    ids = {}
    for name in ("exhausted", "cancelled", "retryable"):
        ids[name] = await _enqueue(db_pool, f"Stale {name}", "text", "fast", f"stale-{name}-hash")
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE ingestion_jobs
            SET status = 'in_progress', claimed_at = now() - interval '2 hours',
                attempts = CASE WHEN id = $1::uuid THEN max_attempts ELSE 1 END,
                cancel_requested = (id = $2::uuid)
            WHERE id = ANY($3::uuid[])
            """,
            ids["exhausted"], ids["cancelled"], list(ids.values()),
        )
        claimed = json.loads(await conn.fetchval("SELECT claim_ingestion_jobs(10, 60)"))

    claimed_ids = {str(j["id"]) for j in claimed}
    assert str(ids["retryable"]) in claimed_ids
    assert str(ids["exhausted"]) not in claimed_ids
    assert str(ids["cancelled"]) not in claimed_ids
    exhausted = await _job(db_pool, ids["exhausted"])
    assert exhausted["status"] == "failed"
    assert "stopped without finishing" in exhausted["error"]
    assert (await _job(db_pool, ids["cancelled"]))["status"] == "cancelled"


# --- 6. Slow-read model calls go through the pipeline's hook ---------------


async def test_slow_read_calls_wait_on_the_pipeline_hook_and_are_counted(db_pool, monkeypatch):
    import core.usage
    import services.hexis_rlm as rlm
    from services.ingest import IngestionMode

    calls = {"model": 0, "hook": 0}
    answer = json.dumps(_assessment(["Slow fact read through the counted hook."]))

    async def _answer(**_kwargs):
        calls["model"] += 1
        return {"content": f"FINAL({answer})", "raw": None}

    async def _no_usage(**_kwargs):
        return None

    async def _hook():
        calls["hook"] += 1

    monkeypatch.setattr(rlm, "chat_completion", _answer)
    monkeypatch.setattr(core.usage, "record_llm_usage", _no_usage)

    title = "Slow hook doc"
    pipeline = await _make_pipeline(db_pool, IngestionMode.SLOW)
    pipeline.llm.before_call = _hook
    await pipeline.ingest_text(_doc("hook"), title=title)
    counted = pipeline.llm.call_count
    await pipeline.close()

    assert calls["model"] == 3, "one FINAL answer per chunk"
    assert calls["hook"] == 3, "every slow-read call waits on the pipeline hook"
    assert counted == 3, "slow-read calls are counted with the pipeline's calls"
    async with db_pool.acquire() as conn:
        llm_calls = await conn.fetchval(
            "SELECT llm_calls FROM ingestion_metrics ORDER BY created_at DESC LIMIT 1"
        )
    assert llm_calls == 3
