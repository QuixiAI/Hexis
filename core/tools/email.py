"""
Hexis Tools System - Email Integration

Provides email tools for sending messages.
Supports SMTP and API-based sending (SendGrid, Mailgun, etc.).
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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


class EmailSendHandler(ToolHandler):
    """Send email via SMTP."""

    def __init__(
        self,
        config_resolver: Callable[[], dict[str, Any] | None] | None = None,
    ):
        """
        Initialize the handler.

        Args:
            config_resolver: Callable that returns SMTP configuration dict with keys:
                - smtp_host: SMTP server hostname
                - smtp_port: SMTP server port (default: 587)
                - smtp_user: SMTP username
                - smtp_password: SMTP password
                - from_email: Default sender email
                - from_name: Default sender name (optional)
        """
        self._config_resolver = config_resolver

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="email_send",
            description="Send an email message. Use for important communications, notifications, or outreach.",
            parameters={
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Recipient email address",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject line",
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body (plain text)",
                    },
                    "html_body": {
                        "type": "string",
                        "description": "Email body (HTML, optional)",
                    },
                    "cc": {
                        "type": "string",
                        "description": "CC recipients (comma-separated)",
                    },
                    "reply_to": {
                        "type": "string",
                        "description": "Reply-to address (optional)",
                    },
                },
                "required": ["to", "subject", "body"],
            },
            category=ToolCategory.EMAIL,
            energy_cost=4,
            is_read_only=False,
            requires_approval=True,
            allowed_contexts={ToolContext.HEARTBEAT, ToolContext.CHAT},
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        config = None
        if self._config_resolver:
            config = self._config_resolver()

        if not config:
            return ToolResult(
                success=False,
                output=None,
                error="Email configuration not set. Configure SMTP settings via 'hexis tools set-api-key email_send EMAIL_CONFIG'",
                error_type=ToolErrorType.AUTH_FAILED,
            )

        smtp_host = config.get("smtp_host")
        smtp_port = config.get("smtp_port", 587)
        smtp_user = config.get("smtp_user")
        smtp_password = config.get("smtp_password")
        from_email = config.get("from_email")
        from_name = config.get("from_name", "")

        if not all([smtp_host, smtp_user, smtp_password, from_email]):
            return ToolResult(
                success=False,
                output=None,
                error="Incomplete SMTP configuration. Required: smtp_host, smtp_user, smtp_password, from_email",
                error_type=ToolErrorType.INVALID_PARAMS,
            )

        to_email = arguments["to"]
        subject = arguments["subject"]
        body = arguments["body"]
        html_body = arguments.get("html_body")
        cc = arguments.get("cc")
        reply_to = arguments.get("reply_to")

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
            msg["To"] = to_email

            if cc:
                msg["Cc"] = cc
            if reply_to:
                msg["Reply-To"] = reply_to

            # Attach plain text
            msg.attach(MIMEText(body, "plain"))

            # Attach HTML if provided
            if html_body:
                msg.attach(MIMEText(html_body, "html"))

            # Send via SMTP
            ssl_context = ssl.create_default_context()

            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls(context=ssl_context)
                server.login(smtp_user, smtp_password)

                recipients = [to_email]
                if cc:
                    recipients.extend([e.strip() for e in cc.split(",")])

                server.sendmail(from_email, recipients, msg.as_string())

            return ToolResult(
                success=True,
                output={
                    "to": to_email,
                    "subject": subject,
                    "sent": True,
                },
                display_output=f"Email sent to {to_email}: {subject}",
            )

        except smtplib.SMTPAuthenticationError as e:
            logger.exception("SMTP auth error")
            return ToolResult(
                success=False,
                output=None,
                error=f"SMTP authentication failed: {str(e)}",
                error_type=ToolErrorType.AUTH_FAILED,
            )
        except smtplib.SMTPException as e:
            logger.exception("SMTP error")
            return ToolResult(
                success=False,
                output=None,
                error=f"SMTP error: {str(e)}",
                error_type=ToolErrorType.EXECUTION_FAILED,
            )
        except Exception as e:
            logger.exception("Email send error")
            return ToolResult(
                success=False,
                output=None,
                error=f"Failed to send email: {str(e)}",
                error_type=ToolErrorType.EXECUTION_FAILED,
            )


class SendGridEmailHandler(ToolHandler):
    """Send email via SendGrid API."""

    def __init__(
        self,
        api_key_resolver: Callable[[], str | None] | None = None,
        from_email: str | None = None,
    ):
        self._api_key_resolver = api_key_resolver
        self._from_email = from_email

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="email_send_sendgrid",
            description="Send an email via SendGrid API. Alternative to SMTP-based sending.",
            parameters={
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Recipient email address",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject line",
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body (plain text)",
                    },
                    "html_body": {
                        "type": "string",
                        "description": "Email body (HTML, optional)",
                    },
                    "from_email": {
                        "type": "string",
                        "description": "Sender email (if different from default)",
                    },
                },
                "required": ["to", "subject", "body"],
            },
            category=ToolCategory.EMAIL,
            energy_cost=4,
            is_read_only=False,
            requires_approval=True,
            allowed_contexts={ToolContext.HEARTBEAT, ToolContext.CHAT},
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        api_key = None
        if self._api_key_resolver:
            api_key = self._api_key_resolver()

        if not api_key:
            return ToolResult(
                success=False,
                output=None,
                error="SendGrid API key not configured",
                error_type=ToolErrorType.AUTH_FAILED,
            )

        try:
            import aiohttp
        except ImportError:
            return ToolResult(
                success=False,
                output=None,
                error="aiohttp not installed",
                error_type=ToolErrorType.MISSING_DEPENDENCY,
            )

        to_email = arguments["to"]
        subject = arguments["subject"]
        body = arguments["body"]
        html_body = arguments.get("html_body")
        from_email = arguments.get("from_email") or self._from_email

        if not from_email:
            return ToolResult(
                success=False,
                output=None,
                error="No sender email configured",
                error_type=ToolErrorType.INVALID_PARAMS,
            )

        try:
            content = [{"type": "text/plain", "value": body}]
            if html_body:
                content.append({"type": "text/html", "value": html_body})

            payload = {
                "personalizations": [{"to": [{"email": to_email}]}],
                "from": {"email": from_email},
                "subject": subject,
                "content": content,
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.sendgrid.com/v3/mail/send",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                ) as resp:
                    if resp.status not in (200, 202):
                        error_text = await resp.text()
                        return ToolResult(
                            success=False,
                            output=None,
                            error=f"SendGrid API error ({resp.status}): {error_text}",
                            error_type=ToolErrorType.EXECUTION_FAILED,
                        )

            return ToolResult(
                success=True,
                output={
                    "to": to_email,
                    "subject": subject,
                    "sent": True,
                },
                display_output=f"Email sent to {to_email}: {subject}",
            )

        except Exception as e:
            logger.exception("SendGrid send error")
            return ToolResult(
                success=False,
                output=None,
                error=f"Failed to send email: {str(e)}",
                error_type=ToolErrorType.EXECUTION_FAILED,
            )


def create_email_tools(
    smtp_config_resolver: Callable[[], dict[str, Any] | None] | None = None,
    sendgrid_api_key_resolver: Callable[[], str | None] | None = None,
    sendgrid_from_email: str | None = None,
) -> list[ToolHandler]:
    """
    Create email tool handlers.

    Args:
        smtp_config_resolver: Callable that returns SMTP configuration dict.
        sendgrid_api_key_resolver: Callable that returns SendGrid API key.
        sendgrid_from_email: Default sender email for SendGrid.

    Returns:
        List of email tool handlers.
    """
    tools = [EmailSendHandler(smtp_config_resolver)]

    # Only add SendGrid if API key resolver is provided
    if sendgrid_api_key_resolver:
        tools.append(SendGridEmailHandler(sendgrid_api_key_resolver, sendgrid_from_email))

    return tools
