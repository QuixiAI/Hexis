"""load_llm_config fallback semantics: JSON-null and empty config rows mean
"no override configured" and must fall back to fallback_key — not silently
default the provider (which sent openai-codex users to a keyless OpenAI
client; see the recmem 'Missing credentials' failure).
"""
from __future__ import annotations

import json

import pytest

from core.llm_config import load_llm_config

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


async def test_json_null_override_falls_back(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(
                """
                INSERT INTO config (key, value, description) VALUES
                    ('llm.test_null_role', 'null'::jsonb, 'test'),
                    ('llm.test_fallback_role', $1::jsonb, 'test')
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """,
                json.dumps({"provider": "openai_compatible", "model": "test-model"}),
            )
            cfg = await load_llm_config(
                conn, "llm.test_null_role", fallback_key="llm.test_fallback_role"
            )
            assert cfg["provider"] == "openai_compatible"
            assert cfg["model"] == "test-model"
        finally:
            await tr.rollback()


async def test_empty_object_override_falls_back(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(
                """
                INSERT INTO config (key, value, description) VALUES
                    ('llm.test_empty_role', '{}'::jsonb, 'test'),
                    ('llm.test_fallback_role2', $1::jsonb, 'test')
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """,
                json.dumps({"provider": "openai_compatible", "model": "test-model-2"}),
            )
            cfg = await load_llm_config(
                conn, "llm.test_empty_role", fallback_key="llm.test_fallback_role2"
            )
            assert cfg["model"] == "test-model-2"
        finally:
            await tr.rollback()


async def test_real_override_wins_over_fallback(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(
                """
                INSERT INTO config (key, value, description) VALUES
                    ('llm.test_set_role', $1::jsonb, 'test'),
                    ('llm.test_fallback_role3', $2::jsonb, 'test')
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """,
                json.dumps({"provider": "openai_compatible", "model": "override-model"}),
                json.dumps({"provider": "openai_compatible", "model": "fallback-model"}),
            )
            cfg = await load_llm_config(
                conn, "llm.test_set_role", fallback_key="llm.test_fallback_role3"
            )
            assert cfg["model"] == "override-model"
        finally:
            await tr.rollback()


async def test_global_inference_fallbacks_survive_primary_credential_failure(db_pool, monkeypatch):
    from unittest.mock import AsyncMock
    from core import llm_config
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            routes = [{"provider":"openai_compatible", "model":"reserve", "endpoint":"http://localhost:4000/v1"}]
            await conn.execute("INSERT INTO config(key,value) VALUES ('llm.fallbacks',$1::jsonb) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value", json.dumps(routes))
            monkeypatch.setattr(llm_config, "apply_provider_credentials", AsyncMock(side_effect=RuntimeError("expired")))
            cfg = await load_llm_config(conn, "llm.test_primary")
            assert cfg["fallbacks"] == routes
