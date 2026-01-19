from __future__ import annotations

import argparse
import asyncio
import logging
import os
import asyncpg
from dotenv import load_dotenv

from core.agent_api import db_dsn_from_env
from core.rabbitmq_bridge import RabbitMQBridge, RABBITMQ_ENABLED
from core.state import (
    is_agent_terminated,
    mark_subconscious_decider_run,
    run_heartbeat,
    run_maintenance_if_due,
    should_run_subconscious_decider,
)
from services.consent import ensure_consent
from services.external_calls import ExternalCallProcessor
from services.subconscious import run_subconscious_decider
from services.worker_tasks import process_pending_call


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("heartbeat_worker")

POLL_INTERVAL = float(os.getenv("WORKER_POLL_INTERVAL", 1.0))
MAX_RETRIES = int(os.getenv("WORKER_MAX_RETRIES", 3))


class HeartbeatWorker:
    """Stateless worker that bridges the database and external APIs."""

    def __init__(self):
        self.pool: asyncpg.Pool | None = None
        self.running = False
        self.call_processor = ExternalCallProcessor(max_retries=MAX_RETRIES)

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(dsn=db_dsn_from_env(), min_size=2, max_size=10)
        logger.info("Connected to database")

    async def disconnect(self) -> None:
        if self.pool:
            await self.pool.close()
            logger.info("Disconnected from database")

    async def _ensure_consent(self) -> bool:
        if not self.pool:
            return False
        try:
            async with self.pool.acquire() as conn:
                return await ensure_consent(conn, dsn=db_dsn_from_env())
        except Exception as exc:
            logger.error(f"Consent flow failed: {exc}")
            return False

    async def _process_pending_call(self) -> None:
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            try:
                payload = await process_pending_call(conn, call_processor=self.call_processor)
            except Exception as exc:
                logger.error(f"Error processing pending call: {exc}")
                return
        if payload and payload.get("execution", {}).get("terminated") is True:
            logger.info("Termination executed; stopping workers and skipping heartbeat completion.")
            self.stop()

    async def _run_heartbeat_if_due(self) -> None:
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            heartbeat_id = await run_heartbeat(conn)
            if heartbeat_id:
                logger.info(f"Heartbeat started: {heartbeat_id}")

    async def run(self) -> None:
        self.running = True
        logger.info("Heartbeat worker starting...")
        await self.connect()

        if not await self._ensure_consent():
            logger.warning("LLM consent not granted; heartbeat worker exiting.")
            self.running = False
            return

        try:
            while self.running:
                try:
                    if await self._is_agent_terminated():
                        logger.info("Agent is terminated; heartbeat worker exiting.")
                        break
                    await self._process_pending_call()
                    await self._run_heartbeat_if_due()
                except Exception as exc:
                    logger.error(f"Worker loop error: {exc}")
                await asyncio.sleep(POLL_INTERVAL)
        finally:
            await self.disconnect()

    def stop(self) -> None:
        self.running = False
        logger.info("Heartbeat worker stopping...")

    async def _is_agent_terminated(self) -> bool:
        if not self.pool:
            return False
        try:
            async with self.pool.acquire() as conn:
                return await is_agent_terminated(conn)
        except Exception:
            return False


class MaintenanceWorker:
    """Subconscious maintenance loop: consolidates/prunes substrate on its own trigger."""

    def __init__(self):
        self.pool: asyncpg.Pool | None = None
        self.running = False
        self.bridge: RabbitMQBridge | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(dsn=db_dsn_from_env(), min_size=1, max_size=5)
        logger.info("Connected to database")
        if RABBITMQ_ENABLED:
            self.bridge = RabbitMQBridge(self.pool)
            await self.bridge.ensure_ready()

    async def disconnect(self) -> None:
        if self.pool:
            await self.pool.close()
            logger.info("Disconnected from database")

    async def _run_maintenance_if_due(self) -> None:
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            stats = await run_maintenance_if_due(conn, {})
            if stats is None:
                return
            if not stats.get("skipped"):
                logger.info(f"Subconscious maintenance: {stats}")

    async def _run_subconscious_if_due(self) -> None:
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            should_run = await should_run_subconscious_decider(conn)
            if not should_run:
                return
            result = await run_subconscious_decider(conn)
            await mark_subconscious_decider_run(conn)
            logger.info(f"Subconscious decider: {result}")

    async def run(self) -> None:
        self.running = True
        logger.info("Maintenance worker starting...")
        await self.connect()
        try:
            while self.running:
                try:
                    if await self._is_agent_terminated():
                        logger.info("Agent is terminated; maintenance worker exiting.")
                        break
                    if self.bridge:
                        await self.bridge.poll_inbox_messages()
                        await self.bridge.publish_outbox_messages(max_messages=10)
                    await self._run_maintenance_if_due()
                    await self._run_subconscious_if_due()
                except Exception as exc:
                    logger.error(f"Maintenance loop error: {exc}")
                await asyncio.sleep(POLL_INTERVAL)
        finally:
            await self.disconnect()

    def stop(self) -> None:
        self.running = False
        logger.info("Maintenance worker stopping...")

    async def _is_agent_terminated(self) -> bool:
        if not self.pool:
            return False
        try:
            async with self.pool.acquire() as conn:
                return await is_agent_terminated(conn)
        except Exception:
            return False


async def _amain(mode: str) -> None:
    hb_worker = HeartbeatWorker()
    maint_worker = MaintenanceWorker()

    import signal

    def shutdown(signum, frame):
        hb_worker.stop()
        maint_worker.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    mode = (mode or "both").strip().lower()
    if mode == "heartbeat":
        await hb_worker.run()
        return
    if mode == "maintenance":
        await maint_worker.run()
        return
    if mode == "both":
        await asyncio.gather(hb_worker.run(), maint_worker.run())
        return
    raise ValueError("mode must be one of: heartbeat, maintenance, both")


def main() -> int:
    p = argparse.ArgumentParser(prog="hexis-worker", description="Run Hexis background workers.")
    p.add_argument(
        "--mode",
        choices=["heartbeat", "maintenance", "both"],
        default=os.getenv("HEXIS_WORKER_MODE", "both"),
        help="Which worker to run.",
    )
    args = p.parse_args()
    asyncio.run(_amain(args.mode))
    return 0


__all__ = [
    "HeartbeatWorker",
    "MaintenanceWorker",
    "main",
    "MAX_RETRIES",
]
