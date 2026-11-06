"""The ingest worker: reads queued documents as soon as they are queued.

Reading used to be one step of the maintenance loop, so a queued document
waited behind every other step. This worker does nothing else: it LISTENs on
the ``ingestion_jobs`` channel (db 0055) and keeps ``ingest.job_batch_size``
documents in flight until the queue is empty. It also wakes every
``_RECHECK_SECONDS`` for what a notification can't announce: retries coming
due and jobs left in progress by a worker that stopped (claim timeout).
Several can run at once; claims use SKIP LOCKED.
"""
from __future__ import annotations

import asyncio
import logging
import os

import asyncpg

from core.agent_api import db_dsn_from_env

logger = logging.getLogger(__name__)

_RECHECK_SECONDS = 30.0


class IngestWorker:
    def __init__(self, instance: str | None = None):
        self.instance = instance or os.getenv("HEXIS_INSTANCE")
        self.pool: asyncpg.Pool | None = None
        self.running = False
        self.worker_id: str | None = None
        self._wake = asyncio.Event()

    def stop(self) -> None:
        self.running = False
        self._wake.set()
        logger.info("Ingest worker stopping...")

    async def _agent_ready(self) -> bool:
        async with self.pool.acquire() as conn:
            return bool(await conn.fetchval("SELECT is_agent_configured() AND is_init_complete()"))

    async def _listen(self, dsn: str) -> asyncpg.Connection:
        conn = await asyncpg.connect(dsn)
        await conn.add_listener("ingestion_jobs", lambda *_: self._wake.set())
        return conn

    async def run(self) -> None:
        from services.ingest.jobs import run_ingestion_jobs_step
        from services.worker_service import (
            _mark_worker_seen,
            _mark_worker_stopped,
            _register_worker_instance,
        )

        dsn = db_dsn_from_env(self.instance)
        self.pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=8)
        self.worker_id = await _register_worker_instance(self.pool, "ingest", self.instance)
        listener: asyncpg.Connection | None = None
        self.running = True
        logger.info("Ingest worker started")
        try:
            while self.running:
                try:
                    if listener is None or listener.is_closed():
                        listener = await self._listen(dsn)
                    await _mark_worker_seen(self.pool, self.worker_id)
                    self._wake.clear()
                    if await self._agent_ready():
                        handled = await run_ingestion_jobs_step(self.pool, until_empty=True)
                        if handled:
                            logger.info("Ingest worker read %d documents", handled)
                except Exception:
                    logger.exception("Ingest worker loop error")
                    listener = None
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=_RECHECK_SECONDS)
                except asyncio.TimeoutError:
                    pass
        finally:
            if listener is not None and not listener.is_closed():
                await listener.close()
            await _mark_worker_stopped(self.pool, self.worker_id, reason="shutdown")
            await self.pool.close()
