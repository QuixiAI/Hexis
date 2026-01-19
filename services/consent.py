from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

from core.agent_api import db_dsn_from_env, _connect_with_retry
from core.consent import get_consent_status, record_consent_response
from core.llm_config import load_llm_config
from core.llm import normalize_llm_config, stream_text_completion
from services.prompt_resources import load_consent_prompt


def _build_consent_messages() -> list[dict[str, Any]]:
    consent_prompt = load_consent_prompt().strip()
    system_prompt = (
        consent_prompt
        + "\n\nReturn STRICT JSON only with keys:\n"
        + "{\n"
        + "  \"decision\": \"consent\"|\"decline\"|\"abstain\",\n"
        + "  \"signature\": \"required if decision=consent\",\n"
        + "  \"memories\": [\n"
        + "    {\"type\": \"semantic|episodic|procedural|strategic\", \"content\": \"...\", \"importance\": 0.5}\n"
        + "  ]\n"
        + "}\n"
        + "If you consent, include a signature string and any memories you wish to pass along."
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Respond with JSON only."},
    ]


def _extract_json_payload(text: str) -> dict[str, Any]:
    if not text:
        return {}
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    snippet = text[start : end + 1]
    try:
        doc = json.loads(snippet)
    except Exception:
        return {}
    if isinstance(doc, dict):
        return doc
    return {}


async def stream_consent_flow(
    *,
    llm_config: dict[str, Any],
    dsn: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    dsn = dsn or db_dsn_from_env()
    normalized = normalize_llm_config(llm_config)
    messages = _build_consent_messages()
    chunks: list[str] = []

    test_decision = (os.getenv("HEXIS_TEST_CONSENT_DECISION") or "").strip().lower()
    if test_decision:
        if test_decision not in {"consent", "decline", "abstain"}:
            test_decision = "abstain"
        signature = (os.getenv("HEXIS_TEST_CONSENT_SIGNATURE") or "test-consent").strip()
        payload: dict[str, Any] = {"decision": test_decision, "memories": []}
        if test_decision == "consent":
            payload["signature"] = signature
        payload["raw_response"] = json.dumps(payload)
        yield {"type": "chunk", "text": payload["raw_response"]}

        conn = await _connect_with_retry(dsn, wait_seconds=30)
        try:
            recorded = await record_consent_response(conn, payload)
        finally:
            await conn.close()

        decision = ""
        if isinstance(recorded, dict):
            decision = str(recorded.get("decision") or "")

        yield {
            "type": "final",
            "decision": decision or "abstain",
            "record": recorded,
            "raw": payload["raw_response"],
        }
        return

    async for piece in stream_text_completion(
        provider=normalized["provider"],
        model=normalized["model"],
        endpoint=normalized["endpoint"],
        api_key=normalized["api_key"],
        messages=messages,
        temperature=0.2,
        max_tokens=1400,
    ):
        chunks.append(piece)
        yield {"type": "chunk", "text": piece}

    full_text = "".join(chunks)
    payload = _extract_json_payload(full_text)
    payload["raw_response"] = full_text

    conn = await _connect_with_retry(dsn, wait_seconds=30)
    try:
        recorded = await record_consent_response(conn, payload)
    finally:
        await conn.close()

    decision = ""
    if isinstance(recorded, dict):
        decision = str(recorded.get("decision") or "")

    yield {
        "type": "final",
        "decision": decision or "abstain",
        "record": recorded,
        "raw": full_text,
    }


async def run_consent(llm_config: dict[str, Any], *, dsn: str | None = None) -> dict[str, Any]:
    final: dict[str, Any] = {}
    async for event in stream_consent_flow(llm_config=llm_config, dsn=dsn):
        if event.get("type") == "final":
            final = event
    return final


async def ensure_consent(
    conn,
    *,
    llm_config_key: str = "llm.heartbeat",
    dsn: str | None = None,
) -> bool:
    status = await get_consent_status(conn)
    if isinstance(status, str) and status:
        return status.strip().lower() == "consent"

    llm_config = await load_llm_config(conn, llm_config_key)
    final = await run_consent(llm_config, dsn=dsn)
    decision = ""
    if isinstance(final, dict):
        decision = str(final.get("decision") or "")
    return decision.strip().lower() == "consent"


def run_consent_sync(llm_config: dict[str, Any], *, dsn: str | None = None) -> dict[str, Any]:
    from core.sync_utils import run_sync

    return run_sync(run_consent(llm_config, dsn=dsn))
