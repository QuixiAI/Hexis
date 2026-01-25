"""
Hexis Tools System - MCP Integration

Client for connecting to MCP (Model Context Protocol) servers and
exposing their tools to the Hexis tool system.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from .base import (
    ToolCategory,
    ToolContext,
    ToolErrorType,
    ToolExecutionContext,
    ToolHandler,
    ToolResult,
    ToolSpec,
)
from .config import MCPServerConfig

if TYPE_CHECKING:
    from .registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class MCPMessage:
    """JSON-RPC message for MCP protocol."""

    jsonrpc: str = "2.0"
    id: int | None = None
    method: str | None = None
    params: dict[str, Any] | None = None
    result: Any = None
    error: dict[str, Any] | None = None

    def to_json(self) -> str:
        d = {"jsonrpc": self.jsonrpc}
        if self.id is not None:
            d["id"] = self.id
        if self.method is not None:
            d["method"] = self.method
        if self.params is not None:
            d["params"] = self.params
        if self.result is not None:
            d["result"] = self.result
        if self.error is not None:
            d["error"] = self.error
        return json.dumps(d)

    @classmethod
    def from_json(cls, data: str | dict) -> "MCPMessage":
        if isinstance(data, str):
            data = json.loads(data)
        return cls(
            jsonrpc=data.get("jsonrpc", "2.0"),
            id=data.get("id"),
            method=data.get("method"),
            params=data.get("params"),
            result=data.get("result"),
            error=data.get("error"),
        )


class MCPClient:
    """
    Client for communicating with an MCP server via stdio.

    Manages the subprocess lifecycle and handles JSON-RPC communication.
    """

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self._process: asyncio.subprocess.Process | None = None
        self._message_id = 0
        self._pending_requests: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._connected = False
        self._tools: list[dict[str, Any]] = []
        self._resources: list[dict[str, Any]] = []

    @property
    def is_connected(self) -> bool:
        return self._connected and self._process is not None

    async def connect(self) -> bool:
        """Start the MCP server and establish connection."""
        if self._connected:
            return True

        try:
            # Build environment
            env = os.environ.copy()
            env.update(self.config.env)

            # Start the subprocess
            cmd = [self.config.command] + self.config.args

            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            # Start reader task
            self._reader_task = asyncio.create_task(self._read_messages())

            # Initialize the connection
            result = await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                },
                "clientInfo": {
                    "name": "hexis",
                    "version": "1.0.0",
                },
            })

            if result is None:
                logger.error(f"MCP server {self.config.name} initialization failed")
                await self.disconnect()
                return False

            # Send initialized notification
            await self._send_notification("notifications/initialized", {})

            self._connected = True
            logger.info(f"Connected to MCP server: {self.config.name}")

            # List available tools
            await self._refresh_tools()

            return True

        except Exception as e:
            logger.exception(f"Failed to connect to MCP server {self.config.name}")
            await self.disconnect()
            return False

    async def disconnect(self) -> None:
        """Disconnect from the MCP server."""
        self._connected = False

        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
            except Exception:
                pass
            self._process = None

        # Cancel pending requests
        for future in self._pending_requests.values():
            future.cancel()
        self._pending_requests.clear()

    async def _read_messages(self) -> None:
        """Background task to read messages from the server."""
        if not self._process or not self._process.stdout:
            return

        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break

                try:
                    message = MCPMessage.from_json(line.decode("utf-8"))

                    if message.id is not None and message.id in self._pending_requests:
                        # This is a response to a request
                        future = self._pending_requests.pop(message.id)
                        if message.error:
                            future.set_exception(
                                MCPError(message.error.get("message", "Unknown error"))
                            )
                        else:
                            future.set_result(message.result)

                except json.JSONDecodeError:
                    continue

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception("Error reading MCP messages")

    async def _send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> Any:
        """Send a request and wait for response."""
        if not self._process or not self._process.stdin:
            raise MCPError("Not connected")

        self._message_id += 1
        msg_id = self._message_id

        message = MCPMessage(id=msg_id, method=method, params=params)
        request_line = message.to_json() + "\n"

        # Create future for response
        future: asyncio.Future = asyncio.Future()
        self._pending_requests[msg_id] = future

        try:
            self._process.stdin.write(request_line.encode("utf-8"))
            await self._process.stdin.drain()

            result = await asyncio.wait_for(future, timeout=timeout)
            return result

        except asyncio.TimeoutError:
            self._pending_requests.pop(msg_id, None)
            raise MCPError(f"Request timed out: {method}")

    async def _send_notification(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        """Send a notification (no response expected)."""
        if not self._process or not self._process.stdin:
            return

        message = MCPMessage(method=method, params=params)
        notification_line = message.to_json() + "\n"

        self._process.stdin.write(notification_line.encode("utf-8"))
        await self._process.stdin.drain()

    async def _refresh_tools(self) -> None:
        """Refresh the list of available tools."""
        try:
            result = await self._send_request("tools/list", {})
            self._tools = result.get("tools", []) if result else []
            logger.debug(f"MCP server {self.config.name} has {len(self._tools)} tools")
        except Exception as e:
            logger.warning(f"Failed to list tools from {self.config.name}: {e}")
            self._tools = []

    def get_tools(self) -> list[dict[str, Any]]:
        """Get the list of available tools."""
        return self._tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """Call a tool on the MCP server."""
        if not self._connected:
            raise MCPError("Not connected")

        result = await self._send_request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
            timeout=timeout,
        )

        return result


class MCPError(Exception):
    """Error from MCP communication."""
    pass


class MCPToolHandler(ToolHandler):
    """
    Handler for tools exposed by external MCP servers.

    Wraps an MCP tool to fit the Hexis ToolHandler interface.
    """

    def __init__(
        self,
        server_name: str,
        tool_name: str,
        tool_spec: dict[str, Any],
        client: MCPClient,
    ):
        self._server_name = server_name
        self._tool_name = tool_name
        self._tool_spec = tool_spec
        self._client = client

    @property
    def spec(self) -> ToolSpec:
        # Prefix the tool name with the server name for uniqueness
        prefixed_name = f"mcp_{self._server_name}_{self._tool_name}"

        return ToolSpec(
            name=prefixed_name,
            description=self._tool_spec.get("description", f"MCP tool: {self._tool_name}"),
            parameters=self._tool_spec.get("inputSchema", {"type": "object", "properties": {}}),
            category=ToolCategory.EXTERNAL,
            energy_cost=2,  # Default cost for MCP tools
            is_read_only=False,  # Assume MCP tools may have side effects
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        if not self._client.is_connected:
            return ToolResult.error_result(
                f"MCP server '{self._server_name}' is not connected",
                ToolErrorType.EXECUTION_FAILED,
            )

        try:
            result = await self._client.call_tool(self._tool_name, arguments)

            # Parse MCP result format
            content = result.get("content", [])
            is_error = result.get("isError", False)

            # Extract text content
            output_parts = []
            for item in content:
                if item.get("type") == "text":
                    output_parts.append(item.get("text", ""))
                elif item.get("type") == "image":
                    output_parts.append(f"[Image: {item.get('mimeType', 'image')}]")
                elif item.get("type") == "resource":
                    output_parts.append(f"[Resource: {item.get('uri', 'unknown')}]")

            output_text = "\n".join(output_parts)

            if is_error:
                return ToolResult.error_result(
                    output_text or "MCP tool returned an error",
                    ToolErrorType.EXECUTION_FAILED,
                )

            return ToolResult.success_result(
                output={
                    "server": self._server_name,
                    "tool": self._tool_name,
                    "content": content,
                    "text": output_text,
                },
                display_output=output_text[:500] if output_text else f"Executed {self._tool_name}",
            )

        except MCPError as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)
        except Exception as e:
            logger.exception(f"MCP tool execution failed: {self._tool_name}")
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class MCPManager:
    """
    Manages connections to multiple MCP servers.

    Handles server lifecycle and registers their tools with the registry.
    """

    def __init__(self, registry: "ToolRegistry"):
        self.registry = registry
        self._clients: dict[str, MCPClient] = {}

    async def load_servers(self, configs: list[MCPServerConfig]) -> None:
        """
        Load and connect to MCP servers.

        Args:
            configs: List of MCP server configurations.
        """
        for config in configs:
            if not config.enabled:
                logger.debug(f"Skipping disabled MCP server: {config.name}")
                continue

            await self.add_server(config)

    async def add_server(self, config: MCPServerConfig) -> bool:
        """
        Add and connect to an MCP server.

        Args:
            config: MCP server configuration.

        Returns:
            True if connected successfully.
        """
        if config.name in self._clients:
            logger.warning(f"MCP server already loaded: {config.name}")
            return False

        client = MCPClient(config)
        connected = await client.connect()

        if not connected:
            logger.error(f"Failed to connect to MCP server: {config.name}")
            return False

        self._clients[config.name] = client

        # Register tools from this server
        for tool_spec in client.get_tools():
            tool_name = tool_spec.get("name")
            if not tool_name:
                continue

            handler = MCPToolHandler(
                server_name=config.name,
                tool_name=tool_name,
                tool_spec=tool_spec,
                client=client,
            )

            self.registry.register_mcp(handler)
            logger.debug(f"Registered MCP tool: {handler.spec.name}")

        return True

    async def remove_server(self, name: str) -> bool:
        """
        Disconnect and remove an MCP server.

        Args:
            name: Server name.

        Returns:
            True if removed successfully.
        """
        client = self._clients.pop(name, None)
        if not client:
            return False

        # Unregister its tools
        for tool_spec in client.get_tools():
            tool_name = tool_spec.get("name")
            if tool_name:
                prefixed = f"mcp_{name}_{tool_name}"
                self.registry.unregister(prefixed)

        await client.disconnect()
        return True

    async def shutdown(self) -> None:
        """Disconnect from all MCP servers."""
        for name in list(self._clients.keys()):
            await self.remove_server(name)

    def get_server(self, name: str) -> MCPClient | None:
        """Get a connected MCP client by name."""
        return self._clients.get(name)

    def list_servers(self) -> list[str]:
        """List connected server names."""
        return list(self._clients.keys())

    def is_connected(self, name: str) -> bool:
        """Check if a server is connected."""
        client = self._clients.get(name)
        return client.is_connected if client else False


async def create_mcp_manager(registry: "ToolRegistry") -> MCPManager:
    """
    Create an MCP manager and load configured servers.

    Args:
        registry: The tool registry to register tools with.

    Returns:
        Configured MCPManager instance.
    """
    manager = MCPManager(registry)

    # Load MCP servers from config
    config = await registry.get_config()
    if config.mcp_servers:
        await manager.load_servers(config.mcp_servers)

    return manager
