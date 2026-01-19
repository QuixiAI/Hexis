from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import requests


RABBITMQ_ENABLED = os.getenv("RABBITMQ_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
RABBITMQ_MANAGEMENT_URL = os.getenv("RABBITMQ_MANAGEMENT_URL", "http://rabbitmq:15672").rstrip("/")
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "hexis")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "hexis_password")
RABBITMQ_VHOST = os.getenv("RABBITMQ_VHOST", "/")
RABBITMQ_OUTBOX_QUEUE = os.getenv("RABBITMQ_OUTBOX_QUEUE", "hexis.outbox")
RABBITMQ_INBOX_QUEUE = os.getenv("RABBITMQ_INBOX_QUEUE", "hexis.inbox")
RABBITMQ_POLL_INBOX_EVERY = float(os.getenv("RABBITMQ_POLL_INBOX_EVERY", 1.0))


class RabbitMQBridge:
    def __init__(self, pool):
        self.pool = pool
        self._last_inbox_poll = 0.0

    def _vhost_path(self) -> str:
        if RABBITMQ_VHOST == "/":
            return "%2F"
        return requests.utils.quote(RABBITMQ_VHOST, safe="")

    async def _request(self, method: str, path: str, payload: dict | None = None) -> requests.Response:
        url = f"{RABBITMQ_MANAGEMENT_URL}{path}"
        auth = (RABBITMQ_USER, RABBITMQ_PASSWORD)

        def _do() -> requests.Response:
            return requests.request(method, url, auth=auth, json=payload, timeout=5)

        return await asyncio.to_thread(_do)

    async def ensure_ready(self) -> None:
        try:
            resp = await self._request("GET", "/api/overview")
            if resp.status_code != 200:
                raise RuntimeError(f"rabbitmq overview HTTP {resp.status_code}")

            vhost = self._vhost_path()
            for q in (RABBITMQ_OUTBOX_QUEUE, RABBITMQ_INBOX_QUEUE):
                r = await self._request(
                    "PUT",
                    f"/api/queues/{vhost}/{requests.utils.quote(q, safe='')}",
                    payload={"durable": True, "auto_delete": False, "arguments": {}},
                )
                if r.status_code not in (200, 201, 204):
                    raise RuntimeError(f"rabbitmq queue declare {q!r} HTTP {r.status_code}: {r.text[:200]}")
        except Exception:
            return

    async def publish_outbox_messages(self, max_messages: int = 20) -> int:
        if not RABBITMQ_ENABLED or not self.pool:
            return 0

        published = 0
        vhost = self._vhost_path()
        for _ in range(max_messages):
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, kind, payload
                    FROM outbox_messages
                    WHERE status = 'pending'
                    ORDER BY created_at
                    LIMIT 1
                    """
                )
                if not row:
                    return published
                msg_id = row["id"]
                kind = row["kind"]
                payload = row["payload"]

            body = {"id": str(msg_id), "kind": kind, "payload": payload}
            try:
                resp = await self._request(
                    "POST",
                    f"/api/exchanges/{vhost}/amq.default/publish",
                    payload={
                        "properties": {"content_type": "application/json"},
                        "routing_key": RABBITMQ_OUTBOX_QUEUE,
                        "payload": json.dumps(body, default=str),
                        "payload_encoding": "string",
                    },
                )
                ok = resp.status_code == 200 and bool(resp.json().get("routed"))
                if not ok:
                    raise RuntimeError(f"publish not routed: HTTP {resp.status_code} body={resp.text[:200]}")

                async with self.pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE outbox_messages
                        SET status = 'sent', sent_at = CURRENT_TIMESTAMP, error_message = NULL
                        WHERE id = $1::uuid
                        """,
                        msg_id,
                    )
                published += 1
            except Exception as exc:
                async with self.pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE outbox_messages
                        SET status = 'failed', error_message = $2
                        WHERE id = $1::uuid
                        """,
                        msg_id,
                        str(exc),
                    )
                return published

        return published

    async def poll_inbox_messages(self, max_messages: int = 10) -> int:
        if not RABBITMQ_ENABLED or not self.pool:
            return 0

        now = time.monotonic()
        if now - self._last_inbox_poll < RABBITMQ_POLL_INBOX_EVERY:
            return 0
        self._last_inbox_poll = now

        vhost = self._vhost_path()
        try:
            resp = await self._request(
                "POST",
                f"/api/queues/{vhost}/{requests.utils.quote(RABBITMQ_INBOX_QUEUE, safe='')}/get",
                payload={
                    "count": max_messages,
                    "ackmode": "ack_requeue_false",
                    "encoding": "auto",
                    "truncate": 50000,
                },
            )
            if resp.status_code != 200:
                raise RuntimeError(f"inbox get HTTP {resp.status_code}: {resp.text[:200]}")
            msgs = resp.json()
            if not isinstance(msgs, list):
                return 0
        except Exception:
            return 0

        ingested = 0
        for msg in msgs:
            payload = msg.get("payload")
            content: Any = payload
            try:
                parsed = json.loads(payload) if isinstance(payload, str) else payload
                if isinstance(parsed, dict) and "content" in parsed:
                    content = parsed["content"]
                else:
                    content = parsed
            except Exception:
                pass

            try:
                async with self.pool.acquire() as conn:
                    await conn.fetchval(
                        "SELECT add_to_working_memory($1::text, INTERVAL '1 day')",
                        str(content),
                    )
                    await conn.execute(
                        "UPDATE heartbeat_state SET last_user_contact = CURRENT_TIMESTAMP WHERE id = 1"
                    )
                ingested += 1
            except Exception:
                return ingested

        return ingested
