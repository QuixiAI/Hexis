from __future__ import annotations

from typing import Any

from services.external_calls import ExternalCallProcessor
from services.heartbeat_runner import execute_heartbeat_decision


async def process_pending_call(
    conn,
    *,
    call_processor: ExternalCallProcessor,
) -> dict[str, Any] | None:
    call = await call_processor.claim_pending_call(conn)
    if not call:
        return None

    call_id = str(call["id"])
    call_type = call["call_type"]
    call_input = call.get("input") or {}
    heartbeat_id = call.get("heartbeat_id")

    try:
        result = await call_processor.process_call_payload(conn, call_type, call_input)
    except Exception as exc:
        await call_processor.fail_call(conn, call_id, str(exc))
        return {"call_id": call_id, "call_type": call_type, "error": str(exc)}
    if (
        heartbeat_id
        and isinstance(result, dict)
        and result.get("kind") == "heartbeat_decision"
        and "decision" in result
    ):
        await call_processor.apply_result(conn, call_id, result)
        try:
            exec_result = await execute_heartbeat_decision(
                conn,
                heartbeat_id=str(heartbeat_id),
                decision=result["decision"],
                call_processor=call_processor,
            )
        except Exception as exc:
            return {
                "call_id": call_id,
                "call_type": call_type,
                "result": result,
                "execution": {"error": str(exc)},
            }
        return {
            "call_id": call_id,
            "call_type": call_type,
            "result": result,
            "execution": exec_result,
        }

    await call_processor.apply_result(conn, call_id, result)
    return {
        "call_id": call_id,
        "call_type": call_type,
        "result": result,
    }
