"""Where the agent's messages to the user go.

Outbox messages addressed to no one in particular (a heartbeat reaching out,
``queue_user_message`` from a heartbeat) are for the user. They always land in
the dashboard inbox; this decides where else each one goes.

``outreach.destination``:

- ``auto`` (default): the agent decides. With one channel available, that
  one; with several, the channel the agent asked for (Slack, then Telegram,
  then email when it didn't ask); with none, the dashboard only.
- ``slack`` / ``telegram`` / ``email``: always that channel, when available.
- ``inbox``: the dashboard only.

Before this, such messages went to whichever chat was most recently active,
which with more than one person on a channel could be someone else's chat.

Reaching the user: in Slack, ``outreach.slack_user_id`` (falling back to the
channel's ``channel.slack.operator_user_id``), else the most recent Slack DM;
on Telegram, ``outreach.telegram_chat_id``, else the most recent Telegram
chat; by email, the connected Gmail address when Gmail may send.
"""

from __future__ import annotations

import json
from typing import Any

CHANNELS = ("slack", "telegram", "email")
DESTINATIONS = ("auto", "inbox", *CHANNELS)
DESTINATION_KEY = "outreach.destination"
SLACK_USER_KEY = "outreach.slack_user_id"
TELEGRAM_CHAT_KEY = "outreach.telegram_chat_id"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


def _text(value: Any) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            pass
    return value.strip() if isinstance(value, str) else ""


async def _config(conn: Any, key: str) -> str:
    return _text(await conn.fetchval("SELECT get_config($1)", key))


async def _channel_ready(conn: Any, channel_type: str) -> bool:
    """A channel adapter that is configured and running can carry a message."""
    row = await conn.fetchrow(
        "SELECT configured, running FROM channel_adapter_runtime WHERE channel_type = $1",
        channel_type,
    )
    return bool(row and row["configured"] and row["running"])


async def _gmail_sender(conn: Any) -> str | None:
    """The connected Gmail address, when the connection may send mail."""
    row = await conn.fetchrow(
        """
        SELECT account_key, granted_scopes
        FROM integration_connections
        WHERE connector_id = 'gmail' AND status = 'connected'
        ORDER BY connected_at DESC NULLS LAST
        LIMIT 1
        """
    )
    if not row or GMAIL_SEND_SCOPE not in list(row["granted_scopes"] or []):
        return None
    address = str(row["account_key"] or "").strip()
    return address if "@" in address else None


async def state(pool: Any) -> dict[str, Any]:
    """The setting, and how (whether) each channel reaches the user."""
    async with pool.acquire() as conn:
        destination = await _config(conn, DESTINATION_KEY) or "auto"
        if destination not in DESTINATIONS:
            destination = "auto"
        slack_ready = await _channel_ready(conn, "slack")
        telegram_ready = await _channel_ready(conn, "telegram")

        slack_user = await _config(conn, SLACK_USER_KEY)
        slack_source = "set" if slack_user else None
        if not slack_user:
            slack_user = await _config(conn, "channel.slack.operator_user_id")
            slack_source = "channel_setting" if slack_user else None
        if not slack_user:
            # The most recent Slack DM: a companion usually talks to one person.
            slack_user = await conn.fetchval(
                """
                SELECT sender_id FROM channel_sessions
                WHERE channel_type = 'slack' AND channel_id LIKE 'D%'
                ORDER BY last_active DESC NULLS LAST LIMIT 1
                """
            )
            slack_source = "recent_dm" if slack_user else None
        slack_name = None
        if slack_user:
            slack_name = await conn.fetchval(
                """
                SELECT sender_name FROM channel_sessions
                WHERE channel_type = 'slack' AND sender_id = $1
                ORDER BY last_active DESC NULLS LAST LIMIT 1
                """,
                slack_user,
            )

        telegram_chat = await _config(conn, TELEGRAM_CHAT_KEY)
        telegram_name = None
        if not telegram_chat:
            row = await conn.fetchrow(
                """
                SELECT channel_id, sender_name FROM channel_sessions
                WHERE channel_type = 'telegram'
                ORDER BY last_active DESC NULLS LAST LIMIT 1
                """
            )
            if row:
                telegram_chat, telegram_name = row["channel_id"], row["sender_name"]

        email = await _gmail_sender(conn)

    channels = {
        "slack": {"ready": slack_ready, "reachable": slack_ready and bool(slack_user),
                  "user_id": slack_user or None, "name": slack_name, "source": slack_source},
        "telegram": {"ready": telegram_ready, "reachable": telegram_ready and bool(telegram_chat),
                     "chat_id": telegram_chat or None, "name": telegram_name},
        "email": {"ready": bool(email), "reachable": bool(email), "address": email},
    }
    available = [c for c in CHANNELS if channels[c]["reachable"]]
    return {"destination": destination, "available": available, "channels": channels}


def pick(current: dict[str, Any], requested: str | None = None) -> str:
    """The channel one message goes to: a channel name, or "inbox"."""
    available = current["available"]
    destination = current["destination"]
    if destination == "inbox":
        return "inbox"
    if destination in CHANNELS:
        return destination if destination in available else "inbox"
    if requested in available:
        return requested
    return available[0] if available else "inbox"


async def choose(pool: Any, requested: str | None = None) -> tuple[str, dict[str, Any]]:
    current = await state(pool)
    return pick(current, requested), current


async def save(
    pool: Any,
    *,
    destination: str | None = None,
    slack_user_id: str | None = None,
    telegram_chat_id: str | None = None,
) -> dict[str, Any]:
    if destination is not None and destination not in DESTINATIONS:
        raise ValueError(f"destination must be one of {', '.join(DESTINATIONS)}")
    async with pool.acquire() as conn:
        if destination is not None:
            await conn.execute("SELECT set_config($1, $2::jsonb)", DESTINATION_KEY, json.dumps(destination))
        if slack_user_id is not None:
            await conn.execute("SELECT set_config($1, $2::jsonb)", SLACK_USER_KEY, json.dumps(slack_user_id.strip()))
        if telegram_chat_id is not None:
            await conn.execute(
                "SELECT set_config($1, $2::jsonb)", TELEGRAM_CHAT_KEY, json.dumps(telegram_chat_id.strip())
            )
    return await state(pool)
