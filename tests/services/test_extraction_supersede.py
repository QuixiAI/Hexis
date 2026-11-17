"""A fact that replaces an older one marks the older memory as no longer current."""
from __future__ import annotations

import json

import pytest

from services.extraction import _format_unit, _supersede_updated_facts

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


class _Conn:
    def __init__(self, routed: dict, updated: bool = True):
        self.routed = routed
        self.updated = updated
        self.updates: list[tuple] = []
        self.embedded: list[str] = []

    async def fetchval(self, sql, *args):
        if "ingest_theta_related" in sql:
            return 0.8
        if "ingest_route_embedding" in sql:
            self.embedded.append(args[0])
            return json.dumps(self.routed)
        if sql.lstrip().startswith("UPDATE memories"):
            self.updates.append(args)
            return args[0] if self.updated else None
        raise AssertionError(sql)


async def test_a_move_expires_the_old_location():
    conn = _Conn({"decision": "related", "matched_memory_id": "m-osaka", "similarity": 0.86})
    facts = [
        {"content": "Eric works from the Tokyo office", "updates": "Eric works from the Osaka office"},
        {"content": "Eric is vegetarian"},  # an addition replaces nothing
    ]
    assert await _supersede_updated_facts(conn, facts, "t0") == 1
    assert conn.embedded == ["Eric works from the Osaka office"]
    target, by, started = conn.updates[0]
    assert (target, by, started) == ("m-osaka", "Eric works from the Tokyo office", "t0")


async def test_a_loose_match_replaces_nothing():
    conn = _Conn({"decision": "create", "matched_memory_id": None, "similarity": 0.41})
    facts = [{"content": "Eric works from the Tokyo office", "updates": "Eric works from the Osaka office"}]
    assert await _supersede_updated_facts(conn, facts, "t0") == 0
    assert conn.updates == []


def test_the_turn_before_is_context_only():
    row = {"id": "u2", "metadata": "{}", "source_identity": "Eric", "turn_at": "2026-10-16T10:00:00Z",
           "content": "Their CIO told me off the record they are also evaluating Fintra."}
    text = _format_unit(row, previous="Eric: Nishi Bank is reviewing vendors this quarter.")
    assert text.index("context only; extract nothing from it") < text.index("content:\n")
    assert "Nishi Bank is reviewing vendors" in text
    assert "earlier in this conversation" not in _format_unit(row)
