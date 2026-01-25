"""
Hexis Tools System - Configuration

Configuration dataclasses and loading for the tools system.
Configuration is stored in the database (config table, key='tools').
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .base import ToolCategory, ToolContext


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MCPServerConfig":
        return cls(
            name=str(data.get("name", "")),
            command=str(data.get("command", "")),
            args=list(data.get("args", [])),
            env=dict(data.get("env", {})),
            enabled=bool(data.get("enabled", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "args": self.args,
            "env": self.env,
            "enabled": self.enabled,
        }


@dataclass
class ContextOverrides:
    """Context-specific tool configuration overrides."""

    max_energy_per_tool: int | None = None
    disabled: list[str] = field(default_factory=list)
    enabled: list[str] = field(default_factory=list)
    allow_all: bool = False
    allow_shell: bool = False
    allow_file_write: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextOverrides":
        return cls(
            max_energy_per_tool=data.get("max_energy_per_tool"),
            disabled=list(data.get("disabled", [])),
            enabled=list(data.get("enabled", [])),
            allow_all=bool(data.get("allow_all", False)),
            allow_shell=bool(data.get("allow_shell", False)),
            allow_file_write=bool(data.get("allow_file_write", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_energy_per_tool": self.max_energy_per_tool,
            "disabled": self.disabled,
            "enabled": self.enabled,
            "allow_all": self.allow_all,
            "allow_shell": self.allow_shell,
            "allow_file_write": self.allow_file_write,
        }


@dataclass
class ToolsConfig:
    """
    Complete tools configuration.

    Stored in database: config table, key='tools'
    """

    # Global enable/disable lists
    enabled: list[str] | None = None  # None = all enabled by default
    disabled: list[str] = field(default_factory=list)
    disabled_categories: list[ToolCategory] = field(default_factory=list)

    # MCP servers
    mcp_servers: list[MCPServerConfig] = field(default_factory=list)

    # API keys (values are env var references like "env:TAVILY_API_KEY")
    api_keys: dict[str, str] = field(default_factory=dict)

    # Custom energy costs (overrides defaults)
    costs: dict[str, int] = field(default_factory=dict)

    # Context-specific overrides
    context_overrides: dict[ToolContext, ContextOverrides] = field(default_factory=dict)

    # Workspace restrictions
    workspace_path: str | None = None

    @classmethod
    def from_json(cls, data: str | dict | None) -> "ToolsConfig":
        """Parse configuration from JSON or dict."""
        if data is None:
            return cls()

        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return cls()

        if not isinstance(data, dict):
            return cls()

        # Parse disabled categories
        disabled_categories = []
        for cat in data.get("disabled_categories", []):
            try:
                disabled_categories.append(ToolCategory(cat))
            except ValueError:
                pass

        # Parse MCP servers
        mcp_servers = []
        for server_data in data.get("mcp_servers", []):
            if isinstance(server_data, dict):
                mcp_servers.append(MCPServerConfig.from_dict(server_data))

        # Parse context overrides
        context_overrides: dict[ToolContext, ContextOverrides] = {}
        for ctx_name, ctx_data in data.get("context_overrides", {}).items():
            try:
                ctx = ToolContext(ctx_name)
                if isinstance(ctx_data, dict):
                    context_overrides[ctx] = ContextOverrides.from_dict(ctx_data)
            except ValueError:
                pass

        return cls(
            enabled=data.get("enabled"),
            disabled=list(data.get("disabled", [])),
            disabled_categories=disabled_categories,
            mcp_servers=mcp_servers,
            api_keys=dict(data.get("api_keys", {})),
            costs=dict(data.get("costs", {})),
            context_overrides=context_overrides,
            workspace_path=data.get("workspace_path"),
        )

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "enabled": self.enabled,
            "disabled": self.disabled,
            "disabled_categories": [c.value for c in self.disabled_categories],
            "mcp_servers": [s.to_dict() for s in self.mcp_servers],
            "api_keys": self.api_keys,
            "costs": self.costs,
            "context_overrides": {
                k.value: v.to_dict() for k, v in self.context_overrides.items()
            },
            "workspace_path": self.workspace_path,
        }

    def is_tool_enabled(self, tool_name: str, category: ToolCategory) -> bool:
        """Check if a tool is enabled globally."""
        # Check explicit disable
        if tool_name in self.disabled:
            return False

        # Check category disable
        if category in self.disabled_categories:
            return False

        # Check explicit enable list
        if self.enabled is not None:
            return tool_name in self.enabled

        return True

    def is_tool_enabled_for_context(
        self,
        tool_name: str,
        category: ToolCategory,
        context: ToolContext,
    ) -> bool:
        """Check if a tool is enabled for a specific context."""
        # First check global
        if not self.is_tool_enabled(tool_name, category):
            return False

        # Check context overrides
        ctx_override = self.context_overrides.get(context)
        if ctx_override:
            if ctx_override.allow_all:
                return True
            if tool_name in ctx_override.disabled:
                return False
            if ctx_override.enabled and tool_name not in ctx_override.enabled:
                return False

        return True

    def get_energy_cost(self, tool_name: str, default_cost: int) -> int:
        """Get energy cost for a tool (custom or default)."""
        return self.costs.get(tool_name, default_cost)

    def get_api_key(self, key_name: str) -> str | None:
        """
        Resolve an API key.

        Values can be:
        - Direct value: "sk-..."
        - Env reference: "env:TAVILY_API_KEY"
        """
        value = self.api_keys.get(key_name)
        if not value:
            return None

        if value.startswith("env:"):
            env_var = value[4:]
            return os.getenv(env_var)

        return value

    def get_context_overrides(self, context: ToolContext) -> ContextOverrides:
        """Get overrides for a context (or defaults)."""
        return self.context_overrides.get(context, ContextOverrides())


async def load_tools_config(pool) -> ToolsConfig:
    """Load tools configuration from database."""
    async with pool.acquire() as conn:
        row = await conn.fetchval(
            "SELECT value FROM config WHERE key = 'tools'"
        )
        return ToolsConfig.from_json(row)


async def save_tools_config(pool, config: ToolsConfig) -> None:
    """Save tools configuration to database."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO config (key, value, description, updated_at)
            VALUES ('tools', $1::jsonb, 'Tool system configuration', NOW())
            ON CONFLICT (key) DO UPDATE SET value = $1::jsonb, updated_at = NOW()
            """,
            config.to_json(),
        )


def update_tools_config_sync(conn, updates: dict[str, Any]) -> ToolsConfig:
    """Update tools configuration synchronously (for CLI)."""
    row = conn.execute("SELECT value FROM config WHERE key = 'tools'").fetchone()
    config = ToolsConfig.from_json(row[0] if row else None)

    # Apply updates
    if "enable" in updates:
        tool_name = updates["enable"]
        if config.enabled is None:
            config.enabled = []
        if tool_name not in config.enabled:
            config.enabled.append(tool_name)
        if tool_name in config.disabled:
            config.disabled.remove(tool_name)

    if "disable" in updates:
        tool_name = updates["disable"]
        if tool_name not in config.disabled:
            config.disabled.append(tool_name)
        if config.enabled and tool_name in config.enabled:
            config.enabled.remove(tool_name)

    if "add_mcp" in updates:
        server = updates["add_mcp"]
        if isinstance(server, MCPServerConfig):
            config.mcp_servers.append(server)

    if "remove_mcp" in updates:
        name = updates["remove_mcp"]
        config.mcp_servers = [s for s in config.mcp_servers if s.name != name]

    if "set_api_key" in updates:
        key_name, key_value = updates["set_api_key"]
        config.api_keys[key_name] = key_value

    if "set_cost" in updates:
        tool_name, cost = updates["set_cost"]
        config.costs[tool_name] = cost

    # Save
    conn.execute(
        """
        INSERT INTO config (key, value, description, updated_at)
        VALUES ('tools', %s, 'Tool system configuration', NOW())
        ON CONFLICT (key) DO UPDATE SET value = %s, updated_at = NOW()
        """,
        (config.to_json(), config.to_json()),
    )

    return config
