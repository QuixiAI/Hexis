from __future__ import annotations

import json
from typing import Any


def _normalize_payload(row: Any) -> dict[str, Any] | None:
    if not row:
        return None
    payload = dict(row)
    call_input = payload.get("input")
    if isinstance(call_input, str):
        try:
            payload["input"] = json.loads(call_input)
        except Exception:
            pass
    return payload


async def claim_pending_call(conn) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        UPDATE external_calls
        SET status = 'processing'::external_call_status, started_at = CURRENT_TIMESTAMP
        WHERE id = (
            SELECT id FROM external_calls
            WHERE status = 'pending'::external_call_status
            ORDER BY requested_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        RETURNING id, call_type, input, heartbeat_id, retry_count
        """
    )
    return _normalize_payload(row)


async def claim_call_by_id(conn, call_id: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        UPDATE external_calls
        SET status = 'processing'::external_call_status, started_at = CURRENT_TIMESTAMP
        WHERE id = $1::uuid AND status = 'pending'::external_call_status
        RETURNING id, call_type, input, heartbeat_id, retry_count
        """,
        call_id,
    )
    return _normalize_payload(row)


async def apply_result(conn, call_id: str, output: dict[str, Any]) -> dict[str, Any]:
    try:
        raw = await conn.fetchval(
            "SELECT apply_external_call_result($1::uuid, $2::jsonb)",
            call_id,
            json.dumps(output),
        )
    except Exception:
        await conn.execute(
            """
            UPDATE external_calls
            SET status = 'complete'::external_call_status, output = $1, completed_at = CURRENT_TIMESTAMP
            WHERE id = $2
            """,
            json.dumps(output),
            call_id,
        )
        return output

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    return raw if isinstance(raw, dict) else output


async def get_call_status(conn, call_id: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        "SELECT status, output, error_message FROM external_calls WHERE id = $1::uuid",
        call_id,
    )
    return dict(row) if row else None


async def fail_call(
    conn,
    call_id: str,
    error: str,
    *,
    max_retries: int,
    retry: bool = True,
) -> None:
    if retry:
        await conn.execute(
            """
            UPDATE external_calls
            SET status = CASE
                    WHEN retry_count < $1 THEN 'pending'::external_call_status
                    ELSE 'failed'::external_call_status
                END,
                error_message = $2,
                retry_count = retry_count + 1,
                started_at = NULL
            WHERE id = $3
            """,
            max_retries,
            error,
            call_id,
        )
    else:
        await conn.execute(
            """
            UPDATE external_calls
            SET status = 'failed'::external_call_status, error_message = $1, completed_at = CURRENT_TIMESTAMP
            WHERE id = $2
            """,
            error,
            call_id,
        )
