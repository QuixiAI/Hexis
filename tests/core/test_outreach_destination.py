"""Messages for the user go where Settings says, not to whoever talked last."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core import outreach_destination
from core.outreach_destination import pick

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


def _state(destination: str, available: list[str]) -> dict:
    return {
        "destination": destination,
        "available": available,
        "channels": {
            "slack": {"ready": "slack" in available, "reachable": "slack" in available, "user_id": "U1",
                      "name": "Ana", "source": "set"},
            "telegram": {"ready": "telegram" in available, "reachable": "telegram" in available,
                         "chat_id": "42", "name": "Ana"},
            "email": {"ready": "email" in available, "reachable": "email" in available,
                      "address": "ana@example.com"},
        },
    }


async def test_auto_uses_the_only_channel_set_up():
    assert pick(_state("auto", ["telegram"])) == "telegram"
    assert pick(_state("auto", ["telegram"]), requested="slack") == "telegram"


async def test_auto_honours_the_requested_channel_when_available():
    assert pick(_state("auto", ["slack", "email"]), requested="email") == "email"
    assert pick(_state("auto", ["slack", "email"])) == "slack"


async def test_a_fixed_channel_wins_and_falls_back_to_the_inbox_when_not_set_up():
    assert pick(_state("email", ["slack", "email"]), requested="slack") == "email"
    assert pick(_state("email", ["slack"])) == "inbox"
    assert pick(_state("inbox", ["slack", "email"]), requested="slack") == "inbox"
    assert pick(_state("auto", [])) == "inbox"


async def test_a_message_for_no_one_in_particular_goes_to_the_user(monkeypatch):
    from channels.outbox import ChannelOutboxConsumer

    pool = MagicMock()
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    manager = MagicMock()
    manager.send = AsyncMock(return_value="sent-1")
    consumer = ChannelOutboxConsumer(manager, pool)

    async def choose(_pool, requested=None):
        return "slack", _state("auto", ["slack"])

    sent: dict = {}

    async def send(**kwargs):
        sent.update(kwargs)
        return True

    monkeypatch.setattr(outreach_destination, "choose", choose)
    monkeypatch.setattr(consumer, "_send_with_obligation", send)
    await consumer._deliver_last_active("Finished the summary.", "Finished the summary.", {"message": "x"}, "m1")
    assert sent["channel_type"] == "slack"
    assert sent["channel_id"] == "U1"  # the adapter opens the DM
    assert sent["delivery_mode"] == "user"


async def test_the_user_can_keep_messages_in_the_dashboard(monkeypatch):
    from channels.outbox import ChannelOutboxConsumer

    consumer = ChannelOutboxConsumer(MagicMock(), MagicMock())

    async def choose(_pool, requested=None):
        return "inbox", _state("inbox", ["slack"])

    send = AsyncMock()
    monkeypatch.setattr(outreach_destination, "choose", choose)
    monkeypatch.setattr(consumer, "_send_with_obligation", send)
    await consumer._deliver_last_active("Hello", "Hello", {}, "m2")
    send.assert_not_called()


async def test_setting_and_reading_the_destination(db_pool):
    try:
        state = await outreach_destination.save(db_pool, destination="inbox")
        assert state["destination"] == "inbox"
        state = await outreach_destination.state(db_pool)
        assert state["destination"] == "inbox"
        assert set(state["channels"]) == {"slack", "telegram", "email"}
        with pytest.raises(ValueError):
            await outreach_destination.save(db_pool, destination="carrier_pigeon")
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM config WHERE key = $1", outreach_destination.DESTINATION_KEY)
