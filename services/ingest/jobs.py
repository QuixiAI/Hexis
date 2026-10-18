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
from dataclasses import replace
from typing import Any

logger = logging.getLogger(__name__)

_PROGRESS_INTERVAL_SECONDS = 15.0


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


async def _process_job(pool, job: dict[str, Any], *, config_override=None) -> None:
    from core.tools.ingest import _build_ingest_config

    from .config import IngestionMode
    from .pipeline import IngestionPipeline

    job_id = job["id"]
    payload = job.get("payload") or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    mode_value = str(payload.get("mode") or "fast")

    cancel_flag = {"set": False}
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
    # Chat comes first: each model call for this document waits while a
    # chat turn is live, so a person's question never queues behind reading.
    from core.chat_priority import wait_for_chat_idle

    pipeline.llm.before_call = lambda: wait_for_chat_idle(pool)

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
        return await pipeline.ingest_text(
            job.get("content") or "",
            title=payload.get("title"),
            source_type=str(payload.get("source_type") or "pasted_text"),
        )

    try:
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
            await conn.fetchval(
                "SELECT complete_ingestion_job($1::uuid, $2::jsonb)",
                job_id,
                json.dumps(result),
            )
        logger.info("ingestion job %s completed: %s memories", job_id, count)
    except Exception as exc:
        async with pool.acquire() as conn:
            outcome_raw = await conn.fetchval(
                "SELECT fail_ingestion_job($1::uuid, $2)", job_id, str(exc)[:2000]
            )
        outcome = json.loads(outcome_raw) if isinstance(outcome_raw, str) else outcome_raw
        logger.error(
            "ingestion job %s failed (%s): %s", job_id, (outcome or {}).get("status"), exc
        )
    finally:
        await pipeline.close()


async def run_ingestion_jobs_step(pool, *, config_override=None) -> int:
    """Claim and process due ingestion jobs; returns how many were handled."""
    async with pool.acquire() as conn:
        raw = await conn.fetchval("SELECT claim_ingestion_jobs()")
    jobs = json.loads(raw) if isinstance(raw, str) else (raw or [])
    for job in jobs:
        await _process_job(pool, job, config_override=config_override)
    return len(jobs)
