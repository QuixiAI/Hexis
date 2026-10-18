"""Parallel tool calls on the ChatGPT stream keep their arguments."""
from __future__ import annotations

import core.llm  # noqa: F401  (applies the patch)
from litellm.completion_extras.litellm_responses_transformation.transformation import (
    OpenAiResponsesToChatCompletionStreamIterator as Iterator,
)


def _iterator() -> Iterator:
    it = Iterator.__new__(Iterator)
    it._tool_call_index_map = {}
    it._chat_completion_id = None
    return it


def _arguments(it: Iterator, events: list[dict]) -> dict[int, str]:
    out: dict[int, str] = {}
    for event in events:
        for choice in it.chunk_parser(event).choices:
            for call in choice.delta.tool_calls or []:
                if call.function.arguments:
                    out[call.index] = out.get(call.index, "") + call.function.arguments
    return out


def _call(i: int, name: str, args: str = "") -> dict:
    return {"type": "function_call", "id": f"fc_{i}", "call_id": f"call_{i}", "name": name, "arguments": args}


def test_arguments_sent_only_when_each_call_finishes_are_kept():
    events = [
        {"type": "response.output_item.added", "output_index": 1, "item": _call(1, "recall")},
        {"type": "response.output_item.added", "output_index": 2, "item": _call(2, "search_history")},
        {"type": "response.output_item.done", "output_index": 1, "item": _call(1, "recall", '{"query": "flowers"}')},
        {"type": "response.output_item.done", "output_index": 2, "item": _call(2, "search_history", '{"query": "flowers"}')},
    ]
    assert _arguments(_iterator(), events) == {0: '{"query": "flowers"}', 1: '{"query": "flowers"}'}


def test_streamed_arguments_are_not_sent_twice():
    events = [
        {"type": "response.output_item.added", "output_index": 0, "item": _call(0, "recall")},
        {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": '{"query": '},
        {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": '"flowers"}'},
        {"type": "response.output_item.done", "output_index": 0, "item": _call(0, "recall", '{"query": "flowers"}')},
    ]
    assert _arguments(_iterator(), events) == {0: '{"query": "flowers"}'}
