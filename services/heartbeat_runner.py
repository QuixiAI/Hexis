from __future__ import annotations

from typing import Any

from core.state import apply_heartbeat_decision
from services.external_calls import ExternalCallProcessor


def _coerce_list(val: Any) -> list[Any]:
    if isinstance(val, list):
        return val
    return []


def _termination_applied(payload: dict[str, Any]) -> bool:
    termination = payload.get("termination")
    if isinstance(termination, dict) and termination.get("terminated") is True:
        return True
    return payload.get("terminated") is True


async def execute_heartbeat_decision(
    conn,
    *,
    heartbeat_id: str,
    decision: dict[str, Any],
    call_processor: ExternalCallProcessor,
) -> dict[str, Any]:
    start_index = 0
    outbox_messages: list[Any] = []
    while True:
        batch = await apply_heartbeat_decision(
            conn,
            heartbeat_id=heartbeat_id,
            decision=decision,
            start_index=start_index,
        )

        outbox_messages.extend(_coerce_list(batch.get("outbox_messages")))

        if batch.get("terminated") is True:
            return {"terminated": True, "halt_reason": "terminated", "outbox_messages": outbox_messages}

        pending_call = batch.get("pending_external_call")
        if isinstance(pending_call, dict) and pending_call.get("call_type"):
            try:
                call_type = str(pending_call.get("call_type") or "")
                call_input = pending_call.get("input") or {}
                if isinstance(call_input, str):
                    call_input = {}
                external_result = await call_processor.process_call_payload(conn, call_type, call_input)
                applied = await call_processor.apply_result(conn, pending_call, external_result)
            except Exception as exc:
                applied = {"error": str(exc)}

            if isinstance(applied, dict):
                outbox_messages.extend(_coerce_list(applied.get("outbox_messages")))
                if _termination_applied(applied):
                    return {"terminated": True, "halt_reason": "terminated", "outbox_messages": outbox_messages}

            next_index = batch.get("next_index")
            if isinstance(next_index, int):
                start_index = next_index
            else:
                start_index = 0
            continue

        if batch.get("completed") is True:
            return {
                "completed": True,
                "memory_id": batch.get("memory_id"),
                "halt_reason": batch.get("halt_reason"),
                "outbox_messages": outbox_messages,
            }

        return {
            "completed": False,
            "halt_reason": batch.get("halt_reason") or "unknown",
            "outbox_messages": outbox_messages,
        }
