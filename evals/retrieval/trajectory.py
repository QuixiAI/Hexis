"""Scripted RLM helpers for retrieval trajectory evals.

These evals are deterministic: the model side is scripted, while the RLM loop,
REPL, source-document syscalls, RecMem desk, and metrics are real. That gives
CI a stable policy-level harness today, and leaves a clean insertion point for
live-model trajectory grading later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScriptedRLM:
    """Async callable that returns pre-planned RLM responses in order."""

    responses: list[str]
    prompts: list[list[dict[str, str]]] = field(default_factory=list)

    async def __call__(
        self,
        messages: list[dict[str, str]],
        llm_config: dict[str, Any],
        max_tokens: int = 4096,
        **_kwargs: Any,
    ) -> str:
        self.prompts.append(messages)
        if not self.responses:
            raise AssertionError("ScriptedRLM ran out of responses")
        return self.responses.pop(0)


def repl_block(code: str) -> str:
    """Wrap code for an RLM REPL iteration."""
    return f"```repl\n{code.strip()}\n```"


def final_var(name: str = "final_answer") -> str:
    """Return a FINAL_VAR response for a variable set in a previous REPL step."""
    return f"FINAL_VAR({name})"


def retrieval_calls(metrics: dict[str, Any]) -> int:
    """Count all retrieval syscalls represented in a run_chat_turn metrics dict."""
    names = (
        "search_count",
        "fetch_count",
        "document_search_count",
        "document_fetch_count",
        "document_load_count",
        "document_chunk_search_count",
        "document_chunk_fetch_count",
        "document_chunk_load_count",
        "desk_list_count",
        "desk_fetch_count",
    )
    return sum(int(metrics.get(name) or 0) for name in names)
