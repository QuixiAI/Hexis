"""Sync-to-async tool bridge for RLM REPL environments.

Provides synchronous `tool_use()` and `list_tools()` functions that can be
called from exec() inside the REPL sandbox. Tool calls are bridged back to
the async ToolRegistry via `asyncio.run_coroutine_threadsafe()`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from core.tools.base import ToolContext, ToolExecutionContext, ToolResult

if TYPE_CHECKING:
    from core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

TOOL_CALL_TIMEOUT = 60  # seconds
# work_on_goal: the agent says which goal its next calls serve.
WORK_ON_GOAL = "work_on_goal"
_FREE_ENERGY = 1_000_000
TOOL_DISCOVERY_TIMEOUT = 10  # seconds


@dataclass
class ToolCallRecord:
    """Record of a single tool call made from the REPL."""

    tool_name: str
    arguments: dict[str, Any]
    result: ToolResult | None = None
    call_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    error: str | None = None

    @property
    def duration(self) -> float:
        if self.end_time:
            return self.end_time - self.start_time
        return 0.0

    @property
    def energy_spent(self) -> int:
        if self.result:
            return self.result.energy_spent
        return 0

    def to_action_taken(self) -> dict[str, Any]:
        """Convert to the actions_taken format used by apply_heartbeat_decision."""
        output = None
        if self.result:
            output = self.result.to_model_output() if self.result.success else self.result.error

        return {
            "action": self.tool_name,
            "params": self.arguments,
            "source": "rlm_repl",
            "result": {
                "success": self.result.success if self.result else False,
                "output_preview": str(output)[:500] if output else None,
                "energy_spent": self.energy_spent,
                "duration_seconds": round(self.duration, 3),
            },
        }


class ReplToolBridge:
    """
    Synchronous bridge for calling async ToolRegistry from REPL threads.

    The REPL runs in a thread pool executor. This bridge uses
    `asyncio.run_coroutine_threadsafe()` to dispatch tool calls back
    to the main async event loop.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        loop: asyncio.AbstractEventLoop,
        *,
        heartbeat_id: str | None = None,
        initial_energy: float = 20.0,
        tool_context: ToolContext = ToolContext.HEARTBEAT,
        allow_network: bool = True,
        allow_shell: bool = False,
        allow_file_write: bool = False,
    ):
        self._registry = registry
        self._loop = loop
        self._heartbeat_id = heartbeat_id
        self._remaining_energy = initial_energy
        self._tool_context = tool_context
        self._allow_network = allow_network
        self._allow_shell = allow_shell
        self._allow_file_write = allow_file_write
        self._call_records: list[ToolCallRecord] = []
        # Set by work_on_goal: calls serving a goal the user set cost no
        # energy (until the day's free-work cap, heartbeat.free_work_daily_token_cap).
        self._focus_goal: dict[str, Any] | None = None
        self._free = False

    def _work_on_goal(self, args: dict[str, Any]) -> dict[str, Any]:
        goal_id = str(args.get("goal_id") or "").strip()

        async def _lookup() -> tuple[Any, Any]:
            async with self._registry.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id::text, content, status::text, goal_origin::text AS origin "
                    "FROM memories WHERE type = 'goal' AND id::text = $1",
                    goal_id,
                )
                status = await conn.fetchval("SELECT free_goal_work_status()")
            return row, status

        try:
            row, status = asyncio.run_coroutine_threadsafe(_lookup(), self._loop).result(timeout=TOOL_DISCOVERY_TIMEOUT)
        except Exception as exc:
            return {"success": False, "output": None, "error": f"Could not look up the goal: {exc}", "energy_spent": 0}
        if row is None:
            return {"success": False, "output": None, "error": f"No goal with id {goal_id!r}", "energy_spent": 0}
        status = json.loads(status) if isinstance(status, str) else (status or {})
        by_user = row["origin"] == "user_request"
        created_by = "user" if by_user else "agent"
        self._focus_goal = {"goal_id": row["id"], "title": row["content"], "created_by": created_by}
        self._free = by_user and bool(status.get("free", True))
        if self._free:
            note = "The user set this goal: work on it costs no energy."
        elif by_user:
            note = "The user set this goal, but today's free-work cap is reached: it costs energy as usual."
        else:
            note = "You set this goal yourself: work on it costs energy as usual."
        logger.info("REPL work_on_goal: %s (created_by=%s, free=%s)", row["content"][:80], created_by, self._free)
        return {"success": True, "output": {**self._focus_goal, "free": self._free, "note": note}, "error": None, "energy_spent": 0}

    def tool_use(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Execute a tool synchronously from the REPL.

        Args:
            name: Tool name (e.g. "recall", "reflect")
            args: Tool arguments dict

        Returns:
            Dict with keys: success, output, energy_spent, error
        """
        args = args or {}
        if name == WORK_ON_GOAL:
            return self._work_on_goal(args)
        record = ToolCallRecord(tool_name=name, arguments=args)
        free = self._free

        try:
            ctx = ToolExecutionContext(
                tool_context=self._tool_context,
                call_id=record.call_id,
                heartbeat_id=self._heartbeat_id,
                energy_available=_FREE_ENERGY if free else int(self._remaining_energy),
                allow_network=self._allow_network,
                allow_shell=self._allow_shell,
                allow_file_write=self._allow_file_write,
            )

            # Bridge async call from sync thread
            future = asyncio.run_coroutine_threadsafe(
                self._registry.execute(name, args, ctx),
                self._loop,
            )
            result: ToolResult = future.result(timeout=TOOL_CALL_TIMEOUT)

            if free:
                result.energy_spent = 0  # the user's goal
            record.result = result
            record.end_time = time.time()

            # Track energy
            self._remaining_energy -= result.energy_spent

            self._call_records.append(record)

            logger.info(
                "REPL tool_use: %s -> success=%s energy=%d duration=%.2fs",
                name,
                result.success,
                result.energy_spent,
                record.duration,
            )

            return {
                "success": result.success,
                "output": result.output if result.success else None,
                "error": result.error if not result.success else None,
                "energy_spent": result.energy_spent,
            }

        except TimeoutError:
            record.error = f"Tool call timed out after {TOOL_CALL_TIMEOUT}s"
            record.end_time = time.time()
            self._call_records.append(record)
            logger.warning("REPL tool_use: %s timed out", name)
            return {"success": False, "output": None, "error": record.error, "energy_spent": 0}

        except Exception as e:
            record.error = str(e)
            record.end_time = time.time()
            self._call_records.append(record)
            logger.exception("REPL tool_use: %s failed", name)
            return {"success": False, "output": None, "error": str(e), "energy_spent": 0}

    def list_tools(self) -> list[dict[str, Any]]:
        """Return available tools with descriptions, schemas, and energy costs."""
        if self._loop.is_closed():
            return self._registered_tool_entries("event loop is closed")

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is self._loop:
            return self._registered_tool_entries("called from owning event loop thread")

        future = asyncio.run_coroutine_threadsafe(
            self._enabled_tools_and_approvals(),
            self._loop,
        )
        try:
            handlers, approved = future.result(timeout=TOOL_DISCOVERY_TIMEOUT)
        except TimeoutError:
            future.cancel()
            return self._registered_tool_entries(
                f"registry discovery timed out after {TOOL_DISCOVERY_TIMEOUT}s"
            )
        except Exception as exc:
            return self._registered_tool_entries(f"registry discovery failed: {exc}")

        return self._tool_entries(handlers, approved)

    async def _enabled_tools_and_approvals(self) -> tuple[list[Any], set[str]]:
        handlers = await self._registry.get_enabled_tools(self._tool_context)
        approved: set[str] = set()
        pool = getattr(self._registry, "pool", None)
        if pool is not None:
            try:
                async with pool.acquire() as conn:
                    raw = await conn.fetchval("SELECT value FROM config WHERE key = 'tools.approvals'")
                value = json.loads(raw) if isinstance(raw, str) else raw
                approved = {str(name) for name in value or []}
            except Exception:
                logger.debug("REPL list_tools could not read tool approvals", exc_info=True)
        return handlers, approved

    def _registered_tool_entries(self, reason: str) -> list[dict[str, Any]]:
        """Fallback to context-filtered in-process registry entries."""
        logger.warning("REPL list_tools falling back to registered tools: %s", reason)
        try:
            handlers = [
                handler
                for handler in self._registry.list_all()
                if self._tool_context in handler.spec.allowed_contexts
            ]
        except Exception as exc:
            logger.exception("REPL list_tools fallback failed")
            return [{
                "error": f"Tool inventory unavailable: {exc}",
                "name": "__tool_inventory_error__",
                "description": "The tool registry could not be inspected.",
                "energy_cost": 0,
                "category": "internal",
                "is_read_only": True,
                "parameters": {},
                "allowed_contexts": [],
            }]
        return self._tool_entries(handlers)

    @staticmethod
    def _tool_entries(handlers: list[Any], approved: set[str] | None = None) -> list[dict[str, Any]]:
        # A tool the user pre-approved needs no approval now; say so, so the
        # agent doesn't hold back from it on an old refusal.
        approved = approved or set()
        entries = []
        for handler in handlers:
            spec = handler.spec
            pre_approved = spec.requires_approval and spec.name in approved
            entries.append({
                "name": spec.name,
                "description": spec.description,
                "energy_cost": spec.energy_cost,
                "category": spec.category.value,
                "is_read_only": spec.is_read_only,
                "requires_approval": spec.requires_approval and not pre_approved,
                **({"pre_approved": True} if pre_approved else {}),
                "supports_parallel": spec.supports_parallel,
                "optional": spec.optional,
                "parameters": spec.parameters,
                "allowed_contexts": sorted(context.value for context in spec.allowed_contexts),
            })
        entries.append({
            "name": WORK_ON_GOAL,
            "description": (
                "Say which goal your next tool calls serve: work_on_goal({'goal_id': ...}). "
                "Work on a goal the user set costs no energy; work on your own goals costs as usual. "
                "Call it again when you switch goals."
            ),
            "energy_cost": 0,
            "category": "internal",
            "is_read_only": True,
            "requires_approval": False,
            "supports_parallel": False,
            "optional": False,
            "parameters": {"type": "object", "properties": {"goal_id": {"type": "string"}}, "required": ["goal_id"]},
            "allowed_contexts": ["heartbeat"],
        })
        return sorted(entries, key=lambda item: item["name"])

    def energy_remaining(self) -> float:
        """Return remaining energy budget."""
        return max(0.0, self._remaining_energy)

    def get_call_records(self) -> list[ToolCallRecord]:
        """Return all tool call records."""
        return list(self._call_records)

    def get_total_energy_spent(self) -> int:
        """Total energy spent across all REPL tool calls."""
        return sum(r.energy_spent for r in self._call_records)


def call_records_to_actions_taken(records: list[ToolCallRecord]) -> list[dict[str, Any]]:
    """Convert REPL tool call records to actions_taken format for apply_heartbeat_decision."""
    return [r.to_action_taken() for r in records if r.result is not None]
