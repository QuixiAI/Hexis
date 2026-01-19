from __future__ import annotations

import json
from typing import Any

from services.heartbeat_prompt import build_heartbeat_decision_prompt
from core.llm_config import load_llm_config
from core.llm_json import chat_json
from core import external_calls as external_calls_store
from services.prompt_resources import (
    compose_personhood_prompt,
    load_heartbeat_prompt,
    load_termination_confirm_prompt,
)


class ExternalCallProcessor:
    def __init__(self, *, max_retries: int = 3):
        self.max_retries = max_retries

    async def claim_pending_call(self, conn) -> dict[str, Any] | None:
        return await external_calls_store.claim_pending_call(conn)

    async def claim_call_by_id(self, conn, call_id: str) -> dict[str, Any] | None:
        return await external_calls_store.claim_call_by_id(conn, call_id)

    async def apply_result(self, conn, call_id: str, output: dict[str, Any]) -> dict[str, Any]:
        return await external_calls_store.apply_result(conn, call_id, output)

    async def fail_call(self, conn, call_id: str, error: str, *, retry: bool = True) -> None:
        await external_calls_store.fail_call(
            conn,
            call_id,
            error,
            max_retries=self.max_retries,
            retry=retry,
        )

    async def process_call_payload(self, conn, call_type: str, call_input: dict[str, Any]) -> dict[str, Any]:
        if call_type == "think":
            return await self._process_think_call(conn, call_input)
        if call_type == "embed":
            raise RuntimeError("external_calls type 'embed' is unsupported; use get_embedding() inside Postgres")
        return {"error": f"Unsupported call_type: {call_type}"}

    async def process_call_by_id(self, conn, call_id: str) -> dict[str, Any]:
        row = await self.claim_call_by_id(conn, call_id)
        if not row:
            cur = await external_calls_store.get_call_status(conn, call_id)
            return cur if cur else {"error": "call not found"}

        call_type = row["call_type"]
        call_input = row.get("input") or {}
        if isinstance(call_input, str):
            try:
                call_input = json.loads(call_input)
            except Exception:
                call_input = {}

        result = await self.process_call_payload(conn, call_type, call_input)
        return await self.apply_result(conn, call_id, result)

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
        return {"error": f"Unknown think kind: {kind!r}"}

    async def _process_heartbeat_decision_call(self, conn, call_input: dict[str, Any]) -> dict[str, Any]:
        context = call_input.get("context", {})
        heartbeat_id = call_input.get("heartbeat_id")
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
            max_tokens=2048,
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
