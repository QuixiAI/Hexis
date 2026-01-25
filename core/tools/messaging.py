"""
Hexis Tools System - Messaging Integrations

Provides messaging tools for Discord, Slack, and Telegram.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .base import (
    ToolCategory,
    ToolContext,
    ToolErrorType,
    ToolExecutionContext,
    ToolHandler,
    ToolResult,
    ToolSpec,
)

logger = logging.getLogger(__name__)


class DiscordSendHandler(ToolHandler):
    """Send messages to Discord via webhook or bot API."""

    def __init__(
        self,
        config_resolver: Callable[[], dict[str, Any] | None] | None = None,
    ):
        """
        Initialize the handler.

        Args:
            config_resolver: Callable that returns Discord configuration dict with keys:
                - bot_token: Discord bot token (for bot API)
                - webhook_url: Discord webhook URL (alternative to bot)
        """
        self._config_resolver = config_resolver

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="discord_send",
            description="Send a message to a Discord channel. Use for notifications, updates, or reaching out.",
            parameters={
                "type": "object",
                "properties": {
                    "channel_id": {
                        "type": "string",
                        "description": "Discord channel ID (required for bot API)",
                    },
                    "message": {
                        "type": "string",
                        "description": "Message content",
                    },
                    "webhook_url": {
                        "type": "string",
                        "description": "Webhook URL (overrides default, optional)",
                    },
                    "username": {
                        "type": "string",
                        "description": "Override bot username (webhook only)",
                    },
                    "embed": {
                        "type": "object",
                        "description": "Discord embed object (optional)",
                    },
                },
                "required": ["message"],
            },
            category=ToolCategory.MESSAGING,
            energy_cost=5,
            is_read_only=False,
            requires_approval=True,
            allowed_contexts={ToolContext.HEARTBEAT, ToolContext.CHAT},
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        try:
            import aiohttp
        except ImportError:
            return ToolResult(
                success=False,
                output=None,
                error="aiohttp not installed",
                error_type=ToolErrorType.MISSING_DEPENDENCY,
            )

        config = {}
        if self._config_resolver:
            config = self._config_resolver() or {}

        message = arguments["message"]
        channel_id = arguments.get("channel_id")
        webhook_url = arguments.get("webhook_url") or config.get("webhook_url")
        username = arguments.get("username")
        embed = arguments.get("embed")
        bot_token = config.get("bot_token")

        # Prefer webhook if available
        if webhook_url:
            return await self._send_webhook(webhook_url, message, username, embed)
        elif bot_token and channel_id:
            return await self._send_bot(bot_token, channel_id, message, embed)
        else:
            return ToolResult(
                success=False,
                output=None,
                error="Discord not configured. Provide webhook_url or bot_token + channel_id",
                error_type=ToolErrorType.AUTH_FAILED,
            )

    async def _send_webhook(
        self,
        webhook_url: str,
        message: str,
        username: str | None,
        embed: dict | None,
    ) -> ToolResult:
        import aiohttp

        payload: dict[str, Any] = {"content": message}
        if username:
            payload["username"] = username
        if embed:
            payload["embeds"] = [embed]

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json=payload) as resp:
                    if resp.status not in (200, 204):
                        error_text = await resp.text()
                        return ToolResult(
                            success=False,
                            output=None,
                            error=f"Discord webhook error ({resp.status}): {error_text}",
                            error_type=ToolErrorType.EXECUTION_FAILED,
                        )

            return ToolResult(
                success=True,
                output={"sent": True, "method": "webhook"},
                display_output=f"Discord message sent: {message[:50]}...",
            )
        except Exception as e:
            logger.exception("Discord webhook error")
            return ToolResult(
                success=False,
                output=None,
                error=f"Discord webhook failed: {str(e)}",
                error_type=ToolErrorType.EXECUTION_FAILED,
            )

    async def _send_bot(
        self,
        bot_token: str,
        channel_id: str,
        message: str,
        embed: dict | None,
    ) -> ToolResult:
        import aiohttp

        payload: dict[str, Any] = {"content": message}
        if embed:
            payload["embeds"] = [embed]

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"https://discord.com/api/v10/channels/{channel_id}/messages",
                    json=payload,
                    headers={"Authorization": f"Bot {bot_token}"},
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        return ToolResult(
                            success=False,
                            output=None,
                            error=f"Discord API error ({resp.status}): {error_text}",
                            error_type=ToolErrorType.EXECUTION_FAILED,
                        )

                    data = await resp.json()

            return ToolResult(
                success=True,
                output={"sent": True, "method": "bot", "message_id": data.get("id")},
                display_output=f"Discord message sent to channel {channel_id}",
            )
        except Exception as e:
            logger.exception("Discord bot error")
            return ToolResult(
                success=False,
                output=None,
                error=f"Discord bot API failed: {str(e)}",
                error_type=ToolErrorType.EXECUTION_FAILED,
            )


class SlackSendHandler(ToolHandler):
    """Send messages to Slack via webhook or API."""

    def __init__(
        self,
        config_resolver: Callable[[], dict[str, Any] | None] | None = None,
    ):
        """
        Initialize the handler.

        Args:
            config_resolver: Callable that returns Slack configuration dict with keys:
                - bot_token: Slack bot OAuth token
                - webhook_url: Slack incoming webhook URL
        """
        self._config_resolver = config_resolver

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="slack_send",
            description="Send a message to a Slack channel. Use for notifications, updates, or team communication.",
            parameters={
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "string",
                        "description": "Slack channel ID or name (e.g., #general or C01234567)",
                    },
                    "message": {
                        "type": "string",
                        "description": "Message text",
                    },
                    "webhook_url": {
                        "type": "string",
                        "description": "Webhook URL (overrides default, optional)",
                    },
                    "blocks": {
                        "type": "array",
                        "description": "Slack Block Kit blocks (optional)",
                    },
                    "thread_ts": {
                        "type": "string",
                        "description": "Thread timestamp to reply in thread",
                    },
                },
                "required": ["message"],
            },
            category=ToolCategory.MESSAGING,
            energy_cost=5,
            is_read_only=False,
            requires_approval=True,
            allowed_contexts={ToolContext.HEARTBEAT, ToolContext.CHAT},
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        try:
            import aiohttp
        except ImportError:
            return ToolResult(
                success=False,
                output=None,
                error="aiohttp not installed",
                error_type=ToolErrorType.MISSING_DEPENDENCY,
            )

        config = {}
        if self._config_resolver:
            config = self._config_resolver() or {}

        message = arguments["message"]
        channel = arguments.get("channel")
        webhook_url = arguments.get("webhook_url") or config.get("webhook_url")
        blocks = arguments.get("blocks")
        thread_ts = arguments.get("thread_ts")
        bot_token = config.get("bot_token")

        # Prefer webhook if available
        if webhook_url:
            return await self._send_webhook(webhook_url, message, blocks)
        elif bot_token and channel:
            return await self._send_api(bot_token, channel, message, blocks, thread_ts)
        else:
            return ToolResult(
                success=False,
                output=None,
                error="Slack not configured. Provide webhook_url or bot_token + channel",
                error_type=ToolErrorType.AUTH_FAILED,
            )

    async def _send_webhook(
        self,
        webhook_url: str,
        message: str,
        blocks: list | None,
    ) -> ToolResult:
        import aiohttp

        payload: dict[str, Any] = {"text": message}
        if blocks:
            payload["blocks"] = blocks

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json=payload) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        return ToolResult(
                            success=False,
                            output=None,
                            error=f"Slack webhook error: {error_text}",
                            error_type=ToolErrorType.EXECUTION_FAILED,
                        )

            return ToolResult(
                success=True,
                output={"sent": True, "method": "webhook"},
                display_output=f"Slack message sent: {message[:50]}...",
            )
        except Exception as e:
            logger.exception("Slack webhook error")
            return ToolResult(
                success=False,
                output=None,
                error=f"Slack webhook failed: {str(e)}",
                error_type=ToolErrorType.EXECUTION_FAILED,
            )

    async def _send_api(
        self,
        bot_token: str,
        channel: str,
        message: str,
        blocks: list | None,
        thread_ts: str | None,
    ) -> ToolResult:
        import aiohttp

        payload: dict[str, Any] = {
            "channel": channel,
            "text": message,
        }
        if blocks:
            payload["blocks"] = blocks
        if thread_ts:
            payload["thread_ts"] = thread_ts

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://slack.com/api/chat.postMessage",
                    json=payload,
                    headers={"Authorization": f"Bearer {bot_token}"},
                ) as resp:
                    data = await resp.json()

                    if not data.get("ok"):
                        return ToolResult(
                            success=False,
                            output=None,
                            error=f"Slack API error: {data.get('error')}",
                            error_type=ToolErrorType.EXECUTION_FAILED,
                        )

            return ToolResult(
                success=True,
                output={
                    "sent": True,
                    "method": "api",
                    "ts": data.get("ts"),
                    "channel": data.get("channel"),
                },
                display_output=f"Slack message sent to {channel}",
            )
        except Exception as e:
            logger.exception("Slack API error")
            return ToolResult(
                success=False,
                output=None,
                error=f"Slack API failed: {str(e)}",
                error_type=ToolErrorType.EXECUTION_FAILED,
            )


class TelegramSendHandler(ToolHandler):
    """Send messages via Telegram Bot API."""

    def __init__(
        self,
        config_resolver: Callable[[], dict[str, Any] | None] | None = None,
    ):
        """
        Initialize the handler.

        Args:
            config_resolver: Callable that returns Telegram configuration dict with keys:
                - bot_token: Telegram bot token from BotFather
                - default_chat_id: Default chat ID to send to
        """
        self._config_resolver = config_resolver

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="telegram_send",
            description="Send a message via Telegram. Use for notifications, alerts, or personal outreach.",
            parameters={
                "type": "object",
                "properties": {
                    "chat_id": {
                        "type": "string",
                        "description": "Telegram chat ID (user, group, or channel)",
                    },
                    "message": {
                        "type": "string",
                        "description": "Message text (supports Markdown)",
                    },
                    "parse_mode": {
                        "type": "string",
                        "enum": ["Markdown", "MarkdownV2", "HTML"],
                        "default": "Markdown",
                        "description": "Message formatting mode",
                    },
                    "disable_notification": {
                        "type": "boolean",
                        "default": False,
                        "description": "Send silently",
                    },
                    "reply_to_message_id": {
                        "type": "integer",
                        "description": "Message ID to reply to",
                    },
                },
                "required": ["message"],
            },
            category=ToolCategory.MESSAGING,
            energy_cost=5,
            is_read_only=False,
            requires_approval=True,
            allowed_contexts={ToolContext.HEARTBEAT, ToolContext.CHAT},
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        try:
            import aiohttp
        except ImportError:
            return ToolResult(
                success=False,
                output=None,
                error="aiohttp not installed",
                error_type=ToolErrorType.MISSING_DEPENDENCY,
            )

        config = {}
        if self._config_resolver:
            config = self._config_resolver() or {}

        bot_token = config.get("bot_token")
        if not bot_token:
            return ToolResult(
                success=False,
                output=None,
                error="Telegram bot token not configured",
                error_type=ToolErrorType.AUTH_FAILED,
            )

        message = arguments["message"]
        chat_id = arguments.get("chat_id") or config.get("default_chat_id")
        if not chat_id:
            return ToolResult(
                success=False,
                output=None,
                error="No chat_id provided and no default configured",
                error_type=ToolErrorType.INVALID_PARAMS,
            )

        parse_mode = arguments.get("parse_mode", "Markdown")
        disable_notification = arguments.get("disable_notification", False)
        reply_to = arguments.get("reply_to_message_id")

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": parse_mode,
            "disable_notification": disable_notification,
        }
        if reply_to:
            payload["reply_to_message_id"] = reply_to

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json=payload,
                ) as resp:
                    data = await resp.json()

                    if not data.get("ok"):
                        return ToolResult(
                            success=False,
                            output=None,
                            error=f"Telegram API error: {data.get('description')}",
                            error_type=ToolErrorType.EXECUTION_FAILED,
                        )

            result_msg = data.get("result", {})
            return ToolResult(
                success=True,
                output={
                    "sent": True,
                    "message_id": result_msg.get("message_id"),
                    "chat_id": chat_id,
                },
                display_output=f"Telegram message sent to {chat_id}",
            )
        except Exception as e:
            logger.exception("Telegram API error")
            return ToolResult(
                success=False,
                output=None,
                error=f"Telegram API failed: {str(e)}",
                error_type=ToolErrorType.EXECUTION_FAILED,
            )


def create_messaging_tools(
    discord_config_resolver: Callable[[], dict[str, Any] | None] | None = None,
    slack_config_resolver: Callable[[], dict[str, Any] | None] | None = None,
    telegram_config_resolver: Callable[[], dict[str, Any] | None] | None = None,
) -> list[ToolHandler]:
    """
    Create messaging tool handlers.

    Args:
        discord_config_resolver: Callable that returns Discord configuration dict.
        slack_config_resolver: Callable that returns Slack configuration dict.
        telegram_config_resolver: Callable that returns Telegram configuration dict.

    Returns:
        List of messaging tool handlers.
    """
    return [
        DiscordSendHandler(discord_config_resolver),
        SlackSendHandler(slack_config_resolver),
        TelegramSendHandler(telegram_config_resolver),
    ]
