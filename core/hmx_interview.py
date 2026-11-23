"""HMX onboarding interviews (#97): a use case fully defined in one HMX file.

An imported HMX document can carry an ``onboarding_interview`` section
(schema in ``schemas/hmx-1.7.schema.json``) -- questions the agent asks
conversationally in the first chat after import (or via CLI / Web API),
each bound to an action via existing machinery (remember, ingest,
init_identity, create_goal, set_guardrail, create_scheduled_task, set_config).

Persistence, binding execution, change-journal recording (#93), and web inbox
notices live in DB functions (``db/49a_hmx_onboarding_interview.sql``) so state
survives restarts and stays consistent across CLI, chat, and web surfaces.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import asyncpg

from apps.cli_prompts import select_value, text as prompt_text

_SKIP_SENTINEL = object()

CONSEQUENTIAL_BINDING_ACTIONS = frozenset(
    {
        "ingest",
        "init_identity",
        "create_goal",
        "set_guardrail",
        "create_scheduled_task",
        "set_config",
    }
)


@dataclass(frozen=True)
class InterviewOutcome:
    """What happened running (or not running) an interview."""

    ran: bool
    completed: bool = False
    answered: int = 0
    skipped: int = 0
    failed_bindings: int = 0
    note: str | None = None


def _coerce_json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _dumps(value: Any) -> str:
    return json.dumps(value)


def summarize_binding_effect(question: dict[str, Any]) -> str:
    """Return a concise, human-readable preview of what answering this question affects."""
    binds = question.get("binds") if isinstance(question, dict) else None
    if not isinstance(binds, dict):
        return "records answer"
    action = str(binds.get("action") or "").strip()
    params = binds.get("params_template") if isinstance(binds.get("params_template"), dict) else {}

    if action == "remember":
        return "saves a memory with hmx_interview provenance"
    if action == "ingest":
        kind = str(params.get("kind") or "").strip()
        if params.get("url") or kind == "url":
            return f"queues a URL ingestion job ({params.get('url') or 'user-supplied URL'})"
        if params.get("artifact_id") or kind == "artifact":
            return "queues an artifact ingestion job"
        return "queues a text ingestion job"
    if action == "init_identity":
        fields = [k for k in ("name", "pronouns", "voice", "description", "purpose", "creator_name") if k in params]
        if fields:
            return f"updates agent identity ({', '.join(fields)})"
        return "updates agent identity"
    if action == "create_goal":
        priority = str(params.get("priority") or "active")
        return f"creates a {priority} goal"
    if action == "set_guardrail":
        if params.get("restricts_tools") or params.get("restricts_categories"):
            return "adds a tool boundary guardrail"
        return f"adds a {params.get('category') or 'boundary'} guardrail"
    if action == "create_scheduled_task":
        name = str(params.get("name") or "").strip()
        kind = str(params.get("schedule_kind") or "").strip()
        detail = ", ".join(part for part in (name, kind) if part)
        return f"creates a scheduled task ({detail})" if detail else "creates a scheduled task"
    if action == "set_config":
        key = str(params.get("key") or "").strip()
        return f"sets config key '{key}'" if key else "updates a config setting"
    return f"runs binding action '{action}'"


def resolve_choice_value(question: dict[str, Any], raw_answer: Any) -> Any:
    """For choice questions, map a matching option label back to its declared value.

    When the agent uses ``ask_user`` with option labels as choices, the user's
    selected text is the label (e.g. ``"Daily morning digest"``) while the HMX
    binding template expects the option's ``value`` (e.g. ``"daily_morning"``).
    Exact value matches pass through unchanged.
    """
    if not isinstance(question, dict) or question.get("type") != "choice":
        return raw_answer
    options = question.get("options")
    if not isinstance(options, list) or not options:
        return raw_answer
    for opt in options:
        if isinstance(opt, dict) and opt.get("value") == raw_answer:
            return opt.get("value")
    if isinstance(raw_answer, str):
        needle = raw_answer.strip().lower()
        for opt in options:
            if not isinstance(opt, dict):
                continue
            label = str(opt.get("label") or "").strip().lower()
            val_str = str(opt.get("value") or "").strip().lower()
            if needle == label or needle == val_str:
                return opt.get("value")
    return raw_answer


async def list_pending_interviews(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    """Return pending HMX onboarding interviews with their remaining unanswered questions."""
    raw = await conn.fetchval("SELECT get_pending_hmx_interviews()")
    parsed = _coerce_json(raw)
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def format_pending_interviews_prompt(interviews: list[dict[str, Any]]) -> str:
    """Format pending onboarding interviews for first-chat conversational delivery."""
    active = [
        iv for iv in interviews
        if isinstance(iv, dict) and iv.get("remaining_questions")
    ]
    if not active:
        return ""

    lines = [
        "## Pending HMX Onboarding Interview",
        (
            "An imported HMX use case has onboarding questions waiting for the user. "
            "Weave these naturally into the conversation in your own voice (not as a cold form). "
            "For `choice` questions, call `ask_user` with the option labels as `choices` (up to 4 choices; "
            "include `allow_free_text=true` if skippable or if there are more than 4 options) so the UI renders buttons "
            "or a terminal picker. For `freeform` questions, ask conversationally (or via `ask_user`) and extract the "
            "clean value from the user's reply before calling `record_hmx_interview_answer`. "
            "Respect `skippable: true` by letting the user pass (`status='skipped'`). "
            "For consequential bindings (identity, goals, guardrails, ingestion, scheduled tasks, config), "
            "tell the user plainly what their choice will configure."
        ),
    ]
    for iv in active[:3]:
        export_id = str(iv.get("export_id") or "")
        remaining = iv.get("remaining_questions") or []
        lines.append(f"\n### Import `{export_id}` ({len(remaining)} question(s) remaining)")
        for q in remaining[:12]:
            if not isinstance(q, dict):
                continue
            qid = str(q.get("id") or "")
            qtype = str(q.get("type") or "freeform")
            prompt = str(q.get("prompt") or "")
            required = bool(q.get("required", False))
            skippable = bool(q.get("skippable", not required))
            effect = summarize_binding_effect(q)
            flags = "required" if required and not skippable else "skippable"
            entry = f"- `{qid}` ({qtype}, {flags}; {effect}): {prompt}"
            if qtype == "choice" and isinstance(q.get("options"), list):
                opts = ", ".join(
                    f"{opt.get('label')!r} -> {opt.get('value')!r}"
                    for opt in q["options"]
                    if isinstance(opt, dict)
                )
                if opts:
                    entry += f" | Options: {opts}"
            lines.append(entry)
    return "\n".join(lines)


async def _run_interview_questions_cli(
    conn: asyncpg.Connection,
    export_id: str,
    questions: list[dict[str, Any]],
    answered_ids: set[str],
) -> InterviewOutcome:
    answered = skipped = failed_bindings = 0
    print()
    print("This use case has a few questions for you.")
    for question in questions:
        if question["id"] in answered_ids:
            continue
        action = str((question.get("binds") or {}).get("action") or "")
        if action in CONSEQUENTIAL_BINDING_ACTIONS:
            print(f"  [Effect: {summarize_binding_effect(question)}]")
        outcome = await _ask_one(question)
        if outcome is _SKIP_SENTINEL:
            await conn.fetchval(
                "SELECT record_hmx_interview_answer($1, $2, 'skipped')",
                export_id,
                question["id"],
            )
            skipped += 1
            continue
        result = _coerce_json(
            await conn.fetchval(
                "SELECT record_hmx_interview_answer($1, $2, 'answered', $3::jsonb)",
                export_id,
                question["id"],
                _dumps(outcome),
            )
        )
        answered += 1
        binding = result.get("binding_result") or {}
        if not binding.get("success", True):
            failed_bindings += 1
            print(f"  (could not apply: {binding.get('error', 'unknown error')})")

    final_state = _coerce_json(await conn.fetchval("SELECT get_hmx_interview_state($1)", export_id))
    return InterviewOutcome(
        ran=True,
        completed=final_state["status"] == "completed",
        answered=answered,
        skipped=skipped,
        failed_bindings=failed_bindings,
    )


async def maybe_run_cli_interview(
    conn: asyncpg.Connection,
    document: dict[str, Any],
    *,
    interactive: bool,
) -> InterviewOutcome:
    """Run the document's onboarding_interview, if it has one.

    Returns ``ran=False`` when there is no interview section, or when
    ``interactive`` is False (e.g. ``--json`` output, or no tty) -- in the
    latter case the interview state is still started so it can be resumed
    via ``hexis interview`` or the first chat, and the caller is told so via ``note``.
    """
    interview = document.get("onboarding_interview")
    if not interview or not interview.get("questions"):
        return InterviewOutcome(ran=False)

    export_id = document["export_id"]
    state = _coerce_json(
        await conn.fetchval(
            "SELECT start_hmx_interview($1, $2::jsonb)",
            export_id,
            _dumps(interview),
        )
    )

    if not interactive:
        return InterviewOutcome(
            ran=False,
            note=(
                f"This import includes an onboarding interview ({len(interview['questions'])} "
                "question(s)) that was not run (non-interactive session). Its state was "
                f"recorded under export_id {export_id}. Resume it with "
                f"`hexis interview --export-id {export_id}` or by starting your next chat."
            ),
        )

    if state["status"] == "completed":
        return InterviewOutcome(ran=False, note="This import's onboarding interview was already completed.")

    answered_ids = {a["question_id"] for a in state["answers"] if a["status"] in ("answered", "skipped")}
    return await _run_interview_questions_cli(conn, export_id, interview["questions"], answered_ids)


async def run_pending_cli_interviews(
    conn: asyncpg.Connection,
    *,
    export_id: str | None = None,
    interactive: bool = True,
) -> tuple[list[dict[str, Any]], InterviewOutcome]:
    """Inspect or interactively resume pending HMX onboarding interviews."""
    if export_id:
        raw_state = await conn.fetchval("SELECT get_hmx_interview_state($1)", export_id)
        state = _coerce_json(raw_state)
        if not isinstance(state, dict):
            return [], InterviewOutcome(
                ran=False,
                note=f"No onboarding interview found for export_id {export_id}.",
            )
        interviews = [state]
    else:
        interviews = await list_pending_interviews(conn)

    if not interviews:
        return [], InterviewOutcome(ran=False, note="No pending onboarding interviews.")

    if not interactive:
        return interviews, InterviewOutcome(
            ran=False,
            note=f"{len(interviews)} pending onboarding interview(s). Run `hexis interview` in a TTY or start a chat to answer.",
        )

    total_answered = total_skipped = total_failed = 0
    ran_any = False
    all_completed = True
    for iv in interviews:
        if iv.get("status") == "completed":
            continue
        questions = iv.get("questions") or []
        answered_ids = {
            a["question_id"]
            for a in (iv.get("answers") or [])
            if isinstance(a, dict) and a.get("status") in ("answered", "skipped")
        }
        outcome = await _run_interview_questions_cli(conn, str(iv["export_id"]), questions, answered_ids)
        ran_any = ran_any or outcome.ran
        all_completed = all_completed and outcome.completed
        total_answered += outcome.answered
        total_skipped += outcome.skipped
        total_failed += outcome.failed_bindings

    if not ran_any:
        return interviews, InterviewOutcome(ran=False, note="All matching onboarding interviews are already completed.")
    return interviews, InterviewOutcome(
        ran=True,
        completed=all_completed,
        answered=total_answered,
        skipped=total_skipped,
        failed_bindings=total_failed,
    )


async def _ask_one(question: dict[str, Any]) -> Any:
    """Ask one question; returns the answer value, or _SKIP_SENTINEL."""
    skippable = bool(question.get("skippable", not question.get("required", False)))
    prompt = question["prompt"]

    if question["type"] == "choice":
        pairs = [(opt["label"], opt["value"]) for opt in question.get("options", [])]
        if skippable:
            pairs = [*pairs, ("(skip this question)", _SKIP_SENTINEL)]
        return await select_value(prompt, pairs)

    # freeform
    while True:
        answer = await prompt_text(prompt)
        answer = (answer or "").strip()
        if answer:
            return answer
        if skippable:
            return _SKIP_SENTINEL
        print("  This one needs an answer.")
