"""Chat comes first.

A chat turn holds a short lease in the database (chat_turn_leases). Background
work that is about to call the model waits for the lease to clear, so on a
box where the model is the bottleneck a person's question never queues
behind a document import. Calls already in flight finish; only new ones wait.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

POLL_SECONDS = 0.5


async def _fetchval(pool_or_conn: Any, query: str, *args: Any) -> Any:
    if hasattr(pool_or_conn, "acquire"):
        async with pool_or_conn.acquire() as conn:
            return await conn.fetchval(query, *args)
    return await pool_or_conn.fetchval(query, *args)


async def begin_chat_turn(pool_or_conn: Any, session_id: str, ttl_seconds: int = 360) -> None:
    try:
        await _fetchval(pool_or_conn, "SELECT chat_turn_begin($1::text, $2::int)", session_id, int(ttl_seconds))
    except Exception:
        logger.warning("Could not record the chat turn lease", exc_info=True)


async def end_chat_turn(pool_or_conn: Any, session_id: str) -> None:
    try:
        await _fetchval(pool_or_conn, "SELECT chat_turn_end($1::text)", session_id)
    except Exception:
        logger.warning("Could not clear the chat turn lease", exc_info=True)


async def chat_turn_active(pool_or_conn: Any) -> bool:
    try:
        return bool(await _fetchval(pool_or_conn, "SELECT chat_turn_active()"))
    except Exception:
        # Never let the gate itself stop background work.
        return False


async def wait_for_chat_idle(pool_or_conn: Any, *, max_wait_seconds: float | None = None) -> float:
    """Block until no chat turn is live, or until max_wait_seconds have passed.

    Returns how long it waited. The cap keeps a busy chat from starving
    background work entirely; a few calls per turn is the cost.
    """
    if max_wait_seconds is None:
        try:
            raw = await _fetchval(pool_or_conn, "SELECT get_config_int('chat.priority_max_wait_s')")
            max_wait_seconds = float(raw) if raw is not None else 180.0
        except Exception:
            max_wait_seconds = 180.0
    start = time.monotonic()
    waited_once = False
    while await chat_turn_active(pool_or_conn):
        if time.monotonic() - start >= max_wait_seconds:
            logger.info("Background model call going ahead after waiting %.0fs for chat", max_wait_seconds)
            break
        if not waited_once:
            logger.debug("Background model call waiting for the chat turn to finish")
            waited_once = True
        await asyncio.sleep(POLL_SECONDS)
    return time.monotonic() - start
