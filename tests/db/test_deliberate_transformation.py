import json

import pytest

from tests.utils import get_test_identifier, _coerce_json

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.db]


async def _create_transformable_belief(conn, *, subcategory: str) -> str:
    belief_id = await conn.fetchval(
        """
        SELECT create_worldview_memory(
            $1,
            'self',
            0.9,
            0.99,
            0.8,
            'user_initialized',
            NULL,
            NULL,
            NULL,
            0.1
        )
        """,
        f"I hold a {subcategory} belief",
    )
    await conn.execute(
        """
        UPDATE memories
        SET metadata = metadata || jsonb_build_object(
            'subcategory', $2::text,
            'change_requires', 'deliberate_transformation',
            'evidence_threshold', 0.5,
            'transformation_state', default_transformation_state(),
            'change_history', '[]'::jsonb
        )
        WHERE id = $1
        """,
        belief_id,
        subcategory,
    )
    return str(belief_id)


async def _create_high_trust_evidence(conn, *, label: str) -> str:
    return str(
        await conn.fetchval(
            """
            SELECT create_semantic_memory(
                $1,
                0.95,
                ARRAY['evidence'],
                NULL,
                $2::jsonb,
                0.95,
                NULL,
                0.95
            )
            """,
            label,
            json.dumps(
                [
                    {"kind": "observation", "trust": 0.95, "ref": "journal"},
                    {"kind": "conversation", "trust": 0.9, "ref": "coach"},
                ]
            ),
        )
    )


async def test_begin_and_record_transformation_effort(db_pool, ensure_embedding_service):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            belief_id = await _create_transformable_belief(conn, subcategory="personality")
            goal_id = await conn.fetchval(
                "SELECT create_goal($1, $2, $3, $4, $5, $6)",
                f"Explore belief {belief_id}",
                "explore belief",
                "curiosity",
                "queued",
                None,
                None,
            )
            result = await conn.fetchval(
                "SELECT begin_belief_exploration($1::uuid, $2::uuid)",
                belief_id,
                goal_id,
            )
            result = _coerce_json(result)
            assert result.get("success") is True

            state = await conn.fetchval(
                "SELECT metadata->'transformation_state' FROM memories WHERE id = $1::uuid",
                belief_id,
            )
            state = _coerce_json(state)
            assert state.get("active_exploration") is True
            assert state.get("exploration_goal_id") == str(goal_id)

            evidence_id = await conn.fetchval(
                "SELECT create_semantic_memory($1, 0.9, ARRAY['evidence'], NULL, NULL, 0.6)",
                f"Evidence {get_test_identifier('evidence')}",
            )
            effort = await conn.fetchval(
                "SELECT record_transformation_effort($1::uuid, $2, $3, $4::uuid)",
                belief_id,
                "contemplate",
                "some notes",
                evidence_id,
            )
            effort = _coerce_json(effort)
            assert effort.get("success") is True
            assert effort.get("new_reflection_count") == 1

            state = await conn.fetchval(
                "SELECT metadata->'transformation_state' FROM memories WHERE id = $1::uuid",
                belief_id,
            )
            state = _coerce_json(state)
            assert str(evidence_id) in state.get("evidence_memories", [])
        finally:
            await tr.rollback()


async def test_attempt_worldview_transformation_success(db_pool, ensure_embedding_service):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(
                "SELECT set_config('transformation.personality', $1::jsonb)",
                json.dumps(
                    {
                        "stability": 0.99,
                        "evidence_threshold": 0.1,
                        "min_reflections": 1,
                        "min_heartbeats": 0,
                    }
                ),
            )
            belief_id = await _create_transformable_belief(conn, subcategory="personality")
            goal_id = await conn.fetchval(
                "SELECT create_goal($1, $2, $3, $4, $5, $6)",
                f"Explore belief {belief_id}",
                "explore belief",
                "curiosity",
                "queued",
                None,
                None,
            )
            await conn.fetchval(
                "SELECT begin_belief_exploration($1::uuid, $2::uuid)",
                belief_id,
                goal_id,
            )

            evidence_id = await conn.fetchval(
                "SELECT create_semantic_memory($1, 0.9, ARRAY['evidence'], NULL, NULL, 0.9)",
                f"Evidence {get_test_identifier('evidence')}",
            )
            await conn.fetchval(
                "SELECT record_transformation_effort($1::uuid, $2, $3, $4::uuid)",
                belief_id,
                "reflect",
                "notes",
                evidence_id,
            )

            result = await conn.fetchval(
                "SELECT attempt_worldview_transformation($1::uuid, $2, $3)",
                belief_id,
                "Updated belief content",
                "shift",
            )
            result = _coerce_json(result)
            assert result.get("success") is True

            row = await conn.fetchrow(
                "SELECT content, metadata->'transformation_state' as state, metadata->'change_history' as history FROM memories WHERE id = $1::uuid",
                belief_id,
            )
            assert row["content"] == "Updated belief content"
            state = _coerce_json(row["state"])
            assert state.get("active_exploration") is False
            history = _coerce_json(row["history"])
            assert isinstance(history, list) and history
        finally:
            await tr.rollback()


async def test_calibrate_neutral_belief(db_pool, ensure_embedding_service):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(
                "SELECT set_config('transformation.personality', $1::jsonb)",
                json.dumps(
                    {
                        "stability": 0.99,
                        "evidence_threshold": 0.1,
                        "min_reflections": 5,
                        "min_heartbeats": 0,
                    }
                ),
            )
            belief_id = await conn.fetchval(
                """
                SELECT create_worldview_memory(
                    $1,
                    'self',
                    0.6,
                    0.9,
                    0.7,
                    'neutral_default',
                    NULL,
                    NULL,
                    NULL,
                    0.0
                )
                """,
                "I am neutral in openness",
            )
            await conn.execute(
                """
                UPDATE memories
                SET metadata = metadata || jsonb_build_object(
                    'subcategory', 'personality',
                    'trait', 'openness',
                    'origin', 'neutral_default',
                    'change_requires', 'deliberate_transformation',
                    'transformation_state', default_transformation_state()
                )
                WHERE id = $1
                """,
                belief_id,
            )

            goal_id = await conn.fetchval(
                "SELECT create_goal($1, $2, $3, $4, $5, $6)",
                f"Calibrate belief {belief_id}",
                "calibrate belief",
                "curiosity",
                "queued",
                None,
                None,
            )
            await conn.fetchval(
                "SELECT begin_belief_exploration($1::uuid, $2::uuid)",
                belief_id,
                goal_id,
            )

            evidence_id = await _create_high_trust_evidence(
                conn,
                label=f"Evidence {get_test_identifier('evidence')}",
            )

            await conn.fetchval(
                "SELECT record_transformation_effort($1::uuid, $2, $3, $4::uuid)",
                belief_id,
                "reflect",
                "calibration notes",
                evidence_id,
            )

            result = await conn.fetchval(
                "SELECT calibrate_neutral_belief($1::uuid, $2, $3::uuid[])",
                belief_id,
                0.8,
                [evidence_id],
            )
            result = _coerce_json(result)
            assert result.get("success") is True

            row = await conn.fetchrow(
                "SELECT content, metadata->>'origin' as origin FROM memories WHERE id = $1::uuid",
                belief_id,
            )
            assert row["origin"] == "self_discovered"
            assert "high" in row["content"]
        finally:
            await tr.rollback()


async def test_calibrate_neutral_belief_after_self_discovered(db_pool, ensure_embedding_service):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(
                "SELECT set_config('transformation.personality', $1::jsonb)",
                json.dumps(
                    {
                        "stability": 0.99,
                        "evidence_threshold": 0.1,
                        "min_reflections": 5,
                        "min_heartbeats": 0,
                    }
                ),
            )
            belief_id = await conn.fetchval(
                """
                SELECT create_worldview_memory(
                    $1,
                    'self',
                    0.6,
                    0.9,
                    0.7,
                    'neutral_default',
                    NULL,
                    NULL,
                    NULL,
                    0.0
                )
                """,
                "I am neutral in conscientiousness",
            )
            await conn.execute(
                """
                UPDATE memories
                SET metadata = metadata || jsonb_build_object(
                    'subcategory', 'personality',
                    'trait', 'conscientiousness',
                    'origin', 'neutral_default',
                    'change_requires', 'deliberate_transformation',
                    'transformation_state', default_transformation_state()
                )
                WHERE id = $1
                """,
                belief_id,
            )

            goal_id = await conn.fetchval(
                "SELECT create_goal($1, $2, $3, $4, $5, $6)",
                f"Calibrate belief {belief_id}",
                "calibrate belief",
                "curiosity",
                "queued",
                None,
                None,
            )
            await conn.fetchval(
                "SELECT begin_belief_exploration($1::uuid, $2::uuid)",
                belief_id,
                goal_id,
            )

            evidence_id = await _create_high_trust_evidence(
                conn,
                label=f"Evidence {get_test_identifier('evidence')}",
            )

            await conn.fetchval(
                "SELECT record_transformation_effort($1::uuid, $2, $3, $4::uuid)",
                belief_id,
                "reflect",
                "calibration notes",
                evidence_id,
            )

            first = await conn.fetchval(
                "SELECT calibrate_neutral_belief($1::uuid, $2, $3::uuid[])",
                belief_id,
                0.7,
                [evidence_id],
            )
            first = _coerce_json(first)
            assert first.get("success") is True

            second = await conn.fetchval(
                "SELECT calibrate_neutral_belief($1::uuid, $2, $3::uuid[])",
                belief_id,
                0.8,
                [evidence_id],
            )
            second = _coerce_json(second)
            assert second.get("success") is False
            assert second.get("reason") == "not_neutral_default"
        finally:
            await tr.rollback()


async def test_calibrate_neutral_belief_insufficient_evidence(db_pool, ensure_embedding_service):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(
                "SELECT set_config('transformation.personality', $1::jsonb)",
                json.dumps(
                    {
                        "stability": 0.99,
                        "evidence_threshold": 0.1,
                        "min_reflections": 5,
                        "min_heartbeats": 0,
                    }
                ),
            )
            belief_id = await conn.fetchval(
                """
                SELECT create_worldview_memory(
                    $1,
                    'self',
                    0.6,
                    0.9,
                    0.7,
                    'neutral_default',
                    NULL,
                    NULL,
                    NULL,
                    0.0
                )
                """,
                "I am neutral in agreeableness",
            )
            await conn.execute(
                """
                UPDATE memories
                SET metadata = metadata || jsonb_build_object(
                    'subcategory', 'personality',
                    'trait', 'agreeableness',
                    'origin', 'neutral_default',
                    'change_requires', 'deliberate_transformation',
                    'transformation_state', default_transformation_state()
                )
                WHERE id = $1
                """,
                belief_id,
            )

            goal_id = await conn.fetchval(
                "SELECT create_goal($1, $2, $3, $4, $5, $6)",
                f"Calibrate belief {belief_id}",
                "calibrate belief",
                "curiosity",
                "queued",
                None,
                None,
            )
            await conn.fetchval(
                "SELECT begin_belief_exploration($1::uuid, $2::uuid)",
                belief_id,
                goal_id,
            )

            evidence_id = await conn.fetchval(
                "SELECT create_semantic_memory($1, 0.3, ARRAY['evidence'], NULL, NULL, 0.1)",
                f"Evidence {get_test_identifier('evidence')}",
            )

            await conn.fetchval(
                "SELECT record_transformation_effort($1::uuid, $2, $3, $4::uuid)",
                belief_id,
                "reflect",
                "weak evidence",
                evidence_id,
            )

            result = await conn.fetchval(
                "SELECT calibrate_neutral_belief($1::uuid, $2, $3::uuid[])",
                belief_id,
                0.7,
                [evidence_id],
            )
            result = _coerce_json(result)
            assert result.get("success") is False
            assert result.get("reason") == "insufficient_evidence"
        finally:
            await tr.rollback()


async def test_initialize_personality_core_values_worldview(db_pool, ensure_embedding_service):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            personality_raw = await conn.fetchval("SELECT initialize_personality(NULL)")
            personality = _coerce_json(personality_raw)
            assert personality.get("success") is True
            assert personality.get("created_traits") == 5

            values_raw = await conn.fetchval("SELECT initialize_core_values(NULL)")
            values = _coerce_json(values_raw)
            assert values.get("success") is True
            assert values.get("created_values") >= 3

            worldview_raw = await conn.fetchval("SELECT initialize_worldview(NULL)")
            worldview = _coerce_json(worldview_raw)
            assert worldview.get("success") is True
            assert worldview.get("created_worldview") == 4
        finally:
            await tr.rollback()


async def test_get_transformation_progress(db_pool, ensure_embedding_service):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(
                "SELECT set_config('transformation.personality', $1::jsonb)",
                json.dumps(
                    {
                        "stability": 0.99,
                        "evidence_threshold": 0.1,
                        "min_reflections": 2,
                        "min_heartbeats": 0,
                    }
                ),
            )
            belief_id = await _create_transformable_belief(conn, subcategory="personality")
            goal_id = await conn.fetchval(
                "SELECT create_goal($1, $2, $3, $4, $5, $6)",
                f"Explore belief {belief_id}",
                "explore belief",
                "curiosity",
                "queued",
                None,
                None,
            )
            await conn.fetchval(
                "SELECT begin_belief_exploration($1::uuid, $2::uuid)",
                belief_id,
                goal_id,
            )
            evidence_id = await _create_high_trust_evidence(
                conn,
                label=f"Evidence {get_test_identifier('evidence')}",
            )
            await conn.fetchval(
                "SELECT record_transformation_effort($1::uuid, $2, $3, $4::uuid)",
                belief_id,
                "reflect",
                "notes",
                evidence_id,
            )

            progress_raw = await conn.fetchval(
                "SELECT get_transformation_progress($1::uuid)",
                belief_id,
            )
            progress = _coerce_json(progress_raw)
            assert progress.get("status") == "exploring"
            assert progress.get("requirements", {}).get("min_reflections") == 2
            samples = progress.get("evidence_samples", [])
            assert isinstance(samples, list) and samples
            assert any(sample.get("memory_id") == str(evidence_id) for sample in samples)
        finally:
            await tr.rollback()


async def test_check_transformation_readiness(db_pool, ensure_embedding_service):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(
                "SELECT set_config('transformation.personality', $1::jsonb)",
                json.dumps(
                    {
                        "stability": 0.99,
                        "evidence_threshold": 0.1,
                        "min_reflections": 1,
                        "min_heartbeats": 0,
                    }
                ),
            )
            belief_id = await _create_transformable_belief(conn, subcategory="personality")
            goal_id = await conn.fetchval(
                "SELECT create_goal($1, $2, $3, $4, $5, $6)",
                f"Explore belief {belief_id}",
                "explore belief",
                "curiosity",
                "queued",
                None,
                None,
            )
            await conn.fetchval(
                "SELECT begin_belief_exploration($1::uuid, $2::uuid)",
                belief_id,
                goal_id,
            )
            evidence_id = await conn.fetchval(
                "SELECT create_semantic_memory($1, 0.9, ARRAY['evidence'], NULL, NULL, 0.9)",
                f"Evidence {get_test_identifier('evidence')}",
            )
            await conn.fetchval(
                "SELECT record_transformation_effort($1::uuid, $2, $3, $4::uuid)",
                belief_id,
                "reflect",
                "notes",
                evidence_id,
            )

            ready_raw = await conn.fetchval("SELECT check_transformation_readiness()")
            ready = _coerce_json(ready_raw)
            assert isinstance(ready, list)
            assert any(item.get("belief_id") == belief_id for item in ready)
        finally:
            await tr.rollback()


async def test_abandon_belief_exploration(db_pool, ensure_embedding_service):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            belief_id = await _create_transformable_belief(conn, subcategory="personality")
            goal_id = await conn.fetchval(
                "SELECT create_goal($1, $2, $3, $4, $5, $6)",
                f"Explore belief {belief_id}",
                "explore belief",
                "curiosity",
                "queued",
                None,
                None,
            )
            await conn.fetchval(
                "SELECT begin_belief_exploration($1::uuid, $2::uuid)",
                belief_id,
                goal_id,
            )

            result = await conn.fetchval(
                "SELECT abandon_belief_exploration($1::uuid, $2)",
                belief_id,
                "pause",
            )
            result = _coerce_json(result)
            assert result.get("success") is True

            state = await conn.fetchval(
                "SELECT metadata->'transformation_state' FROM memories WHERE id = $1::uuid",
                belief_id,
            )
            state = _coerce_json(state)
            assert state.get("active_exploration") is False
            assert state.get("exploration_goal_id") is None
            assert state.get("reflection_count") == 0
            assert state.get("evidence_memories") == []
        finally:
            await tr.rollback()


async def test_reexplore_after_failed_attempt(db_pool, ensure_embedding_service):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(
                "SELECT set_config('transformation.personality', $1::jsonb)",
                json.dumps(
                    {
                        "stability": 0.99,
                        "evidence_threshold": 0.1,
                        "min_reflections": 2,
                        "min_heartbeats": 0,
                    }
                ),
            )
            belief_id = await _create_transformable_belief(conn, subcategory="personality")
            goal_id = await conn.fetchval(
                "SELECT create_goal($1, $2, $3, $4, $5, $6)",
                f"Explore belief {belief_id}",
                "explore belief",
                "curiosity",
                "queued",
                None,
                None,
            )
            await conn.fetchval(
                "SELECT begin_belief_exploration($1::uuid, $2::uuid)",
                belief_id,
                goal_id,
            )
            evidence_id = await _create_high_trust_evidence(
                conn,
                label=f"Evidence {get_test_identifier('evidence')}",
            )
            await conn.fetchval(
                "SELECT record_transformation_effort($1::uuid, $2, $3, $4::uuid)",
                belief_id,
                "reflect",
                "notes",
                evidence_id,
            )

            first_attempt = await conn.fetchval(
                "SELECT attempt_worldview_transformation($1::uuid, $2, $3)",
                belief_id,
                "Updated belief content",
                "shift",
            )
            first_attempt = _coerce_json(first_attempt)
            assert first_attempt.get("success") is False
            assert first_attempt.get("reason") == "insufficient_reflections"

            state = await conn.fetchval(
                "SELECT metadata->'transformation_state' FROM memories WHERE id = $1::uuid",
                belief_id,
            )
            state = _coerce_json(state)
            assert state.get("active_exploration") is True
            assert state.get("reflection_count") == 1

            await conn.fetchval(
                "SELECT record_transformation_effort($1::uuid, $2, $3, $4::uuid)",
                belief_id,
                "reflect",
                "more notes",
                evidence_id,
            )
            second_attempt = await conn.fetchval(
                "SELECT attempt_worldview_transformation($1::uuid, $2, $3)",
                belief_id,
                "Updated belief content",
                "shift",
            )
            second_attempt = _coerce_json(second_attempt)
            assert second_attempt.get("success") is True
        finally:
            await tr.rollback()


async def test_integration_goal_to_transform(db_pool, ensure_embedding_service):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(
                "SELECT set_config('transformation.personality', $1::jsonb)",
                json.dumps(
                    {
                        "stability": 0.99,
                        "evidence_threshold": 0.1,
                        "min_reflections": 1,
                        "min_heartbeats": 0,
                    }
                ),
            )
            belief_id = await _create_transformable_belief(conn, subcategory="personality")
            goal_id = await conn.fetchval(
                "SELECT create_goal($1, $2, $3, $4, $5, $6)",
                f"Explore belief {belief_id}",
                "explore belief",
                "curiosity",
                "queued",
                None,
                None,
            )
            await conn.fetchval(
                "SELECT begin_belief_exploration($1::uuid, $2::uuid)",
                belief_id,
                goal_id,
            )
            evidence_id = await _create_high_trust_evidence(
                conn,
                label=f"Evidence {get_test_identifier('evidence')}",
            )
            await conn.fetchval(
                "SELECT record_transformation_effort($1::uuid, $2, $3, $4::uuid)",
                belief_id,
                "contemplate",
                "notes",
                evidence_id,
            )
            result = await conn.fetchval(
                "SELECT attempt_worldview_transformation($1::uuid, $2, $3)",
                belief_id,
                "Updated belief content",
                "shift",
            )
            result = _coerce_json(result)
            assert result.get("success") is True
        finally:
            await tr.rollback()


async def test_multiple_concurrent_explorations(db_pool, ensure_embedding_service):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            belief_a = await _create_transformable_belief(conn, subcategory="personality")
            belief_b = await _create_transformable_belief(conn, subcategory="core_value")
            goal_a = await conn.fetchval(
                "SELECT create_goal($1, $2, $3, $4, $5, $6)",
                f"Explore belief {belief_a}",
                "explore belief",
                "curiosity",
                "queued",
                None,
                None,
            )
            goal_b = await conn.fetchval(
                "SELECT create_goal($1, $2, $3, $4, $5, $6)",
                f"Explore belief {belief_b}",
                "explore belief",
                "curiosity",
                "queued",
                None,
                None,
            )
            result_a = await conn.fetchval(
                "SELECT begin_belief_exploration($1::uuid, $2::uuid)",
                belief_a,
                goal_a,
            )
            result_a = _coerce_json(result_a)
            assert result_a.get("success") is True

            result_b = await conn.fetchval(
                "SELECT begin_belief_exploration($1::uuid, $2::uuid)",
                belief_b,
                goal_b,
            )
            result_b = _coerce_json(result_b)
            assert result_b.get("success") is True

            active_raw = await conn.fetchval(
                "SELECT get_active_transformations_context(5)"
            )
            active = _coerce_json(active_raw)
            ids = {item.get("belief_id") for item in active if isinstance(item, dict)}
            assert belief_a in ids
            assert belief_b in ids
        finally:
            await tr.rollback()
