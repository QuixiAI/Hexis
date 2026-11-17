"""Durable ingestion-job consumer (#87).

The maintenance worker calls run_ingestion_jobs_step() each tick; the queue
table (db/73) is the state — claim with SKIP LOCKED + stale reclaim, progress
heartbeats that extend the claim and surface cancellation, exponential-backoff
failure, and completion with the memory count. The pipeline itself resumes
any partial document via receipts (#85), so a reclaimed job never redoes
finished sections.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import replace
from typing import Any

logger = logging.getLogger(__name__)

_PROGRESS_INTERVAL_SECONDS = 15.0
_REFILL_SECONDS = 3.0


def _infer_acquisition(payload: dict[str, Any]) -> str | None:
    explicit = str(payload.get("acquisition") or "").strip()
    if explicit:
        return explicit
    connector_markers = (
        "connector_id",
        "account_key",
        "provider_item_id",
        "provider_thread_id",
        "platform_message_id",
        "channel_type",
        "channel_id",
    )
    if any(str(payload.get(key) or "").strip() for key in connector_markers):
        return "connector"
    return None


# Failures that end once a service is back. Deliberately narrow: a document
# that fails this way is retried without limit, so bad input must never look
# like an outage (core.llm's in-call retry rule is broader, which suits a
# few quick retries but not this).
_OUTAGE_STATUS = {408, 429, 500, 502, 503, 504, 529}
_OUTAGE_TYPES = {
    "RateLimitError", "Timeout", "APITimeoutError", "TimeoutException", "ConnectTimeout", "ReadTimeout",
    "ServiceUnavailableError", "InternalServerError", "BadGatewayError", "APIConnectionError",
    "ConnectError", "RemoteProtocolError",
}
_OUTAGE_TEXT = re.compile(
    r"embedding service not available|rate[ -]?limit|too many requests|overloaded|timed out|\btimeout\b"
    r"|temporarily unavailable|service unavailable|connection (?:refused|reset|aborted)",
    re.IGNORECASE,
)


def _is_outage(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(status, int) and status in _OUTAGE_STATUS:
        return True
    # ConnectionError, not OSError: a missing or unreadable file is bad input.
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError)):
        return True
    if any(cls.__name__ in _OUTAGE_TYPES for cls in type(exc).__mro__):
        return True
    return bool(_OUTAGE_TEXT.search(str(exc)))


def _is_transient_failure(exc: BaseException) -> bool:
    """A failure that retrying fixes: the model or embedding service was
    down, slow or rate-limited. Bad input (unreadable file, model output that
    never parses) is not; it fails after its attempts and stays in the log."""
    from .pipeline import IngestSectionError

    if isinstance(exc, IngestSectionError) and exc.causes:
        return all(_is_transient_failure(cause) for cause in exc.causes)
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if _is_outage(current):
            return True
        current = current.__cause__ or current.__context__
    return False


async def _stored_document(pool, document_id: Any) -> dict[str, Any]:
    """Hash, path and file type of the document a connector or channel stored
    for this job, so reading lands on that row instead of a second copy."""
    if not document_id:
        return {}
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT content_hash, path, file_type FROM source_documents WHERE id = $1::uuid",
            str(document_id),
        )
    if row is None:
        return {}
    stored: dict[str, Any] = {"content_hash": row["content_hash"]}
    if row["path"]:
        stored["path"] = row["path"]
    if row["file_type"]:
        stored["file_type"] = row["file_type"]
    return stored


async def _process_job(pool, job: dict[str, Any], *, config_override=None) -> None:
    from core.tools.ingest import _build_ingest_config

    from .config import IngestionMode
    from .pipeline import IngestionPipeline

    job_id = job["id"]
    cancel_flag = {"set": False}
    pipeline = None

    async def _report_progress(progress: dict[str, int]) -> None:
        # Sections, facts, memories saved and merged so far: the ingestion
        # view turns each change into something you see happen.
        async with pool.acquire() as conn:
            await conn.fetchval(
                "SELECT update_ingestion_job_progress($1::uuid, $2::jsonb)", job_id, json.dumps(progress)
            )

    async def _ingest_artifact() -> int:
        """Re-read preserved original bytes and run the file pipeline. The
        artifact row is the durable source; the temp file is scratch."""
        import tempfile
        from pathlib import Path

        artifact_id = str(payload.get("artifact_id") or "")
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT bytes, storage_kind, storage_ref, original_filename, sha256
                FROM source_artifacts WHERE id = $1::uuid AND status = 'active'
                """,
                artifact_id,
            )
        if row is None:
            raise RuntimeError(f"artifact {artifact_id} not found or not active")
        if row["bytes"] is not None:
            data = bytes(row["bytes"])
        elif row["storage_kind"] == "filesystem" and row["storage_ref"]:
            artifact_path = config.resolve_artifact_dir() / row["storage_ref"]
            if not artifact_path.exists():
                raise RuntimeError(
                    f"artifact file missing: {artifact_path} — check HEXIS_ARTIFACT_DIR"
                )
            data = artifact_path.read_bytes()
        else:
            raise RuntimeError(
                f"artifact {artifact_id} has no bytes and no filesystem reference"
            )

        suffix = Path(str(row["original_filename"] or payload.get("filename") or "upload.txt")).suffix or ".txt"
        with tempfile.TemporaryDirectory(prefix="hexis_artifact_") as tmpdir:
            name = str(row["original_filename"] or payload.get("filename") or f"upload{suffix}")
            tmp_path = Path(tmpdir) / name
            tmp_path.write_bytes(data)
            return await pipeline.ingest_file(tmp_path)

    async def _ingest() -> int:
        if job["kind"] == "url":
            return await pipeline.ingest_url(
                str(payload.get("url") or ""), title=payload.get("title")
            )
        if job["kind"] == "artifact":
            return await _ingest_artifact()
        stored = await _stored_document(pool, payload.get("source_document_id"))
        return await pipeline.ingest_text(
            job.get("content") or "",
            title=payload.get("title"),
            source_type=str(payload.get("source_type") or "pasted_text"),
            **stored,
        )

    # Setup is inside the try: a job whose config or pipeline cannot be
    # built fails (and backs off) instead of staying in_progress forever.
    try:
        payload = job.get("payload") or {}
        if isinstance(payload, str):
            payload = json.loads(payload)
        mode_value = str(payload.get("mode") or "fast")

        if config_override is not None:
            config = replace(config_override)
        else:
            config = await _build_ingest_config(pool, mode=IngestionMode(mode_value))
        config.verbose = False
        config.cancel_check = lambda: cancel_flag["set"]
        if str(payload.get("sensitivity") or "").strip().lower() == "private":
            config.sensitivity = "private"
        acquisition = _infer_acquisition(payload)
        if acquisition:
            config.acquisition = acquisition
        acquired_reason = str(
            payload.get("acquired_reason") or payload.get("keep_reason") or ""
        ).strip()
        if acquired_reason:
            config.acquired_reason = acquired_reason
        pipeline = IngestionPipeline(config)
        pipeline.on_progress = _report_progress
        # Chat comes first: each model call for this document waits while a
        # chat turn is live, so a person's question never queues behind reading.
        from core.chat_priority import wait_for_chat_idle

        pipeline.llm.before_call = lambda: wait_for_chat_idle(pool)

        task = asyncio.ensure_future(_ingest())
        while True:
            done, _pending = await asyncio.wait({task}, timeout=_PROGRESS_INTERVAL_SECONDS)
            if done:
                break
            # Heartbeat: extends the stale-claim window and carries back the
            # cancel request in one round trip.
            async with pool.acquire() as conn:
                cancel_requested = await conn.fetchval(
                    "SELECT update_ingestion_job_progress($1::uuid, '{}'::jsonb)", job_id
                )
            if cancel_requested:
                cancel_flag["set"] = True

        count = task.result()
        result = {"memories_created": count}
        if isinstance(getattr(pipeline, "last_result", None), dict):
            result.update(pipeline.last_result)
        async with pool.acquire() as conn:
            done_raw = await conn.fetchval(
                "SELECT complete_ingestion_job($1::uuid, $2::jsonb)",
                job_id,
                json.dumps(result),
            )
        done = json.loads(done_raw) if isinstance(done_raw, str) else done_raw
        logger.info(
            "ingestion job %s %s: %s memories", job_id, (done or {}).get("status", "completed"), count
        )
    except Exception as exc:
        async with pool.acquire() as conn:
            if _is_transient_failure(exc):
                # A service outage never uses up a document's attempts: it
                # retries with the normal backoff until the service is back.
                await conn.execute(
                    "UPDATE ingestion_jobs SET attempts = LEAST(attempts, max_attempts - 1)"
                    " WHERE id = $1::uuid",
                    job_id,
                )
            outcome_raw = await conn.fetchval(
                "SELECT fail_ingestion_job($1::uuid, $2)", job_id, str(exc)[:2000]
            )
        outcome = json.loads(outcome_raw) if isinstance(outcome_raw, str) else outcome_raw
        logger.error(
            "ingestion job %s failed (%s): %s", job_id, (outcome or {}).get("status"), exc
        )
    finally:
        if pipeline is not None:
            await pipeline.close()


async def _config_int(pool, key: str, default: int) -> int:
    async with pool.acquire() as conn:
        value = await conn.fetchval("SELECT get_config_int($1)", key)
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


async def _claim(pool, limit: int) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        raw = await conn.fetchval("SELECT claim_ingestion_jobs($1)", limit)
    return json.loads(raw) if isinstance(raw, str) else (raw or [])


async def run_ingestion_jobs_step(pool, *, config_override=None, until_empty: bool = False) -> int:
    """Read due ingestion jobs, keeping ingest.job_batch_size documents in
    flight at once; returns how many were handled.

    A slot is refilled the moment its document finishes, so a batch no longer
    idles behind its slowest document. New claims stop after
    ingest.job_step_budget_s so a caller sharing its loop with other work
    (the CLI, tests) gets control back; documents already in flight run to
    completion. The maintenance worker runs this as its own task and passes
    until_empty=True, so slots stay full until nothing is due.
    """
    slots = max(1, await _config_int(pool, "ingest.job_batch_size", 1))
    budget_s = max(0, await _config_int(pool, "ingest.job_step_budget_s", 120))
    loop = asyncio.get_running_loop()
    deadline = float("inf") if until_empty else loop.time() + budget_s
    running: set[asyncio.Task] = set()
    handled = 0
    while True:
        free = slots - len(running)
        if free > 0 and loop.time() < deadline:
            jobs = await _claim(pool, free)
            for job in jobs:
                running.add(asyncio.ensure_future(_process_job(pool, job, config_override=config_override)))
            handled += len(jobs)
        if not running:
            break  # nothing in flight and nothing due
        # Wake when a document finishes, or every few seconds to fill free
        # slots with documents queued meanwhile (a long read no longer holds
        # them back).
        done, running = await asyncio.wait(running, timeout=_REFILL_SECONDS, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if not task.cancelled() and task.exception() is not None:
                # _process_job records job failures itself; this is a bug in the consumer.
                logger.exception("ingestion job task crashed", exc_info=task.exception())
    return handled
