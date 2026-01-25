"""
Hexis Tools System - Base Classes

Provides the foundational abstractions for the tools system:
- ToolSpec: Tool definition exposed to LLMs
- ToolResult: Structured result from tool execution
- ToolHandler: Abstract base class for tool implementations
- ToolExecutionContext: Context passed to tool execution
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .registry import ToolRegistry


class ToolCategory(str, Enum):
    """Categories of tools for organization and policy."""

    MEMORY = "memory"  # Memory operations (recall, remember, etc.)
    WEB = "web"  # Web search, fetch
    FILESYSTEM = "filesystem"  # File read, write, glob, grep
    SHELL = "shell"  # Command execution
    CALENDAR = "calendar"  # Calendar integrations
    EMAIL = "email"  # Email sending
    MESSAGING = "messaging"  # Discord, Slack, Telegram
    EXTERNAL = "external"  # MCP and custom tools


class ToolContext(str, Enum):
    """Contexts in which tools can be executed."""

    HEARTBEAT = "heartbeat"  # Autonomous heartbeat loop
    CHAT = "chat"  # Interactive conversation
    MCP = "mcp"  # External MCP client


class ToolErrorType(str, Enum):
    """Typed error categories for tool execution."""

    # General
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_PARAMS = "invalid_params"
    EXECUTION_FAILED = "execution_failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"

    # Policy
    CONTEXT_DENIED = "context_denied"
    INSUFFICIENT_ENERGY = "insufficient_energy"
    BOUNDARY_VIOLATION = "boundary_violation"
    APPROVAL_REQUIRED = "approval_required"
    DISABLED = "disabled"

    # Filesystem
    FILE_NOT_FOUND = "file_not_found"
    DIRECTORY_NOT_FOUND = "directory_not_found"
    PERMISSION_DENIED = "permission_denied"
    FILE_TOO_LARGE = "file_too_large"
    PATH_NOT_ALLOWED = "path_not_allowed"

    # Shell
    SHELL_DISABLED = "shell_disabled"
    SHELL_TIMEOUT = "shell_timeout"
    SHELL_EXIT_ERROR = "shell_exit_error"

    # Web
    NETWORK_ERROR = "network_error"
    HTTP_ERROR = "http_error"
    FETCH_TIMEOUT = "fetch_timeout"

    # Config
    MISSING_CONFIG = "missing_config"
    MISSING_API_KEY = "missing_api_key"
    MISSING_DEPENDENCY = "missing_dependency"

    # Auth/API
    AUTH_FAILED = "auth_failed"
    RATE_LIMITED = "rate_limited"


@dataclass
class ToolSpec:
    """Tool definition exposed to LLMs."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    category: ToolCategory
    energy_cost: int = 1
    requires_approval: bool = False
    is_read_only: bool = True
    supports_parallel: bool = True
    allowed_contexts: set[ToolContext] = field(
        default_factory=lambda: {ToolContext.HEARTBEAT, ToolContext.CHAT, ToolContext.MCP}
    )

    def to_openai_function(self) -> dict[str, Any]:
        """Convert to OpenAI function calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_mcp_tool(self) -> dict[str, Any]:
        """Convert to MCP tool format."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.parameters,
        }


@dataclass
class ToolResult:
    """Structured result from tool execution."""

    success: bool
    output: Any  # For LLM consumption (JSON-serializable)
    display_output: str | None = None  # For UI display (human-readable)
    error: str | None = None
    error_type: ToolErrorType | None = None
    duration_seconds: float = 0.0
    energy_spent: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_model_output(self) -> str:
        """Format for LLM consumption."""
        import json

        if self.success:
            if isinstance(self.output, str):
                return self.output
            return json.dumps(self.output, indent=2, default=str)
        return f"Error: {self.error}"

    def to_display_output(self) -> str:
        """Format for UI display."""
        if self.display_output:
            return self.display_output
        return self.to_model_output()

    def log_preview(self, max_len: int = 100) -> str:
        """Short preview for logging."""
        output = self.to_display_output()
        if len(output) > max_len:
            return output[:max_len] + "..."
        return output

    @classmethod
    def error_result(
        cls,
        error: str,
        error_type: ToolErrorType = ToolErrorType.EXECUTION_FAILED,
    ) -> "ToolResult":
        """Create an error result."""
        return cls(
            success=False,
            output=None,
            error=error,
            error_type=error_type,
        )

    @classmethod
    def success_result(
        cls,
        output: Any,
        display_output: str | None = None,
    ) -> "ToolResult":
        """Create a success result."""
        return cls(
            success=True,
            output=output,
            display_output=display_output,
        )


@dataclass
class ToolExecutionContext:
    """Context passed to tool execution."""

    tool_context: ToolContext
    call_id: str
    heartbeat_id: str | None = None
    session_id: str | None = None
    energy_available: int | None = None
    workspace_path: str | None = None

    # Policy flags (can be overridden per-context)
    allow_network: bool = True
    allow_shell: bool = False
    allow_file_write: bool = False
    allow_file_read: bool = True

    # Registry reference (set by registry during execution)
    registry: "ToolRegistry | None" = None

    def resolve_path(self, path: str) -> str:
        """Resolve a path relative to workspace."""
        import os

        if self.workspace_path:
            if not os.path.isabs(path):
                return os.path.normpath(os.path.join(self.workspace_path, path))
        return os.path.normpath(path)

    def is_path_allowed(self, path: str) -> bool:
        """Check if a path is within allowed workspace."""
        import os

        if not self.workspace_path:
            return True  # No restriction

        resolved = self.resolve_path(path)
        workspace = os.path.normpath(self.workspace_path)
        return resolved.startswith(workspace)


class ToolHandler(ABC):
    """
    Base class for all tool handlers.

    Subclasses must implement:
    - spec: Property returning ToolSpec
    - execute: Async method performing the tool action
    """

    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        """Return the tool specification."""
        ...

    @abstractmethod
    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        """
        Execute the tool with given arguments.

        Args:
            arguments: Tool arguments (validated against spec.parameters)
            context: Execution context with policy flags and metadata

        Returns:
            ToolResult with success/error and output
        """
        ...

    def validate(self, arguments: dict[str, Any]) -> list[str]:
        """
        Validate arguments against schema.

        Returns list of validation errors (empty if valid).
        Override for custom validation beyond JSON schema.
        """
        errors = []
        schema = self.spec.parameters

        # Check required fields
        required = schema.get("required", [])
        for field in required:
            if field not in arguments:
                errors.append(f"Missing required field: {field}")

        # Check types for provided fields
        properties = schema.get("properties", {})
        for key, value in arguments.items():
            if key not in properties:
                continue  # Skip unknown fields (additionalProperties handling)

            prop_schema = properties[key]
            prop_type = prop_schema.get("type")

            if prop_type == "string" and not isinstance(value, str):
                errors.append(f"Field '{key}' must be a string")
            elif prop_type == "integer" and not isinstance(value, int):
                errors.append(f"Field '{key}' must be an integer")
            elif prop_type == "number" and not isinstance(value, (int, float)):
                errors.append(f"Field '{key}' must be a number")
            elif prop_type == "boolean" and not isinstance(value, bool):
                errors.append(f"Field '{key}' must be a boolean")
            elif prop_type == "array" and not isinstance(value, list):
                errors.append(f"Field '{key}' must be an array")
            elif prop_type == "object" and not isinstance(value, dict):
                errors.append(f"Field '{key}' must be an object")

        return errors


class SyncToolHandler(ToolHandler):
    """
    Wrapper for synchronous tool implementations.

    Subclasses implement execute_sync() instead of execute().
    The wrapper handles running sync code in an executor.
    """

    @abstractmethod
    def execute_sync(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Synchronous execution method."""
        ...

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Run sync method in executor."""
        import asyncio

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self.execute_sync,
            arguments,
            context,
        )


@dataclass
class ToolInvocation:
    """Represents a single tool call for logging/tracking."""

    tool_name: str
    arguments: dict[str, Any]
    context: ToolExecutionContext
    call_id: str
    start_time: float = field(default_factory=time.time)
    result: ToolResult | None = None
    end_time: float | None = None

    @property
    def duration(self) -> float:
        if self.end_time:
            return self.end_time - self.start_time
        return time.time() - self.start_time

    def complete(self, result: ToolResult) -> None:
        self.result = result
        self.end_time = time.time()
        result.duration_seconds = self.duration
