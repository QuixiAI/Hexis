from __future__ import annotations

from typing import Any

from core.state import apply_heartbeat_decision
from services.external_calls import ExternalCallProcessor


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
    while True:
        batch = await apply_heartbeat_decision(
            conn,
            heartbeat_id=heartbeat_id,
            decision=decision,
            start_index=start_index,
        )

        if batch.get("terminated") is True:
            return {"terminated": True, "halt_reason": "terminated"}

        pending_call_id = batch.get("pending_external_call_id")
        if pending_call_id:
            try:
                external_result = await call_processor.process_call_by_id(conn, str(pending_call_id))
            except Exception as exc:
                external_result = {"error": str(exc)}

            if isinstance(external_result, dict) and _termination_applied(external_result):
                return {"terminated": True, "halt_reason": "terminated"}

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
            }

        return {
            "completed": False,
            "halt_reason": batch.get("halt_reason") or "unknown",
        }
