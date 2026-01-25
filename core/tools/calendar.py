"""
Hexis Tools System - Calendar Integration

Provides calendar tools for viewing and creating events.
Supports Google Calendar via API.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
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


class GoogleCalendarHandler(ToolHandler):
    """List upcoming calendar events from Google Calendar."""

    def __init__(
        self,
        credentials_resolver: Callable[[], dict[str, Any] | None] | None = None,
    ):
        """
        Initialize the handler.

        Args:
            credentials_resolver: Callable that returns Google OAuth credentials dict,
                                  or None if not configured.
        """
        self._credentials_resolver = credentials_resolver

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="calendar_events",
            description="List upcoming calendar events. Use to check schedule, find free time, or see what's coming up.",
            parameters={
                "type": "object",
                "properties": {
                    "days_ahead": {
                        "type": "integer",
                        "default": 7,
                        "minimum": 1,
                        "maximum": 30,
                        "description": "Number of days to look ahead",
                    },
                    "max_results": {
                        "type": "integer",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 50,
                        "description": "Maximum number of events to return",
                    },
                    "calendar_id": {
                        "type": "string",
                        "default": "primary",
                        "description": "Calendar ID (default: primary)",
                    },
                },
            },
            category=ToolCategory.CALENDAR,
            energy_cost=2,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        credentials = None
        if self._credentials_resolver:
            credentials = self._credentials_resolver()

        if not credentials:
            return ToolResult(
                success=False,
                output=None,
                error="Google Calendar credentials not configured. Set up OAuth credentials via 'hexis tools set-api-key calendar_events GOOGLE_CALENDAR_CREDENTIALS'",
                error_type=ToolErrorType.AUTH_FAILED,
            )

        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError:
            return ToolResult(
                success=False,
                output=None,
                error="google-api-python-client not installed. Run: pip install google-api-python-client google-auth",
                error_type=ToolErrorType.MISSING_DEPENDENCY,
            )

        days_ahead = arguments.get("days_ahead", 7)
        max_results = arguments.get("max_results", 10)
        calendar_id = arguments.get("calendar_id", "primary")

        try:
            creds = Credentials.from_authorized_user_info(credentials)
            service = build("calendar", "v3", credentials=creds)

            now = datetime.utcnow()
            time_min = now.isoformat() + "Z"
            time_max = (now + timedelta(days=days_ahead)).isoformat() + "Z"

            events_result = (
                service.events()
                .list(
                    calendarId=calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )

            events = events_result.get("items", [])
            formatted_events = []

            for event in events:
                start = event["start"].get("dateTime", event["start"].get("date"))
                end = event["end"].get("dateTime", event["end"].get("date"))
                formatted_events.append({
                    "id": event.get("id"),
                    "summary": event.get("summary", "(No title)"),
                    "start": start,
                    "end": end,
                    "location": event.get("location"),
                    "description": event.get("description", "")[:200] if event.get("description") else None,
                })

            display_lines = []
            for evt in formatted_events:
                display_lines.append(f"- {evt['start']}: {evt['summary']}")
                if evt.get("location"):
                    display_lines.append(f"  Location: {evt['location']}")

            return ToolResult(
                success=True,
                output={
                    "calendar_id": calendar_id,
                    "events": formatted_events,
                    "count": len(formatted_events),
                },
                display_output="\n".join(display_lines) if display_lines else "No upcoming events",
            )

        except Exception as e:
            logger.exception("Calendar API error")
            return ToolResult(
                success=False,
                output=None,
                error=f"Calendar API error: {str(e)}",
                error_type=ToolErrorType.EXECUTION_FAILED,
            )


class CreateCalendarEventHandler(ToolHandler):
    """Create a new calendar event."""

    def __init__(
        self,
        credentials_resolver: Callable[[], dict[str, Any] | None] | None = None,
    ):
        self._credentials_resolver = credentials_resolver

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="calendar_create",
            description="Create a new calendar event. Use to schedule meetings, reminders, or block time.",
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Event title/summary",
                    },
                    "start": {
                        "type": "string",
                        "description": "Start time in ISO format (e.g., 2024-01-15T10:00:00)",
                    },
                    "end": {
                        "type": "string",
                        "description": "End time in ISO format (e.g., 2024-01-15T11:00:00)",
                    },
                    "description": {
                        "type": "string",
                        "description": "Event description/notes",
                    },
                    "location": {
                        "type": "string",
                        "description": "Event location",
                    },
                    "calendar_id": {
                        "type": "string",
                        "default": "primary",
                        "description": "Calendar ID (default: primary)",
                    },
                },
                "required": ["title", "start", "end"],
            },
            category=ToolCategory.CALENDAR,
            energy_cost=3,
            is_read_only=False,
            requires_approval=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        credentials = None
        if self._credentials_resolver:
            credentials = self._credentials_resolver()

        if not credentials:
            return ToolResult(
                success=False,
                output=None,
                error="Google Calendar credentials not configured",
                error_type=ToolErrorType.AUTH_FAILED,
            )

        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError:
            return ToolResult(
                success=False,
                output=None,
                error="google-api-python-client not installed",
                error_type=ToolErrorType.MISSING_DEPENDENCY,
            )

        title = arguments["title"]
        start = arguments["start"]
        end = arguments["end"]
        description = arguments.get("description")
        location = arguments.get("location")
        calendar_id = arguments.get("calendar_id", "primary")

        try:
            creds = Credentials.from_authorized_user_info(credentials)
            service = build("calendar", "v3", credentials=creds)

            event = {
                "summary": title,
                "start": {"dateTime": start, "timeZone": "UTC"},
                "end": {"dateTime": end, "timeZone": "UTC"},
            }
            if description:
                event["description"] = description
            if location:
                event["location"] = location

            created = service.events().insert(calendarId=calendar_id, body=event).execute()

            return ToolResult(
                success=True,
                output={
                    "event_id": created.get("id"),
                    "html_link": created.get("htmlLink"),
                    "summary": created.get("summary"),
                    "start": created.get("start"),
                    "end": created.get("end"),
                },
                display_output=f"Created event: {title}\nLink: {created.get('htmlLink')}",
            )

        except Exception as e:
            logger.exception("Calendar create error")
            return ToolResult(
                success=False,
                output=None,
                error=f"Failed to create event: {str(e)}",
                error_type=ToolErrorType.EXECUTION_FAILED,
            )


def create_calendar_tools(
    credentials_resolver: Callable[[], dict[str, Any] | None] | None = None,
) -> list[ToolHandler]:
    """
    Create calendar tool handlers.

    Args:
        credentials_resolver: Callable that returns Google OAuth credentials dict.

    Returns:
        List of calendar tool handlers.
    """
    return [
        GoogleCalendarHandler(credentials_resolver),
        CreateCalendarEventHandler(credentials_resolver),
    ]
