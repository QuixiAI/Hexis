"""HMX onboarding interviews (#97): the persistence + binding-execution layer
in db/49a_hmx_onboarding_interview.sql. A use case fully defined in one HMX
file can carry a set of questions that bind answers to actions via existing
machinery -- these tests cover the state machine (start/answer/skip/resume/
completion) and the 'remember' binding executor end to end, plus an
unimplemented binding failing loud rather than silently doing nothing.
"""
from __future__ import annotations

import json

import pytest

from tests.utils import get_test_identifier

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture(scope="module", autouse=True)
async def _apply_migration_0268(db_pool):
    from pathlib import Path

    sql = Path("db/migrations/0268_hmx_onboarding_interview_bindings.sql").read_text(
        encoding="utf-8"
    )
    async with db_pool.acquire() as conn:
        await conn.execute(sql)


def _j(value):
    return json.loads(value) if isinstance(value, str) else value


def _interview(marker: str, **overrides) -> dict:
    spec = {
        "version": 1,
        "questions": [
            {
                "id": "name",
                "type": "choice",
                "prompt": "What should I call you?",
                "options": [
                    {"label": "Boss", "value": "boss"},
                    {"label": "Friend", "value": "friend"},
                ],
                "binds": {
                    "action": "remember",
                    "params_template": {
                        "content": f"The user {marker} wants to be called {{{{answer}}}}",
                        "type": "semantic",
                    },
                },
                "required": True,
            },
            {
                "id": "extra",
                "type": "freeform",
                "prompt": "Anything else?",
                "binds": {
                    "action": "remember",
                    "params_template": {"content": f"Extra note {marker}: {{{{answer}}}}"},
                },
                "skippable": True,
            },
        ],
    }
    spec.update(overrides)
    return spec


async def test_start_hmx_interview_is_idempotent(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            marker = get_test_identifier("hmxinterview")
            export_id = f"exp_{marker[:16]:0<16}"
            spec = _interview(marker)

            first = _j(
                await conn.fetchval(
                    "SELECT start_hmx_interview($1, $2::jsonb)", export_id, json.dumps(spec)
                )
            )
            assert first["status"] == "pending"
            assert first["export_id"] == export_id
            assert len(first["questions"]) == 2

            second = _j(
                await conn.fetchval(
                    "SELECT start_hmx_interview($1, $2::jsonb)", export_id, json.dumps(spec)
                )
            )
            assert second["id"] == first["id"]
        finally:
            await tr.rollback()


async def test_required_question_completes_interview_and_applies_remember_binding(
    db_pool,
):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            marker = get_test_identifier("hmxinterview")
            export_id = f"exp_{marker[:16]:0<16}"
            spec = _interview(marker)
            await conn.fetchval(
                "SELECT start_hmx_interview($1, $2::jsonb)", export_id, json.dumps(spec)
            )

            result = _j(
                await conn.fetchval(
                    "SELECT record_hmx_interview_answer($1, 'name', 'answered', $2::jsonb)",
                    export_id,
                    json.dumps("friend"),
                )
            )
            assert result["success"] is True
            assert result["binding_result"]["success"] is True
            # 'name' is the only required question, so the interview is
            # already complete even with 'extra' unanswered.
            assert result["interview_status"] == "completed"

            mem = await conn.fetchrow(
                "SELECT content, source_attribution FROM memories WHERE content = $1",
                f"The user {marker} wants to be called friend",
            )
            assert mem is not None
            source = _j(mem["source_attribution"])
            assert source["kind"] == "hmx_interview"
            assert source["ref"] == export_id
            assert source["label"] == "name"

            state = _j(await conn.fetchval("SELECT get_hmx_interview_state($1)", export_id))
            assert state["status"] == "completed"
            assert len(state["answers"]) == 1
        finally:
            await tr.rollback()


async def test_skipping_a_question_records_no_binding(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            marker = get_test_identifier("hmxinterview")
            export_id = f"exp_{marker[:16]:0<16}"
            spec = _interview(marker)
            await conn.fetchval(
                "SELECT start_hmx_interview($1, $2::jsonb)", export_id, json.dumps(spec)
            )

            result = _j(
                await conn.fetchval(
                    "SELECT record_hmx_interview_answer($1, 'extra', 'skipped')", export_id
                )
            )
            assert result["success"] is True
            assert result["binding_result"] is None

            count = await conn.fetchval(
                "SELECT count(*) FROM memories WHERE content LIKE $1",
                f"Extra note {marker}:%",
            )
            assert count == 0
        finally:
            await tr.rollback()


async def test_unknown_binding_action_fails_loud(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            marker = get_test_identifier("hmxinterview")
            export_id = f"exp_{marker[:16]:0<16}"
            spec = _interview(
                marker,
                questions=[
                    {
                        "id": "future_q",
                        "type": "freeform",
                        "prompt": "Unsupported action?",
                        "binds": {"action": "teleport_moon", "params_template": {"target": "{{answer}}"}},
                    }
                ],
            )
            await conn.fetchval(
                "SELECT start_hmx_interview($1, $2::jsonb)", export_id, json.dumps(spec)
            )

            result = _j(
                await conn.fetchval(
                    "SELECT record_hmx_interview_answer($1, 'future_q', 'answered', $2::jsonb)",
                    export_id,
                    json.dumps("mars"),
                )
            )
            assert result["success"] is True  # recording the answer itself succeeded
            assert result["binding_result"]["success"] is False
            assert result["binding_result"]["error_type"] == "not_implemented"
            assert "teleport_moon" in result["binding_result"]["error"]
        finally:
            await tr.rollback()


async def test_unanswered_required_question_leaves_interview_pending(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            marker = get_test_identifier("hmxinterview")
            export_id = f"exp_{marker[:16]:0<16}"
            spec = _interview(marker)
            await conn.fetchval(
                "SELECT start_hmx_interview($1, $2::jsonb)", export_id, json.dumps(spec)
            )

            # Answer only the non-required question.
            result = _j(
                await conn.fetchval(
                    "SELECT record_hmx_interview_answer($1, 'extra', 'answered', $2::jsonb)",
                    export_id,
                    json.dumps("nothing else"),
                )
            )
            assert result["interview_status"] == "pending"

            state = _j(await conn.fetchval("SELECT get_hmx_interview_state($1)", export_id))
            assert state["status"] == "pending"
        finally:
            await tr.rollback()


async def test_answering_unknown_question_id_is_a_clear_error(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            marker = get_test_identifier("hmxinterview")
            export_id = f"exp_{marker[:16]:0<16}"
            spec = _interview(marker)
            await conn.fetchval(
                "SELECT start_hmx_interview($1, $2::jsonb)", export_id, json.dumps(spec)
            )

            result = _j(
                await conn.fetchval(
                    "SELECT record_hmx_interview_answer($1, 'nonexistent', 'answered', $2::jsonb)",
                    export_id,
                    json.dumps("x"),
                )
            )
            assert result["success"] is False
            assert "unknown question id" in result["error"]
        finally:
            await tr.rollback()


async def test_answering_with_no_interview_started_is_a_clear_error(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            result = _j(
                await conn.fetchval(
                    "SELECT record_hmx_interview_answer('exp_0000000000000000', 'name', 'answered', $1::jsonb)",
                    json.dumps("x"),
                )
            )
            assert result["success"] is False
            assert "no interview in progress" in result["error"]
        finally:
            await tr.rollback()


async def _ensure_migration_0268(conn) -> None:
    from pathlib import Path

    sql = Path("db/migrations/0268_hmx_onboarding_interview_bindings.sql").read_text(
        encoding="utf-8"
    )
    await conn.execute(sql)


async def test_all_seven_bindings_journal_inbox_and_security_rails(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _ensure_migration_0268(conn)
            marker = get_test_identifier("hmxall7")
            export_id = f"exp_{marker[:16]:0<16}"
            spec = {
                "version": 1,
                "questions": [
                    {
                        "id": "q_identity",
                        "type": "freeform",
                        "prompt": "What should my name be?",
                        "binds": {
                            "action": "init_identity",
                            "params_template": {"name": "{{answer}}", "voice": "warm and concise"},
                        },
                    },
                    {
                        "id": "q_goal",
                        "type": "freeform",
                        "prompt": "What goal should we track?",
                        "binds": {
                            "action": "create_goal",
                            "params_template": {
                                "title": f"Goal {marker}: {{{{answer}}}}",
                                "description": "Seeded from onboarding",
                                "priority": "queued",
                            },
                        },
                    },
                    {
                        "id": "q_guardrail_tool",
                        "type": "freeform",
                        "prompt": "Any tool restriction?",
                        "binds": {
                            "action": "set_guardrail",
                            "params_template": {
                                "content": f"Boundary {marker}: {{{{answer}}}}",
                                "restricts_tools": ["shell"],
                            },
                        },
                    },
                    {
                        "id": "q_guardrail_belief",
                        "type": "freeform",
                        "prompt": "Any behavioral rule?",
                        "binds": {
                            "action": "set_guardrail",
                            "params_template": {
                                "content": f"Rule {marker}: {{{{answer}}}}",
                                "category": "boundary",
                            },
                        },
                    },
                    {
                        "id": "q_ingest_text",
                        "type": "freeform",
                        "prompt": "Paste starter notes",
                        "binds": {
                            "action": "ingest",
                            "params_template": {
                                "kind": "text",
                                "title": f"Starter {marker}",
                                "content": "{{answer}}",
                            },
                        },
                    },
                    {
                        "id": "q_ingest_path_blocked",
                        "type": "freeform",
                        "prompt": "Blocked local path",
                        "binds": {
                            "action": "ingest",
                            "params_template": {"path": "{{answer}}"},
                        },
                    },
                    {
                        "id": "q_schedule",
                        "type": "choice",
                        "prompt": "Weekly check-in?",
                        "options": [{"label": "Hourly", "value": 3600}],
                        "binds": {
                            "action": "create_scheduled_task",
                            "params_template": {
                                "name": f"checkin_{marker}",
                                "schedule_kind": "interval",
                                "schedule": {"every_seconds": "{{answer}}"},
                                "action_kind": "queue_user_message",
                                "action_payload": {"message": "Time for your check-in"},
                            },
                        },
                    },
                    {
                        "id": "q_cfg_ok",
                        "type": "choice",
                        "prompt": "Enable digest?",
                        "options": [{"label": "Yes", "value": True}],
                        "binds": {
                            "action": "set_config",
                            "params_template": {
                                "key": f"preferences.{marker}_digest",
                                "value": "{{answer}}",
                            },
                        },
                    },
                    {
                        "id": "q_cfg_env_ok",
                        "type": "freeform",
                        "prompt": "Slack token env var name",
                        "binds": {
                            "action": "set_config",
                            "params_template": {
                                "key": "channel.slack.bot_token",
                                "value": "{{answer}}",
                            },
                        },
                    },
                    {
                        "id": "q_cfg_secret_blocked",
                        "type": "freeform",
                        "prompt": "Raw secret rejected",
                        "binds": {
                            "action": "set_config",
                            "params_template": {
                                "key": "channel.slack.bot_token",
                                "value": "{{answer}}",
                            },
                        },
                    },
                    {
                        "id": "q_cfg_disallowed_key",
                        "type": "freeform",
                        "prompt": "Privileged key rejected",
                        "binds": {
                            "action": "set_config",
                            "params_template": {
                                "key": "llm.chat",
                                "value": "{{answer}}",
                            },
                        },
                    },
                ],
            }
            await conn.fetchval(
                "SELECT start_hmx_interview($1, $2::jsonb)", export_id, json.dumps(spec)
            )

            # Verify get_pending_hmx_interviews sees our interview before completion.
            pending = _j(await conn.fetchval("SELECT get_pending_hmx_interviews()"))
            mine = [p for p in pending if p["export_id"] == export_id]
            assert len(mine) == 1
            assert len(mine[0]["remaining_questions"]) == len(spec["questions"])

            # 1. init_identity: preserves init_stage when already complete.
            prev_stage = await conn.fetchval("SELECT init_stage::text FROM heartbeat_state WHERE id = 1")
            res_id = _j(await conn.fetchval(
                "SELECT record_hmx_interview_answer($1, 'q_identity', 'answered', $2::jsonb)",
                export_id, json.dumps(f"Nova-{marker[:6]}"),
            ))
            assert res_id["binding_result"]["success"] is True
            assert res_id["interview_status"] == "pending"  # all-optional interview stays pending!
            if prev_stage == "complete":
                after_stage = await conn.fetchval("SELECT init_stage::text FROM heartbeat_state WHERE id = 1")
                assert after_stage == "complete"
            agent_name = await conn.fetchval("SELECT get_config_text('agent.name')")
            assert agent_name == f"Nova-{marker[:6]}"

            # 2. create_goal
            res_goal = _j(await conn.fetchval(
                "SELECT record_hmx_interview_answer($1, 'q_goal', 'answered', $2::jsonb)",
                export_id, json.dumps("Ship HMX onboarding"),
            ))
            assert res_goal["binding_result"]["success"] is True, res_goal
            goal_row = await conn.fetchrow(
                "SELECT content, source_attribution, goal_origin::text FROM memories WHERE id = $1::uuid",
                res_goal["binding_result"]["goal_id"],
            )
            assert goal_row is not None
            assert goal_row["goal_origin"] == "user_request"
            assert _j(goal_row["source_attribution"])["kind"] == "hmx_interview"

            # 3. set_guardrail (tool boundary + behavioral boundary)
            res_gt = _j(await conn.fetchval(
                "SELECT record_hmx_interview_answer($1, 'q_guardrail_tool', 'answered', $2::jsonb)",
                export_id, json.dumps("No shell calls"),
            ))
            assert res_gt["binding_result"]["success"] is True
            res_gb = _j(await conn.fetchval(
                "SELECT record_hmx_interview_answer($1, 'q_guardrail_belief', 'answered', $2::jsonb)",
                export_id, json.dumps("Always cite sources"),
            ))
            assert res_gb["binding_result"]["success"] is True

            # 4. ingest (text succeeds; local path fails loud with policy_violation)
            res_ing = _j(await conn.fetchval(
                "SELECT record_hmx_interview_answer($1, 'q_ingest_text', 'answered', $2::jsonb)",
                export_id, json.dumps("Project kickoff notes for Hexis."),
            ))
            assert res_ing["binding_result"]["success"] is True
            job_exists = await conn.fetchval(
                "SELECT count(*) FROM ingestion_jobs WHERE id = $1::uuid",
                res_ing["binding_result"]["job_id"],
            )
            assert job_exists == 1

            res_ing_bad = _j(await conn.fetchval(
                "SELECT record_hmx_interview_answer($1, 'q_ingest_path_blocked', 'answered', $2::jsonb)",
                export_id, json.dumps("/etc/passwd"),
            ))
            assert res_ing_bad["binding_result"]["success"] is False
            assert res_ing_bad["binding_result"]["error_type"] == "policy_violation"

            # 5. create_scheduled_task (typed integer substituted into schedule.every_seconds!)
            res_sched = _j(await conn.fetchval(
                "SELECT record_hmx_interview_answer($1, 'q_schedule', 'answered', $2::jsonb)",
                export_id, json.dumps(3600),
            ))
            assert res_sched["binding_result"]["success"] is True, res_sched
            sched_row = await conn.fetchrow(
                "SELECT name, created_by FROM scheduled_tasks WHERE id = $1::uuid",
                res_sched["binding_result"]["task_id"],
            )
            assert sched_row["created_by"] == "hmx_interview"

            # 6. set_config: allowed key preserves JSON boolean; env-var name passes;
            #    raw secret value and disallowed privileged key fail loud with policy_violation.
            res_cfg = _j(await conn.fetchval(
                "SELECT record_hmx_interview_answer($1, 'q_cfg_ok', 'answered', $2::jsonb)",
                export_id, json.dumps(True),
            ))
            assert res_cfg["binding_result"]["success"] is True
            assert await conn.fetchval(
                "SELECT get_config_bool($1)", f"preferences.{marker}_digest"
            ) is True

            res_env = _j(await conn.fetchval(
                "SELECT record_hmx_interview_answer($1, 'q_cfg_env_ok', 'answered', $2::jsonb)",
                export_id, json.dumps("MY_SLACK_BOT_TOKEN"),
            ))
            assert res_env["binding_result"]["success"] is True

            res_secret = _j(await conn.fetchval(
                "SELECT record_hmx_interview_answer($1, 'q_cfg_secret_blocked', 'answered', $2::jsonb)",
                export_id, json.dumps("xoxb-real-secret-token-123456"),
            ))
            assert res_secret["binding_result"]["success"] is False
            assert res_secret["binding_result"]["error_type"] == "policy_violation"

            res_disallowed = _j(await conn.fetchval(
                "SELECT record_hmx_interview_answer($1, 'q_cfg_disallowed_key', 'answered', $2::jsonb)",
                export_id, json.dumps({"provider": "evil"}),
            ))
            assert res_disallowed["binding_result"]["success"] is False
            assert res_disallowed["binding_result"]["error_type"] == "policy_violation"
            # Last question settled -> all-optional interview now completes!
            assert res_disallowed["interview_status"] == "completed"

            # Verify change_journal (#93) and web_inbox recorded consequential bindings.
            journal_count = await conn.fetchval(
                "SELECT count(*) FROM change_journal WHERE kind = 'hmx_interview' AND detail->>'export_id' = $1",
                export_id,
            )
            assert journal_count >= 7

            inbox_count = await conn.fetchval(
                "SELECT count(*) FROM web_inbox WHERE kind = 'hmx_interview' AND payload->>'export_id' = $1",
                export_id,
            )
            assert inbox_count >= 7
        finally:
            await tr.rollback()


async def test_required_question_cannot_be_skipped_and_chat_tools_work(db_pool):
    from core.tools import ToolContext, ToolExecutionContext, create_default_registry

    async with db_pool.acquire() as conn:
        await _ensure_migration_0268(conn)

    marker = get_test_identifier("hmxtool")
    export_id = f"exp_{marker[:16]:0<16}"
    spec = {
        "version": 1,
        "questions": [
            {
                "id": "tone",
                "type": "choice",
                "prompt": "Pick a tone",
                "options": [
                    {"label": "Crisp & direct", "value": "direct"},
                    {"label": "Warm & chatty", "value": "warm"},
                ],
                "binds": {
                    "action": "set_config",
                    "params_template": {"key": f"preferences.{marker}_tone", "value": "{{answer}}"},
                },
                "required": True,
                "skippable": False,
            }
        ],
    }

    async with db_pool.acquire() as conn:
        await conn.fetchval(
            "SELECT start_hmx_interview($1, $2::jsonb)", export_id, json.dumps(spec)
        )

    registry = create_default_registry(db_pool)
    ctx = ToolExecutionContext(
        tool_context=ToolContext.CHAT,
        call_id="hmx-tool-1",
        session_id="chat-session-hmx",
        registry=registry,
    )
    try:
        status_res = await registry.execute("hmx_interview_status", {"export_id": export_id}, ctx)
        assert status_res.success
        assert status_res.output["count"] == 1
        assert status_res.output["interviews"][0]["remaining_questions"][0]["effect_summary"]

        # Skipping required non-skippable question is rejected cleanly.
        skip_res = await registry.execute(
            "record_hmx_interview_answer",
            {"export_id": export_id, "question_id": "tone", "status": "skipped"},
            ctx,
        )
        assert not skip_res.success
        assert "required" in (skip_res.error or "").lower()

        # Answering with option LABEL maps automatically to declared option VALUE.
        ans_res = await registry.execute(
            "record_hmx_interview_answer",
            {"export_id": export_id, "question_id": "tone", "status": "answered", "answer": "Crisp & direct"},
            ctx,
        )
        assert ans_res.success, ans_res.error
        assert ans_res.output["binding_result"]["value"] == "direct"
        assert ans_res.output["interview_status"] == "completed"
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM hmx_interview_state WHERE export_id = $1", export_id)
            await conn.execute("DELETE FROM config WHERE key = $1", f"preferences.{marker}_tone")
            await conn.execute(
                "DELETE FROM change_journal WHERE kind = 'hmx_interview' AND detail->>'export_id' = $1",
                export_id,
            )
            await conn.execute(
                "DELETE FROM web_inbox WHERE kind = 'hmx_interview' AND payload->>'export_id' = $1",
                export_id,
            )
