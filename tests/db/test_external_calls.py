import json

import pytest

from tests.utils import get_test_identifier, _coerce_json, timed_db_call

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.db]


async def test_apply_external_call_result_applies_side_effects(db_pool, ensure_embedding_service):
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE heartbeat_state SET current_energy = 20, is_paused = FALSE WHERE id = 1")
        hb_payload = _coerce_json(
            await timed_db_call(
                "start_heartbeat",
                conn.fetchval("SELECT start_heartbeat()"),
                conn=conn,
            )
        )
        hb_id = hb_payload.get("heartbeat_id")
        assert hb_id is not None

        brainstorm_raw = await timed_db_call(
            "execute_heartbeat_action_brainstorm_goals",
            conn.fetchval(
                "SELECT execute_heartbeat_action($1::uuid, 'brainstorm_goals', '{}'::jsonb)",
                hb_id,
            ),
            conn=conn,
            track_embeddings=True,
        )
        brainstorm_result = _coerce_json(brainstorm_raw)
        brainstorm_call = (brainstorm_result.get("external_calls") or [{}])[0]
        assert brainstorm_call.get("call_type") == "think"

        test_id = get_test_identifier("apply_external_call")
        brainstorm_output = {
            "kind": "brainstorm_goals",
            "goals": [
                {"title": f"Goal A {test_id}", "description": "A", "priority": "queued", "source": "curiosity"},
                {"title": f"Goal B {test_id}", "description": "B", "priority": "queued", "source": "curiosity"},
            ],
        }

        await timed_db_call(
            "apply_external_call_result_brainstorm_goals",
            conn.fetchval(
                "SELECT apply_external_call_result($1::jsonb, $2::jsonb)",
                json.dumps(brainstorm_call),
                json.dumps(brainstorm_output),
            ),
            conn=conn,
            track_embeddings=True,
        )

        goal_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM memories
            WHERE type = 'goal'
              AND metadata->>'title' IN ($1, $2)
            """,
            f"Goal A {test_id}",
            f"Goal B {test_id}",
        )
        assert goal_count == 2

        inquire_raw = await timed_db_call(
            "execute_heartbeat_action_inquire_shallow",
            conn.fetchval(
                "SELECT execute_heartbeat_action($1::uuid, 'inquire_shallow', $2::jsonb)",
                hb_id,
                json.dumps({"query": f"What is an embedding? {test_id}"}),
            ),
            conn=conn,
            track_embeddings=True,
        )
        inquire_result = _coerce_json(inquire_raw)
        inquire_call = (inquire_result.get("external_calls") or [{}])[0]
        assert inquire_call.get("call_type") == "think"

        inquiry_summary = f"Embeddings are vectors ({test_id})."
        inquire_output = {
            "kind": "inquire",
            "summary": inquiry_summary,
            "confidence": 0.8,
            "sources": [],
            "depth": "inquire_shallow",
            "query": f"What is an embedding? {test_id}",
        }

        await timed_db_call(
            "apply_external_call_result_inquire_shallow",
            conn.fetchval(
                "SELECT apply_external_call_result($1::jsonb, $2::jsonb)",
                json.dumps(inquire_call),
                json.dumps(inquire_output),
            ),
            conn=conn,
            track_embeddings=True,
        )

        inquiry_count = await conn.fetchval(
            "SELECT COUNT(*) FROM memories WHERE type = 'semantic' AND content = $1",
            inquiry_summary,
        )
        assert inquiry_count == 1
