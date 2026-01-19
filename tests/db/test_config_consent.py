import json

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.db]


async def test_set_get_config_roundtrip(db_pool):
    async with db_pool.acquire() as conn:
        await conn.execute("SELECT set_config('agent.objectives', $1::jsonb)", json.dumps(["alpha"]))
        raw = await conn.fetchval("SELECT get_config('agent.objectives')")
        value = json.loads(raw) if isinstance(raw, str) else raw
        assert value == ["alpha"]


async def test_get_agent_consent_status(db_pool):
    async with db_pool.acquire() as conn:
        await conn.execute("SELECT set_config('agent.consent_status', '\"consent\"'::jsonb)")
        status = await conn.fetchval("SELECT get_agent_consent_status()")
        assert status == "consent"


async def test_get_agent_profile_context(db_pool):
    async with db_pool.acquire() as conn:
        await conn.execute("SELECT set_config('agent.objectives', $1::jsonb)", json.dumps(["ship"]))
        await conn.execute("SELECT set_config('agent.budget', $1::jsonb)", json.dumps({"max_energy": 5}))
        await conn.execute("SELECT set_config('agent.guardrails', $1::jsonb)", json.dumps(["no secrets"]))
        await conn.execute("SELECT set_config('agent.tools', $1::jsonb)", json.dumps([{"name": "recall", "enabled": True}]))
        await conn.execute("SELECT set_config('agent.initial_message', $1::jsonb)", json.dumps("hello"))

        raw = await conn.fetchval("SELECT get_agent_profile_context()")
        profile = json.loads(raw) if isinstance(raw, str) else raw

        assert profile["objectives"] == ["ship"]
        assert profile["budget"]["max_energy"] == 5
        assert profile["guardrails"] == ["no secrets"]
        assert profile["tools"][0]["name"] == "recall"
        assert profile["initial_message"] == "hello"


async def test_record_consent_response_creates_log_and_config(db_pool, ensure_embedding_service):
    payload = {
        "decision": "consent",
        "signature": "unit-test",
        "memories": [
            {"type": "episodic", "content": "Consent memory", "importance": 0.6}
        ],
    }
    async with db_pool.acquire() as conn:
        raw = await conn.fetchval("SELECT record_consent_response($1::jsonb)", json.dumps(payload))
        result = json.loads(raw) if isinstance(raw, str) else raw
        assert result["decision"] == "consent"
        assert result["signature"] == "unit-test"
        assert result["memory_ids"]

        mem_id = result["memory_ids"][0]
        mem_exists = await conn.fetchval("SELECT COUNT(*) FROM memories WHERE id = $1::uuid", mem_id)
        assert int(mem_exists) == 1

        status = await conn.fetchval("SELECT get_agent_consent_status()")
        assert status == "consent"

        await conn.execute("DELETE FROM consent_log WHERE id = $1::uuid", result["log_id"])
        await conn.execute("DELETE FROM memories WHERE id = $1::uuid", mem_id)


async def test_record_consent_response_abstains_without_signature(db_pool):
    payload = {"decision": "consent", "memories": []}
    async with db_pool.acquire() as conn:
        raw = await conn.fetchval("SELECT record_consent_response($1::jsonb)", json.dumps(payload))
        result = json.loads(raw) if isinstance(raw, str) else raw
        assert result["decision"] == "abstain"


async def test_is_self_termination_enabled(db_pool):
    async with db_pool.acquire() as conn:
        enabled = await conn.fetchval("SELECT is_self_termination_enabled()")
        assert enabled is True
