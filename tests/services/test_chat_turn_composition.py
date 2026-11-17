"""A chat turn puts the person's words first and what the agent remembers
after a divider, so replies answer the message rather than the context."""
from __future__ import annotations

from services.agent import REMEMBERED_DIVIDER, USER_MESSAGE_MARKER, compose_chat_turn


def test_message_comes_first_then_divider_then_context_in_order():
    turn = compose_chat_turn("what did I say about the trip?", "## Signals\ncurious", None, "  ", "## Memory\nLisbon")
    assert turn.startswith(f"{USER_MESSAGE_MARKER}\nwhat did I say about the trip?")
    assert turn.index(REMEMBERED_DIVIDER) < turn.index("## Signals") < turn.index("## Memory")
    assert turn.count(REMEMBERED_DIVIDER) == 1


def test_no_context_means_no_divider():
    assert compose_chat_turn("hi", None, "") == f"{USER_MESSAGE_MARKER}\nhi"
