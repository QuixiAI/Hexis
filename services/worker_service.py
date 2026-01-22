from __future__ import annotations

import argparse
import asyncio
import logging
import os
import asyncpg
from dotenv import load_dotenv

from core.agent_api import db_dsn_from_env
from core.rabbitmq_bridge import RabbitMQBridge
from core.state import (
    is_agent_terminated,
    mark_subconscious_decider_run,
    run_heartbeat,
    run_maintenance_if_due,
    should_run_subconscious_decider,
)
from services.external_calls import ExternalCallProcessor
from services.heartbeat_runner import execute_heartbeat_decision
from services.subconscious import run_subconscious_decider


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
        self.bridge: RabbitMQBridge | None = None
        self.call_processor = ExternalCallProcessor(max_retries=MAX_RETRIES)

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(dsn=db_dsn_from_env(), min_size=2, max_size=10)
        logger.info("Connected to database")
        self.bridge = RabbitMQBridge(self.pool)
        await self.bridge.ensure_ready()

    async def disconnect(self) -> None:
        if self.pool:
            await self.pool.close()
            logger.info("Disconnected from database")

    async def _publish_outbox(self, messages: list[dict]) -> None:
        if not messages:
            return
        if self.bridge:
            await self.bridge.publish_outbox_payloads(messages)

    async def _run_heartbeat_if_due(self) -> None:
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            payload = await run_heartbeat(conn)
            if not payload:
                return
            heartbeat_id = payload.get("heartbeat_id")
            if heartbeat_id:
                logger.info(f"Heartbeat started: {heartbeat_id}")

            outbox_messages = payload.get("outbox_messages")
            if isinstance(outbox_messages, list):
                await self._publish_outbox(outbox_messages)

            external_calls = payload.get("external_calls")
            if not isinstance(external_calls, list):
                return

            for call in external_calls:
                if not isinstance(call, dict):
                    continue
                call_type = str(call.get("call_type") or "")
                call_input = call.get("input") or {}
                if not isinstance(call_input, dict):
                    call_input = {}
                try:
                    result = await self.call_processor.process_call_payload(conn, call_type, call_input)
                    applied = await self.call_processor.apply_result(conn, call, result)
                except Exception as exc:
                    logger.error(f"Error processing external call: {exc}")
                    continue

                if isinstance(applied, dict):
                    outbox_messages = applied.get("outbox_messages")
                    if isinstance(outbox_messages, list):
                        await self._publish_outbox(outbox_messages)

                if (
                    isinstance(result, dict)
                    and result.get("kind") == "heartbeat_decision"
                    and "decision" in result
                    and heartbeat_id
                ):
                    exec_result = await execute_heartbeat_decision(
                        conn,
                        heartbeat_id=str(heartbeat_id),
                        decision=result["decision"],
                        call_processor=self.call_processor,
                    )
                    if isinstance(exec_result, dict):
                        outbox_messages = exec_result.get("outbox_messages")
                        if isinstance(outbox_messages, list):
                            await self._publish_outbox(outbox_messages)
                        if exec_result.get("terminated") is True:
                            logger.info("Termination executed; stopping workers.")
                            self.stop()
                    return

    async def run(self) -> None:
        self.running = True
        logger.info("Heartbeat worker starting...")
        await self.connect()

        try:
            while self.running:
                try:
                    if await self._is_agent_terminated():
                        logger.info("Agent is terminated; heartbeat worker exiting.")
                        break
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
