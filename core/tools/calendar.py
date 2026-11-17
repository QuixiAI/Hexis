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


CALENDAR_SETUP_STEP = (
    "Google Calendar is not connected. Set GOOGLE_CALENDAR_CREDENTIALS in the "
    "service environment and restart."
)


class _GoogleCalendarCredentials:
    """Readiness shared by the calendar tools: they all need Google credentials."""

    _credentials_resolver: Callable[[], dict[str, Any] | None] | None

    async def readiness(self, context: ToolExecutionContext | None = None) -> str | None:
        if self._credentials_resolver and self._credentials_resolver():
            return None
        return CALENDAR_SETUP_STEP


class GoogleCalendarHandler(_GoogleCalendarCredentials, ToolHandler):
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
            optional=True,
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


class CreateCalendarEventHandler(_GoogleCalendarCredentials, ToolHandler):
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
            optional=True,
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


class UpdateCalendarEventHandler(_GoogleCalendarCredentials, ToolHandler):
    """Update an existing calendar event."""

    def __init__(
        self,
        credentials_resolver: Callable[[], dict[str, Any] | None] | None = None,
    ):
        self._credentials_resolver = credentials_resolver

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="calendar_update",
            description="Update an existing calendar event. Change title, time, location, or description.",
            parameters={
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "Event ID to update (from calendar_events listing)",
                    },
                    "title": {
                        "type": "string",
                        "description": "New event title/summary",
                    },
                    "start": {
                        "type": "string",
                        "description": "New start time in ISO format",
                    },
                    "end": {
                        "type": "string",
                        "description": "New end time in ISO format",
                    },
                    "description": {
                        "type": "string",
                        "description": "New event description/notes",
                    },
                    "location": {
                        "type": "string",
                        "description": "New event location",
                    },
                    "calendar_id": {
                        "type": "string",
                        "default": "primary",
                        "description": "Calendar ID (default: primary)",
                    },
                },
                "required": ["event_id"],
            },
            category=ToolCategory.CALENDAR,
            energy_cost=3,
            is_read_only=False,
            requires_approval=True,
            optional=True,
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

        event_id = arguments["event_id"]
        calendar_id = arguments.get("calendar_id", "primary")

        try:
            creds = Credentials.from_authorized_user_info(credentials)
            service = build("calendar", "v3", credentials=creds)

            # Fetch existing event
            existing = service.events().get(
                calendarId=calendar_id, eventId=event_id
            ).execute()

            # Apply updates (only provided fields)
            if arguments.get("title"):
                existing["summary"] = arguments["title"]
            if arguments.get("start"):
                existing["start"] = {"dateTime": arguments["start"], "timeZone": "UTC"}
            if arguments.get("end"):
                existing["end"] = {"dateTime": arguments["end"], "timeZone": "UTC"}
            if "description" in arguments:
                existing["description"] = arguments["description"]
            if "location" in arguments:
                existing["location"] = arguments["location"]

            updated = service.events().update(
                calendarId=calendar_id, eventId=event_id, body=existing
            ).execute()

            return ToolResult(
                success=True,
                output={
                    "event_id": updated.get("id"),
                    "html_link": updated.get("htmlLink"),
                    "summary": updated.get("summary"),
                    "start": updated.get("start"),
                    "end": updated.get("end"),
                },
                display_output=f"Updated event: {updated.get('summary')}\nLink: {updated.get('htmlLink')}",
            )

        except Exception as e:
            logger.exception("Calendar update error")
            return ToolResult(
                success=False,
                output=None,
                error=f"Failed to update event: {str(e)}",
                error_type=ToolErrorType.EXECUTION_FAILED,
            )


class DeleteCalendarEventHandler(_GoogleCalendarCredentials, ToolHandler):
    """Delete a calendar event."""

    def __init__(
        self,
        credentials_resolver: Callable[[], dict[str, Any] | None] | None = None,
    ):
        self._credentials_resolver = credentials_resolver

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="calendar_delete",
            description="Delete a calendar event by ID.",
            parameters={
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "Event ID to delete (from calendar_events listing)",
                    },
                    "calendar_id": {
                        "type": "string",
                        "default": "primary",
                        "description": "Calendar ID (default: primary)",
                    },
                },
                "required": ["event_id"],
            },
            category=ToolCategory.CALENDAR,
            energy_cost=3,
            is_read_only=False,
            requires_approval=True,
            optional=True,
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

        event_id = arguments["event_id"]
        calendar_id = arguments.get("calendar_id", "primary")

        try:
            creds = Credentials.from_authorized_user_info(credentials)
            service = build("calendar", "v3", credentials=creds)

            service.events().delete(
                calendarId=calendar_id, eventId=event_id
            ).execute()

            return ToolResult(
                success=True,
                output={"event_id": event_id, "deleted": True},
                display_output=f"Deleted event {event_id}",
            )

        except Exception as e:
            logger.exception("Calendar delete error")
            return ToolResult(
                success=False,
                output=None,
                error=f"Failed to delete event: {str(e)}",
                error_type=ToolErrorType.EXECUTION_FAILED,
            )


class MeetingPrepHandler(_GoogleCalendarCredentials, ToolHandler):
    """B.3: Fetch today's calendar events, cross-reference attendees with CRM, build briefing.

    Composite tool that orchestrates:
    1. Google Calendar API fetch of upcoming events
    2. Cross-reference attendees with contacts table
    3. Build a meeting preparation briefing
    """

    def __init__(
        self,
        credentials_resolver: Callable[[], dict[str, Any] | None] | None = None,
    ):
        self._credentials_resolver = credentials_resolver

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="meeting_prep",
            description=(
                "Prepare meeting briefings by fetching upcoming calendar events, "
                "cross-referencing attendees with CRM contacts, and building a "
                "summary. Use as a morning prep routine."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "days_ahead": {
                        "type": "integer",
                        "default": 1,
                        "minimum": 1,
                        "maximum": 7,
                        "description": "Number of days ahead to prepare for",
                    },
                    "calendar_id": {
                        "type": "string",
                        "default": "primary",
                        "description": "Calendar ID (default: primary)",
                    },
                },
            },
            category=ToolCategory.CALENDAR,
            energy_cost=4,
            is_read_only=True,
            optional=True,
            allowed_contexts={ToolContext.HEARTBEAT, ToolContext.CHAT},
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

        import json

        days_ahead = arguments.get("days_ahead", 1)
        calendar_id = arguments.get("calendar_id", "primary")
        pool = context.registry.pool

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
                    maxResults=25,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )

            events = events_result.get("items", [])
            if not events:
                return ToolResult.success_result(
                    {"meetings": [], "count": 0},
                    display_output="No upcoming meetings to prepare for",
                )

            briefings = []
            display_lines = [f"Meeting Prep — next {days_ahead} day(s)", ""]

            async with pool.acquire() as conn:
                for event in events:
                    summary = event.get("summary", "(No title)")
                    start = event["start"].get("dateTime", event["start"].get("date"))
                    end = event["end"].get("dateTime", event["end"].get("date"))
                    location = event.get("location")
                    description = event.get("description", "")
                    attendees_raw = event.get("attendees", [])

                    # Skip events with no attendees (blocks, holds)
                    external_attendees = [
                        a for a in attendees_raw
                        if not a.get("self", False)
                    ]

                    # Cross-reference attendees with CRM
                    attendee_info = []
                    for att in external_attendees:
                        email = att.get("email", "")
                        display_name = att.get("displayName", "")
                        status = att.get("responseStatus", "needsAction")

                        crm_data = None
                        if email:
                            row = await conn.fetchrow(
                                """SELECT name, company, role, notes, tags, last_touch
                                   FROM contacts WHERE email = $1""",
                                email,
                            )
                            if row:
                                crm_data = {
                                    "name": row["name"],
                                    "company": row["company"],
                                    "role": row["role"],
                                    "notes": row["notes"][:200] if row["notes"] else None,
                                    "tags": row["tags"],
                                    "last_touch": str(row["last_touch"]) if row["last_touch"] else None,
                                }

                        attendee_info.append({
                            "email": email,
                            "name": display_name or (crm_data["name"] if crm_data else email.split("@")[0]),
                            "response": status,
                            "crm": crm_data,
                        })

                    meeting = {
                        "summary": summary,
                        "start": start,
                        "end": end,
                        "location": location,
                        "description": description[:300] if description else None,
                        "attendees": attendee_info,
                        "event_id": event.get("id"),
                    }
                    briefings.append(meeting)

                    # Build display
                    display_lines.append(f"## {start} — {summary}")
                    if location:
                        display_lines.append(f"  Location: {location}")
                    if external_attendees:
                        display_lines.append(f"  Attendees ({len(external_attendees)}):")
                        for ai in attendee_info:
                            crm_tag = ""
                            if ai["crm"]:
                                parts = []
                                if ai["crm"].get("company"):
                                    parts.append(ai["crm"]["company"])
                                if ai["crm"].get("role"):
                                    parts.append(ai["crm"]["role"])
                                if parts:
                                    crm_tag = f" [{', '.join(parts)}]"
                            display_lines.append(
                                f"    - {ai['name']} <{ai['email']}>{crm_tag} ({ai['response']})"
                            )
                    display_lines.append("")

            return ToolResult.success_result(
                {"meetings": briefings, "count": len(briefings)},
                display_output="\n".join(display_lines),
            )

        except Exception as e:
            logger.exception("Meeting prep error")
            return ToolResult(
                success=False,
                output=None,
                error=f"Meeting prep failed: {e}",
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
        UpdateCalendarEventHandler(credentials_resolver),
        DeleteCalendarEventHandler(credentials_resolver),
        MeetingPrepHandler(credentials_resolver),
    ]
