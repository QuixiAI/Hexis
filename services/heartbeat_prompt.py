"""Heartbeat decision prompt — rendered by the database.

The prompt is assembled entirely in the DB by render_heartbeat_decision_prompt
(db/39_functions_prompt_render.sql) from the DB-produced heartbeat context
(gather_turn_context). The former Python byte-parity fork of the renderer was
deleted; the golden fixtures in tests/fixtures/prompt_render/ pin the rendered
output (tests/db/test_prompt_render.py).
"""

from __future__ import annotations

import inspect
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def _discover_heartbeat_tool_names(tool_registry: Any) -> set[str] | None:
    """Query the live ToolRegistry for enabled ToolContext.HEARTBEAT tool names.

    Returns a set of tool names (possibly empty if all tools are disabled), or
    None if the registry object does not expose discovery methods or discovery
    cannot be completed.
    """
    if tool_registry is None:
        return None

    from core.tools.base import ToolContext

    handlers: list[Any] | tuple[Any, ...] | set[Any] | None = None
    get_enabled = getattr(tool_registry, "get_enabled_tools", None)
    if callable(get_enabled):
        try:
            maybe_coro = get_enabled(ToolContext.HEARTBEAT)
            if inspect.isawaitable(maybe_coro):
                resolved = await maybe_coro
                if isinstance(resolved, (list, tuple, set)):
                    handlers = resolved
        except Exception as exc:
            logger.warning(
                "Failed to query enabled heartbeat tools from registry; falling back to registered tools: %s",
                exc,
            )

    if handlers is None:
        list_all = getattr(tool_registry, "list_all", None)
        if callable(list_all):
            try:
                raw_all = list_all()
                if isinstance(raw_all, (list, tuple, set)):
                    handlers = [
                        handler
                        for handler in raw_all
                        if ToolContext.HEARTBEAT
                        in getattr(getattr(handler, "spec", None), "allowed_contexts", ())
                    ]
            except Exception as exc:
                logger.warning(
                    "Failed to inspect registered heartbeat tools from registry: %s",
                    exc,
                )

    if handlers is None:
        return None

    live_names: set[str] = set()
    for handler in handlers:
        spec = getattr(handler, "spec", None)
        name = getattr(spec, "name", None)
        if isinstance(name, str) and name:
            live_names.add(name)
    return live_names


async def with_live_heartbeat_tools(
    context: dict[str, Any] | None,
    tool_registry: Any,
) -> dict[str, Any]:
    """Return a shallow copy of heartbeat context with agent.tools derived from
    the live enabled ToolContext.HEARTBEAT handlers in tool_registry.

    Existing agent.tools entries are used only to preserve display ordering for
    tools that are actually enabled; unregistered or disabled names are dropped,
    and all remaining enabled tools are appended in sorted order.
    """
    if not isinstance(context, dict):
        return {}

    live_names = await _discover_heartbeat_tool_names(tool_registry)
    if live_names is None:
        return context

    existing_agent = context.get("agent")
    agent = dict(existing_agent) if isinstance(existing_agent, dict) else {}
    existing_tools = agent.get("tools")

    ordered: list[str] = []
    seen: set[str] = set()
    if isinstance(existing_tools, list):
        for item in existing_tools:
            if isinstance(item, str):
                name = item.strip()
            elif isinstance(item, dict):
                if item.get("enabled") is False:
                    continue
                raw_name = item.get("name")
                name = raw_name.strip() if isinstance(raw_name, str) else ""
            else:
                name = ""
            if name and name in live_names and name not in seen:
                ordered.append(name)
                seen.add(name)

    for name in sorted(live_names - seen):
        ordered.append(name)

    agent["tools"] = ordered
    updated = dict(context)
    updated["agent"] = agent
    return updated


async def render_heartbeat_decision_prompt_db(
    conn: Any,
    context: dict[str, Any],
    *,
    tool_registry: Any = None,
) -> str:
    """Render the heartbeat decision prompt in the DB."""
    if tool_registry is not None:
        context = await with_live_heartbeat_tools(context, tool_registry)
    return await conn.fetchval(
        "SELECT render_heartbeat_decision_prompt($1::jsonb)", json.dumps(context)
    )
