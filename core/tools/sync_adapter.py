"""
Hexis Tools System - Sync Adapter

Provides synchronous wrappers for async tool operations,
allowing the tools system to be used in sync contexts like
the conversation loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .registry import ToolRegistry
    from .base import ToolContext

logger = logging.getLogger(__name__)


class SyncToolAdapter:
    """
    Synchronous adapter for the async ToolRegistry.

    Wraps async operations in asyncio.run() for use in sync contexts.
    Creates its own event loop for each operation.
    """

    def __init__(self, dsn: str):
        """
        Initialize the adapter.

        Args:
            dsn: PostgreSQL connection string.
        """
        self._dsn = dsn
        self._registry: "ToolRegistry | None" = None
        self._pool = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def connect(self) -> None:
        """Connect to the database and initialize the registry."""
        if self._registry is not None:
            return

        async def _connect():
            import asyncpg
            from .registry import create_default_registry

            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
            self._registry = create_default_registry(self._pool)
            return self._registry

        # Create a new event loop for this thread
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(_connect())

    def close(self) -> None:
        """Close the connection."""
        if self._pool is not None and self._loop is not None:
            self._loop.run_until_complete(self._pool.close())
            self._pool = None
            self._registry = None

        if self._loop is not None:
            self._loop.close()
            self._loop = None

    def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """
        Execute a tool synchronously.

        Args:
            tool_name: Name of the tool to execute.
            arguments: Tool arguments.

        Returns:
            Dict with tool result.
        """
        self.connect()

        if self._registry is None or self._loop is None:
            return {"error": "Registry not initialized", "success": False}

        async def _execute():
            from .base import ToolContext, ToolExecutionContext
            import uuid

            context = ToolExecutionContext(
                tool_context=ToolContext.CHAT,
                call_id=str(uuid.uuid4()),
                allow_network=True,
                allow_shell=True,
                allow_file_write=True,
                allow_file_read=True,
            )

            # Apply context overrides from config
            try:
                config = await self._registry.get_config()
                ctx_override = config.get_context_overrides(ToolContext.CHAT)
                context.allow_shell = ctx_override.allow_shell
                context.allow_file_write = ctx_override.allow_file_write
                if config.workspace_path:
                    context.workspace_path = config.workspace_path
            except Exception as e:
                logger.warning(f"Failed to load tool config: {e}")

            result = await self._registry.execute(tool_name, arguments, context)

            return {
                "success": result.success,
                "output": result.output,
                "error": result.error,
                "error_type": result.error_type.value if result.error_type else None,
                "energy_spent": result.energy_spent,
                "duration_seconds": result.duration_seconds,
            }

        try:
            return self._loop.run_until_complete(_execute())
        except Exception as e:
            logger.exception(f"Tool execution failed: {tool_name}")
            return {"error": str(e), "success": False}

    def get_tool_definitions(self, context: str = "chat") -> list[dict[str, Any]]:
        """
        Get OpenAI function definitions for enabled tools.

        Args:
            context: Tool context ("chat", "heartbeat", "mcp").

        Returns:
            List of OpenAI function calling tool definitions.
        """
        self.connect()

        if self._registry is None or self._loop is None:
            return []

        async def _get_specs():
            from .base import ToolContext

            ctx = ToolContext(context)
            return await self._registry.get_specs(ctx)

        try:
            specs = self._loop.run_until_complete(_get_specs())
            # Convert to OpenAI function calling format
            return [{"type": "function", "function": spec} for spec in specs]
        except Exception as e:
            logger.exception("Failed to get tool definitions")
            return []

    def list_tools(self) -> list[str]:
        """List all available tool names."""
        self.connect()

        if self._registry is None:
            return []

        return self._registry.list_names()


class CombinedToolHandler:
    """
    Combined handler that uses both the new tool registry and
    legacy memory tools, preferring the registry for known tools.
    """

    def __init__(self, db_config: dict):
        self.db_config = db_config
        self._sync_adapter: SyncToolAdapter | None = None
        self._legacy_handler = None
        self._registry_tools: set[str] = set()

    def connect(self) -> None:
        """Initialize both handlers."""
        # Initialize sync adapter for new tools
        dsn = (
            f"postgresql://{self.db_config.get('user', 'postgres')}:"
            f"{self.db_config.get('password', 'password')}"
            f"@{self.db_config.get('host', 'localhost')}:"
            f"{int(self.db_config.get('port', 43815))}"
            f"/{self.db_config.get('dbname', 'hexis_memory')}"
        )

        try:
            self._sync_adapter = SyncToolAdapter(dsn)
            self._sync_adapter.connect()
            self._registry_tools = set(self._sync_adapter.list_tools())
            logger.info(f"Tool registry initialized with {len(self._registry_tools)} tools")
        except Exception as e:
            logger.warning(f"Failed to initialize tool registry: {e}")
            self._sync_adapter = None
            self._registry_tools = set()

        # Initialize legacy handler as fallback
        try:
            from core.memory_tools import ApiMemoryToolHandler
            self._legacy_handler = ApiMemoryToolHandler(self.db_config)
            self._legacy_handler.connect()
        except Exception as e:
            logger.warning(f"Failed to initialize legacy handler: {e}")

    def close(self) -> None:
        """Close both handlers."""
        if self._sync_adapter is not None:
            try:
                self._sync_adapter.close()
            except Exception:
                pass
            self._sync_adapter = None

        if self._legacy_handler is not None:
            try:
                self._legacy_handler.close()
            except Exception:
                pass
            self._legacy_handler = None

    def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """
        Execute a tool, using registry for new tools and legacy for memory tools.
        """
        self.connect()

        # Try registry first for new tools
        if self._sync_adapter is not None and tool_name in self._registry_tools:
            result = self._sync_adapter.execute_tool(tool_name, arguments)
            if result.get("success"):
                return result.get("output", result)
            # On failure, return error format
            return {"error": result.get("error", "Unknown error")}

        # Fall back to legacy handler for memory tools
        if self._legacy_handler is not None:
            return self._legacy_handler.execute_tool(tool_name, arguments)

        return {"error": f"Tool not found: {tool_name}"}

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Get combined tool definitions from both sources."""
        self.connect()

        definitions = []
        seen_names = set()

        # Get registry tools first (preferred)
        if self._sync_adapter is not None:
            try:
                registry_defs = self._sync_adapter.get_tool_definitions("chat")
                for defn in registry_defs:
                    name = defn.get("function", {}).get("name")
                    if name and name not in seen_names:
                        definitions.append(defn)
                        seen_names.add(name)
            except Exception as e:
                logger.warning(f"Failed to get registry definitions: {e}")

        # Add legacy tools that aren't in registry
        if self._legacy_handler is not None:
            try:
                from core.memory_tools import get_tool_definitions as get_legacy_defs
                for defn in get_legacy_defs():
                    name = defn.get("function", {}).get("name")
                    if name and name not in seen_names:
                        definitions.append(defn)
                        seen_names.add(name)
            except Exception as e:
                logger.warning(f"Failed to get legacy definitions: {e}")

        return definitions


def create_sync_tool_handler(db_config: dict) -> CombinedToolHandler:
    """
    Create a combined tool handler for use in sync contexts.

    Uses the new tool registry for extended tools (web, filesystem, shell)
    and falls back to legacy memory tools.
    """
    return CombinedToolHandler(db_config)
