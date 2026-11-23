"""core/hmx_interview.py: the CLI-facing orchestration for HMX onboarding
interviews (#97). The DB does persistence + binding execution
(tests/db/test_hmx_onboarding_interview.py); this covers the question-loop
logic -- skip handling, the required-question re-ask loop, and the
non-interactive/no-section/already-completed short-circuits -- against a
mocked connection and mocked prompts.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from core.hmx_interview import maybe_run_cli_interview

pytestmark = pytest.mark.asyncio


def _document(**interview_overrides):
    interview = {
        "version": 1,
        "questions": [
            {
                "id": "name",
                "type": "choice",
                "prompt": "What should I call you?",
                "options": [{"label": "Boss", "value": "boss"}, {"label": "Friend", "value": "friend"}],
                "binds": {"action": "remember", "params_template": {}},
                "skippable": True,
            },
            {
                "id": "extra",
                "type": "freeform",
                "prompt": "Anything else?",
                "binds": {"action": "remember", "params_template": {}},
                "skippable": True,
            },
        ],
    }
    interview.update(interview_overrides)
    return {"export_id": "exp_1111111111111111", "onboarding_interview": interview}


def _mock_conn(*, state_after_start, per_question_results):
    """conn.fetchval dispatches on the SQL text's first word after SELECT."""

    async def fetchval(query, *args):
        if "start_hmx_interview" in query:
            return json.dumps(state_after_start)
        if "record_hmx_interview_answer" in query:
            question_id = args[1]
            return json.dumps(per_question_results[question_id])
        if "get_hmx_interview_state" in query:
            return json.dumps(state_after_start | {"status": "completed"})
        raise AssertionError(f"unexpected query: {query}")

    conn = AsyncMock()
    conn.fetchval.side_effect = fetchval
    return conn


async def test_no_interview_section_is_a_no_op():
    conn = AsyncMock()
    outcome = await maybe_run_cli_interview(conn, {"export_id": "exp_x"}, interactive=True)
    assert outcome.ran is False
    conn.fetchval.assert_not_called()


async def test_non_interactive_starts_but_does_not_prompt():
    conn = _mock_conn(
        state_after_start={"status": "pending", "answers": []},
        per_question_results={},
    )
    doc = _document()
    with patch("core.hmx_interview.select_value") as select_mock:
        outcome = await maybe_run_cli_interview(conn, doc, interactive=False)
    assert outcome.ran is False
    assert doc["export_id"] in outcome.note
    select_mock.assert_not_called()
    conn.fetchval.assert_called_once()  # only start_hmx_interview


async def test_already_completed_interview_is_skipped():
    conn = _mock_conn(
        state_after_start={"status": "completed", "answers": []},
        per_question_results={},
    )
    with patch("core.hmx_interview.select_value") as select_mock:
        outcome = await maybe_run_cli_interview(conn, _document(), interactive=True)
    assert outcome.ran is False
    assert "already completed" in outcome.note
    select_mock.assert_not_called()


async def test_choice_and_freeform_questions_answered_end_to_end():
    conn = _mock_conn(
        state_after_start={"status": "pending", "answers": []},
        per_question_results={
            "name": {
                "success": True,
                "binding_result": {"success": True},
                "interview_status": "pending",
            },
            "extra": {
                "success": True,
                "binding_result": {"success": True},
                "interview_status": "completed",
            },
        },
    )
    with (
        patch("core.hmx_interview.select_value", new=AsyncMock(return_value="friend")),
        patch("core.hmx_interview.prompt_text", new=AsyncMock(return_value="nothing else")),
    ):
        outcome = await maybe_run_cli_interview(conn, _document(), interactive=True)

    assert outcome.ran is True
    assert outcome.completed is True
    assert outcome.answered == 2
    assert outcome.skipped == 0
    assert outcome.failed_bindings == 0

    # The choice answer's value (not its label) is what gets recorded.
    answer_calls = [c for c in conn.fetchval.call_args_list if "record_hmx_interview_answer" in c.args[0]]
    assert len(answer_calls) == 2
    assert json.loads(answer_calls[0].args[3]) == "friend"
    assert json.loads(answer_calls[1].args[3]) == "nothing else"


async def test_skippable_freeform_question_skips_on_empty_answer():
    conn = _mock_conn(
        state_after_start={"status": "pending", "answers": []},
        per_question_results={
            "name": {"success": True, "binding_result": None, "interview_status": "pending"},
        },
    )
    doc = _document(
        questions=[
            {
                "id": "name",
                "type": "freeform",
                "prompt": "What should I call you?",
                "binds": {"action": "remember", "params_template": {}},
                "skippable": True,
            }
        ]
    )
    with patch("core.hmx_interview.prompt_text", new=AsyncMock(return_value="")):
        outcome = await maybe_run_cli_interview(conn, doc, interactive=True)

    assert outcome.skipped == 1
    assert outcome.answered == 0
    skip_calls = [c for c in conn.fetchval.call_args_list if "record_hmx_interview_answer" in c.args[0]]
    assert len(skip_calls) == 1
    assert "'skipped'" in skip_calls[0].args[0]
    assert skip_calls[0].args[2] == "name"


async def test_required_freeform_question_re_asks_until_answered():
    conn = _mock_conn(
        state_after_start={"status": "pending", "answers": []},
        per_question_results={
            "name": {"success": True, "binding_result": {"success": True}, "interview_status": "completed"},
        },
    )
    doc = _document(
        questions=[
            {
                "id": "name",
                "type": "freeform",
                "prompt": "What should I call you?",
                "binds": {"action": "remember", "params_template": {}},
                "required": True,
                "skippable": False,
            }
        ]
    )
    prompt_mock = AsyncMock(side_effect=["", "  ", "Friend"])
    with patch("core.hmx_interview.prompt_text", new=prompt_mock):
        outcome = await maybe_run_cli_interview(conn, doc, interactive=True)

    assert prompt_mock.await_count == 3
    assert outcome.answered == 1
    assert outcome.skipped == 0


async def test_failed_binding_is_counted_and_reported():
    conn = _mock_conn(
        state_after_start={"status": "pending", "answers": []},
        per_question_results={
            "name": {
                "success": True,
                "binding_result": {"success": False, "error": "nope", "error_type": "not_implemented"},
                "interview_status": "completed",
            },
        },
    )
    doc = _document(
        questions=[
            {
                "id": "name",
                "type": "choice",
                "prompt": "Pick one",
                "options": [{"label": "A", "value": "a"}],
                "binds": {"action": "create_goal", "params_template": {}},
                "skippable": True,
            }
        ]
    )
    with patch("core.hmx_interview.select_value", new=AsyncMock(return_value="a")):
        outcome = await maybe_run_cli_interview(conn, doc, interactive=True)

    assert outcome.answered == 1
    assert outcome.failed_bindings == 1


async def test_already_answered_questions_are_not_re_asked():
    conn = _mock_conn(
        state_after_start={
            "status": "pending",
            "answers": [{"question_id": "name", "status": "answered"}],
        },
        per_question_results={
            "extra": {"success": True, "binding_result": {"success": True}, "interview_status": "completed"},
        },
    )
    with (
        patch("core.hmx_interview.select_value", new=AsyncMock()) as select_mock,
        patch("core.hmx_interview.prompt_text", new=AsyncMock(return_value="ok")),
    ):
        outcome = await maybe_run_cli_interview(conn, _document(), interactive=True)

    select_mock.assert_not_called()  # 'name' (choice) already answered
    assert outcome.answered == 1  # only 'extra' asked


async def test_helpers_resolve_choice_labels_summarize_bindings_and_format_chat_prompt():
    from core.hmx_interview import (
        format_pending_interviews_prompt,
        resolve_choice_value,
        summarize_binding_effect,
    )

    q = {
        "id": "cadence",
        "type": "choice",
        "prompt": "How often should I check in?",
        "options": [
            {"label": "Daily morning", "value": "daily"},
            {"label": "Weekly Friday", "value": "weekly"},
        ],
        "binds": {
            "action": "set_config",
            "params_template": {"key": "preferences.cadence", "value": "{{answer}}"},
        },
        "required": True,
    }
    assert resolve_choice_value(q, "Daily morning") == "daily"
    assert resolve_choice_value(q, "weekly") == "weekly"
    assert "preferences.cadence" in summarize_binding_effect(q)

    prompt = format_pending_interviews_prompt(
        [{"export_id": "exp_abc", "remaining_questions": [q]}]
    )
    assert "Pending HMX Onboarding Interview" in prompt
    assert "ask_user" in prompt
    assert "record_hmx_interview_answer" in prompt
    assert "exp_abc" in prompt
    assert "preferences.cadence" in prompt


async def test_hmx_interview_web_api_and_cli_resume(monkeypatch):
    import httpx

    import apps.hexis_api as hexis_api
    from apps.hexis_cli import build_parser
    from core.hmx_interview import run_pending_cli_interviews

    # CLI parser accepts `hexis interview --export-id ... --json`.
    parser = build_parser()
    parsed = parser.parse_args(["interview", "--export-id", "exp_123", "--json"])
    assert parsed.func == "hmx_interview"
    assert parsed.export_id == "exp_123"
    assert parsed.json is True

    # Non-interactive resume returns pending interviews with actionable note.
    pending_row = {
        "export_id": "exp_123",
        "status": "pending",
        "questions": _document()["onboarding_interview"]["questions"],
        "answers": [],
        "remaining_questions": _document()["onboarding_interview"]["questions"],
    }
    conn = AsyncMock()
    conn.fetchval.return_value = json.dumps([pending_row])
    interviews, outcome = await run_pending_cli_interviews(conn, interactive=False)
    assert len(interviews) == 1
    assert outcome.ran is False
    assert "hexis interview" in (outcome.note or "")

    # Web API GET /api/hmx/interviews and POST /api/hmx/interviews/answer.
    class FakeConn:
        async def fetchval(self, query, *args):
            if "get_pending_hmx_interviews" in query:
                return json.dumps([pending_row])
            if "get_hmx_interview_state" in query:
                return json.dumps(pending_row)
            if "record_hmx_interview_answer" in query:
                assert args[0] == "exp_123"
                assert args[1] == "name"
                assert json.loads(args[2]) == "friend"
                return json.dumps(
                    {
                        "success": True,
                        "question_id": "name",
                        "status": "answered",
                        "binding_result": {"success": True},
                        "interview_status": "pending",
                    }
                )
            raise AssertionError(query)

    class FakeAcquire:
        async def __aenter__(self):
            return FakeConn()

        async def __aexit__(self, *args):
            return False

    class FakePool:
        def acquire(self):
            return FakeAcquire()

    monkeypatch.setattr(hexis_api, "_pool", FakePool())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=hexis_api.app),
        base_url="http://testserver",
    ) as client:
        r_list = await client.get("/api/hmx/interviews")
        assert r_list.status_code == 200
        assert r_list.json()["count"] == 1
        assert r_list.json()["interviews"][0]["remaining_questions"][0]["effect_summary"]

        # Label "Friend" maps to value "friend".
        r_ans = await client.post(
            "/api/hmx/interviews/answer",
            json={
                "export_id": "exp_123",
                "question_id": "name",
                "status": "answered",
                "answer": "Friend",
            },
        )
        assert r_ans.status_code == 200
        assert r_ans.json()["success"] is True
