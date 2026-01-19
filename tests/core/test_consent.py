import json

import pytest

from services import consent as consent_mod
from tests.utils import _db_dsn

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.core]


async def test_extract_json_payload_handles_noise():
    payload = {"decision": "consent", "signature": "sig", "memories": []}
    text = f"noise\n{json.dumps(payload)}\ntrailing"
    extracted = consent_mod._extract_json_payload(text)  # noqa: SLF001
    assert extracted["decision"] == "consent"
    assert extracted["signature"] == "sig"


async def test_stream_consent_flow_records_log(monkeypatch, db_pool):
    async def fake_stream_text_completion(**_kwargs):
        yield '{"decision":"consent","signature":"unit-test","memories":[]}'

    monkeypatch.setattr(consent_mod, "stream_text_completion", fake_stream_text_completion)

    events = []
    async for event in consent_mod.stream_consent_flow(
        llm_config={"provider": "openai", "model": "gpt-4o"},
        dsn=_db_dsn(),
    ):
        events.append(event)

    final = events[-1]
    assert final["type"] == "final"
    assert final["decision"] == "consent"

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT decision, signature FROM consent_log WHERE signature = $1 ORDER BY decided_at DESC LIMIT 1",
            "unit-test",
        )
    assert row is not None
    assert row["decision"] == "consent"


async def test_stream_consent_flow_abstains_without_signature(monkeypatch, db_pool):
    async def fake_stream_text_completion(**_kwargs):
        yield '{"decision":"consent","memories":[]}'

    monkeypatch.setattr(consent_mod, "stream_text_completion", fake_stream_text_completion)

    final = await consent_mod.run_consent(
        {"provider": "openai", "model": "gpt-4o"},
        dsn=_db_dsn(),
    )
    assert final["decision"] == "abstain"

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT decision FROM consent_log ORDER BY decided_at DESC LIMIT 1"
        )
    assert row is not None
    assert row["decision"] == "abstain"
