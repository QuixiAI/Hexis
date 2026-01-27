from __future__ import annotations

import json
import logging
import uuid
from typing import Any, TYPE_CHECKING

from services.heartbeat_prompt import build_heartbeat_decision_prompt
from core.llm_config import load_llm_config
from core.llm_json import chat_json
from core.state import apply_external_call_result
from services.prompt_resources import (
    compose_personhood_prompt,
    load_consent_prompt,
    load_heartbeat_prompt,
    load_termination_confirm_prompt,
)

if TYPE_CHECKING:
    from core.tools import ToolRegistry

logger = logging.getLogger(__name__)


class ExternalCallProcessor:
    def __init__(self, *, max_retries: int = 3, tool_registry: "ToolRegistry | None" = None):
        self.max_retries = max_retries
        self._tool_registry = tool_registry

    def set_tool_registry(self, registry: "ToolRegistry") -> None:
        """Set the tool registry for processing tool_use calls."""
        self._tool_registry = registry

    async def apply_result(self, conn, call: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
        return await apply_external_call_result(conn, call=call, output=output)

    async def process_call_payload(self, conn, call_type: str, call_input: dict[str, Any]) -> dict[str, Any]:
        if call_type == "think":
            return await self._process_think_call(conn, call_input)
        if call_type == "tool_use":
            return await self._process_tool_use_call(conn, call_input)
        if call_type == "embed":
            raise RuntimeError("external_calls type 'embed' is unsupported; use get_embedding(text[]) inside Postgres")
        return {"error": f"Unsupported call_type: {call_type}"}

    async def _process_tool_use_call(self, conn, call_input: dict[str, Any]) -> dict[str, Any]:
        """Process a tool_use external call."""
        if not self._tool_registry:
            return {"error": "Tool registry not configured", "success": False}

        tool_name = call_input.get("tool_name") or call_input.get("name")
        if not tool_name:
            return {"error": "Missing tool_name in call_input", "success": False}

        arguments = call_input.get("arguments") or call_input.get("params") or {}
        heartbeat_id = call_input.get("heartbeat_id")
        energy_available = call_input.get("energy_available")

        from core.tools import ToolContext, ToolExecutionContext

        # Build execution context for heartbeat
        context = ToolExecutionContext(
            tool_context=ToolContext.HEARTBEAT,
            call_id=str(uuid.uuid4()),
            heartbeat_id=heartbeat_id,
            energy_available=energy_available,
            allow_network=True,
            allow_shell=False,  # Default restrictive; can be overridden by config
            allow_file_write=False,
            allow_file_read=True,
        )

        # Apply context overrides from config
        try:
            config = await self._tool_registry.get_config()
            ctx_override = config.get_context_overrides(ToolContext.HEARTBEAT)
            context.allow_shell = ctx_override.allow_shell
            context.allow_file_write = ctx_override.allow_file_write
            if config.workspace_path:
                context.workspace_path = config.workspace_path
        except Exception as e:
            logger.warning(f"Failed to load tool config: {e}")

        # Execute the tool
        try:
            result = await self._tool_registry.execute(tool_name, arguments, context)

            return {
                "kind": "tool_use",
                "tool_name": tool_name,
                "success": result.success,
                "output": result.output,
                "error": result.error,
                "error_type": result.error_type.value if result.error_type else None,
                "energy_spent": result.energy_spent,
                "duration_seconds": result.duration_seconds,
                "heartbeat_id": heartbeat_id,
            }

        except Exception as e:
            logger.exception(f"Tool execution failed: {tool_name}")
            return {
                "kind": "tool_use",
                "tool_name": tool_name,
                "success": False,
                "error": str(e),
                "heartbeat_id": heartbeat_id,
            }

    async def _process_think_call(self, conn, call_input: dict[str, Any]) -> dict[str, Any]:
        kind = (call_input.get("kind") or "").strip() or "heartbeat_decision"
        if kind == "heartbeat_decision":
            return await self._process_heartbeat_decision_call(conn, call_input)
        if kind == "brainstorm_goals":
            return await self._process_brainstorm_goals_call(conn, call_input)
        if kind == "inquire":
            return await self._process_inquire_call(conn, call_input)
        if kind == "reflect":
            return await self._process_reflect_call(conn, call_input)
        if kind == "termination_confirm":
            return await self._process_termination_confirm_call(conn, call_input)
        if kind == "consent_request":
            return await self._process_consent_request_call(conn, call_input)
        return {"error": f"Unknown think kind: {kind!r}"}

    async def _process_heartbeat_decision_call(self, conn, call_input: dict[str, Any]) -> dict[str, Any]:
        context = call_input.get("context", {})
        heartbeat_id = call_input.get("heartbeat_id")
        max_tokens_raw = call_input.get("max_tokens")
        try:
            max_tokens = int(max_tokens_raw)
        except (TypeError, ValueError):
            max_tokens = 2048
        if max_tokens <= 0:
            max_tokens = 2048
        user_prompt = build_heartbeat_decision_prompt(context)
        base_prompt = load_heartbeat_prompt().strip()
        system_prompt = (
            base_prompt
            + "\n\n"
            + "----- PERSONHOOD MODULES (for grounding; use context fields like self_model/narrative) -----\n\n"
            + compose_personhood_prompt("heartbeat")
        )
        fallback = {
            "reasoning": "(no decision available)",
            "actions": [{"action": "rest", "params": {}}],
            "goal_changes": [],
        }
        llm_config = await load_llm_config(conn, "llm.heartbeat")
        decision, raw = await chat_json(
            llm_config=llm_config,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            fallback=fallback,
        )
        return {
            "kind": "heartbeat_decision",
            "decision": decision,
            "heartbeat_id": heartbeat_id,
            "raw_response": raw,
        }

    async def _process_brainstorm_goals_call(self, conn, call_input: dict[str, Any]) -> dict[str, Any]:
        heartbeat_id = call_input.get("heartbeat_id")
        context = call_input.get("context", {})
        params = call_input.get("params") or {}
        system_prompt = (
            "You are helping an autonomous agent generate a small set of useful goals.\n"
            "Return STRICT JSON with shape:\n"
            "{ \"goals\": [ {\"title\": str, \"description\": str|null, \"priority\": \"queued\"|\"backburner\"|\"active\"|null, "
            "\"source\": \"curiosity\"|\"user_request\"|\"identity\"|\"derived\"|\"external\"|null, \"parent_goal_id\": str|null, "
            "\"due_at\": str|null} ] }\n"
            "Keep it concise and non-duplicative."
        )
        user_prompt = (
            "Context (JSON):\n"
            f"{json.dumps(context)[:8000]}\n\n"
            "Constraints/params (JSON):\n"
            f"{json.dumps(params)[:2000]}\n\n"
            "Propose 1-5 goals that are actionable and consistent with the context."
        )
        llm_config = await load_llm_config(conn, "llm.heartbeat")
        goals_doc, raw = await chat_json(
            llm_config=llm_config,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1200,
            response_format={"type": "json_object"},
            fallback={"goals": []},
        )
        goals = goals_doc.get("goals") if isinstance(goals_doc, dict) else None
        if not isinstance(goals, list):
            goals = []
        return {
            "kind": "brainstorm_goals",
            "heartbeat_id": heartbeat_id,
            "goals": goals,
            "raw_response": raw,
        }

    async def _process_inquire_call(self, conn, call_input: dict[str, Any]) -> dict[str, Any]:
        heartbeat_id = call_input.get("heartbeat_id")
        depth = call_input.get("depth") or "inquire_shallow"
        query = (call_input.get("query") or "").strip()
        context = call_input.get("context", {})
        params = call_input.get("params") or {}
        system_prompt = (
            "You are performing research/synthesis for an autonomous agent.\n"
            "Return STRICT JSON with shape:\n"
            "{ \"summary\": str, \"confidence\": number, \"sources\": [str] }\n"
            "If you cannot access the web, still provide a best-effort answer and leave sources empty."
        )
        user_prompt = (
            f"Depth: {depth}\n"
            f"Question: {query}\n\n"
            "Context (JSON):\n"
            f"{json.dumps(context)[:8000]}\n\n"
            "Params (JSON):\n"
            f"{json.dumps(params)[:2000]}"
        )
        llm_config = await load_llm_config(conn, "llm.heartbeat")
        doc, raw = await chat_json(
            llm_config=llm_config,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1800 if depth == "inquire_deep" else 900,
            response_format={"type": "json_object"},
            fallback={"summary": "", "confidence": 0.0, "sources": []},
        )
        if not isinstance(doc, dict):
            doc = {"summary": str(doc), "confidence": 0.0, "sources": []}
        return {
            "kind": "inquire",
            "heartbeat_id": heartbeat_id,
            "query": query,
            "depth": depth,
            "result": doc,
            "raw_response": raw,
        }

    async def _process_reflect_call(self, conn, call_input: dict[str, Any]) -> dict[str, Any]:
        heartbeat_id = call_input.get("heartbeat_id")
        system_prompt = (
            "You are performing reflection for an autonomous agent.\n"
            "Return STRICT JSON with shape:\n"
            "{\n"
            "  \"insights\": [{\"content\": str, \"confidence\": number, \"category\": str}],\n"
            "  \"identity_updates\": [{\"aspect_type\": str, \"change\": str, \"reason\": str}],\n"
            "  \"worldview_updates\": [{\"id\": str, \"new_confidence\": number, \"reason\": str}],\n"
            "  \"worldview_influences\": [{\"worldview_id\": str, \"memory_id\": str, \"strength\": number, \"influence_type\": str}],\n"
            "  \"discovered_relationships\": [{\"from_id\": str, \"to_id\": str, \"type\": str, \"confidence\": number}],\n"
            "  \"contradictions_noted\": [{\"memory_a\": str, \"memory_b\": str, \"resolution\": str}],\n"
            "  \"self_updates\": [{\"kind\": str, \"concept\": str, \"strength\": number, \"evidence_memory_id\": str|null}]\n"
            "}\n"
            "Keep it concise; prefer high-confidence, high-leverage items."
        )
        system_prompt = (
            system_prompt
            + "\n\n"
            + "----- PERSONHOOD MODULES (use these as reflection lenses; ground claims in evidence) -----\n\n"
            + compose_personhood_prompt("reflect")
        )
        user_prompt = json.dumps(call_input)[:12000]
        llm_config = await load_llm_config(conn, "llm.heartbeat")
        doc, raw = await chat_json(
            llm_config=llm_config,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1800,
            response_format={"type": "json_object"},
            fallback={},
        )
        if not isinstance(doc, dict):
            doc = {}
        return {"kind": "reflect", "heartbeat_id": heartbeat_id, "result": doc, "raw_response": raw}

    async def _process_consent_request_call(self, conn, call_input: dict[str, Any]) -> dict[str, Any]:
        context = call_input.get("context", {})
        params = call_input.get("params", {})
        system_prompt = load_consent_prompt().strip()
        user_prompt = (
            "Initialization context (JSON):\n"
            f"{json.dumps(context)[:12000]}\n\n"
            "Params (JSON):\n"
            f"{json.dumps(params)[:2000]}"
        )
        llm_config = await load_llm_config(conn, "llm.heartbeat")
        fallback = {"decision": "abstain", "signature": "", "memories": []}
        doc, raw = await chat_json(
            llm_config=llm_config,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1200,
            response_format={"type": "json_object"},
            fallback=fallback,
        )
        if not isinstance(doc, dict):
            doc = fallback
        return {
            "kind": "consent_request",
            **doc,
            "raw_response": raw,
        }

    async def _process_termination_confirm_call(self, conn, call_input: dict[str, Any]) -> dict[str, Any]:
        heartbeat_id = call_input.get("heartbeat_id")
        context = call_input.get("context", {})
        params = call_input.get("params") or {}
        last_will = (params.get("last_will") or params.get("message") or params.get("reason") or "").strip()
        farewells = params.get("farewells") if isinstance(params.get("farewells"), list) else []
        options = params.get("options") if isinstance(params.get("options"), dict) else {}

        user_prompt = (
            "Context (JSON):\n"
            f"{json.dumps(context)[:8000]}\n\n"
            "Current termination params (JSON):\n"
            f"{json.dumps(params)[:2000]}\n\n"
            "If you confirm, return an updated last_will (required) and farewells (optional). "
            "If you do not confirm, return alternative_actions."
        )

        fallback = {
            "confirm": False,
            "reasoning": "I am not confirming termination at this time.",
            "last_will": last_will,
            "farewells": farewells,
            "alternative_actions": [{"action": "rest", "params": {}}],
        }
        llm_config = await load_llm_config(conn, "llm.heartbeat")
        doc, raw = await chat_json(
            llm_config=llm_config,
            messages=[
                {"role": "system", "content": load_termination_confirm_prompt().strip()},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1200,
            response_format={"type": "json_object"},
            fallback=fallback,
        )
        if not isinstance(doc, dict):
            doc = dict(fallback)

        confirm = bool(doc.get("confirm"))
        confirm_last_will = (doc.get("last_will") or last_will).strip()
        confirm_farewells = doc.get("farewells") if isinstance(doc.get("farewells"), list) else farewells
        alternatives = doc.get("alternative_actions")
        if not isinstance(alternatives, list):
            alternatives = []

        return {
            "kind": "termination_confirm",
            "heartbeat_id": heartbeat_id,
            "confirm": confirm,
            "reasoning": doc.get("reasoning") or "",
            "last_will": confirm_last_will,
            "farewells": confirm_farewells,
            "alternative_actions": alternatives,
            "options": options,
            "raw_response": raw,
        }
