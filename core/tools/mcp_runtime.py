"""Process-wide lazy MCP transport (#41).

Skills are the sole model-facing capability catalog; MCP servers are a
transport detail behind them. Nothing connects at startup (when
``mcp.skill_gated`` is on): a server starts the first time a skill bound to it
is activated, its connection is shared across the process (the API builds a
fresh ToolRegistry per request — subprocesses must not be per-request), and
only the tools a skill's manifest names are ever registered, so unlisted
server tools never reach model context.
"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
import shutil
from typing import TYPE_CHECKING, Any

from core.tools.config import MCPServerConfig
from core.tools.mcp import MCPClient, MCPToolHandler

if TYPE_CHECKING:  # pragma: no cover
    from core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class MCPRuntime:
    """Shared, lazily-connected MCP clients keyed by server name."""

    _instance: "MCPRuntime | None" = None

    def __init__(self) -> None:
        self._clients: dict[str, MCPClient] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @classmethod
    def instance(cls) -> "MCPRuntime":
        if cls._instance is None:
            cls._instance = MCPRuntime()
        return cls._instance

    def _lock_for(self, server: str) -> asyncio.Lock:
        if server not in self._locks:
            self._locks[server] = asyncio.Lock()
        return self._locks[server]

    async def ensure_connected(self, config: MCPServerConfig) -> dict[str, Any]:
        """Idempotently connect the server; concurrent activations share one
        attempt. A dead subprocess gets exactly one reconnect. Returns
        {connected, tools?, error?, next_step?} — failures carry what/why/next,
        never a bare no."""
        async with self._lock_for(config.name):
            client = self._clients.get(config.name)
            if client is not None:
                if client.is_connected and client.config.env == config.env:
                    return {"connected": True, "tools": client.get_tools()}
                # Dead process or changed credentials: drop and reconnect once.
                try:
                    await client.disconnect()
                except Exception:
                    logger.debug("stale MCP client disconnect failed", exc_info=True)
                self._clients.pop(config.name, None)

            if not config.command:
                return {
                    "connected": False,
                    "error": f"MCP server '{config.name}' has no command configured",
                    "next_step": (
                        f"Add a command for server '{config.name}' in tools config "
                        "(mcp_servers) or in the skill manifest's mcp block."
                    ),
                }
            if shutil.which(config.command) is None:
                return {
                    "connected": False,
                    "error": f"command not found: {config.command}",
                    "next_step": f"Install '{config.command}' on the host running Hexis and retry.",
                }

            client = MCPClient(config)
            connected = await client.connect()
            if not connected:
                return {
                    "connected": False,
                    "error": f"MCP server '{config.name}' failed to start or initialize",
                    "next_step": (
                        f"Run `{config.command} {' '.join(config.args)}` by hand to see its "
                        "error output; check credentials and network, then retry use_skill."
                    ),
                }
            self._clients[config.name] = client
            return {"connected": True, "tools": client.get_tools()}

    def register_into(
        self,
        registry: "ToolRegistry",
        server: str,
        bound_tools: list[str],
    ) -> list[str]:
        """Register handlers for the connected server's tools into a registry —
        ONLY those the manifest names (exact ``mcp_<server>_<tool>`` names or
        globs like ``mcp_<server>_*``). Returns the concrete registered names.
        """
        client = self._clients.get(server)
        if client is None or not client.is_connected:
            return []
        patterns = [p for p in bound_tools if p.startswith("mcp_")]
        registered: list[str] = []
        for tool_spec in client.get_tools():
            tool_name = tool_spec.get("name")
            if not tool_name:
                continue
            prefixed = f"mcp_{server}_{tool_name}"
            if not any(fnmatch.fnmatch(prefixed, pattern) for pattern in patterns):
                continue
            if registry.get_spec(prefixed) is None:
                registry.register_mcp(MCPToolHandler(
                    server_name=server,
                    tool_name=tool_name,
                    tool_spec=tool_spec,
                    client=client,
                ))
            registered.append(prefixed)
        return registered

    async def disconnect_server(self, server: str) -> None:
        """Stop and remove a single connected MCP server (e.g. on credential revocation)."""
        async with self._lock_for(server):
            client = self._clients.pop(server, None)
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    logger.debug("MCP client disconnect failed for %s", server, exc_info=True)

    async def shutdown(self) -> None:
        for name, client in list(self._clients.items()):
            try:
                await client.disconnect()
            except Exception:
                logger.debug("MCP client shutdown failed for %s", name, exc_info=True)
            self._clients.pop(name, None)

    def connected_servers(self) -> list[str]:
        return [name for name, c in self._clients.items() if c.is_connected]
