"""
Hexis Tools System - Shell Tools

Tools for shell command execution with sandboxing and safety.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
from typing import Any

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


# Commands that are generally safe for read-only operations
SAFE_COMMANDS = {
    "ls", "pwd", "cat", "head", "tail", "grep", "find", "wc", "date", "echo",
    "whoami", "hostname", "uname", "env", "printenv", "which", "type",
    "file", "stat", "du", "df", "tree", "less", "more", "sort", "uniq",
    "cut", "tr", "sed", "awk", "diff", "comm", "join", "xargs",
    "basename", "dirname", "realpath", "readlink",
    # Git read-only
    "git status", "git log", "git show", "git diff", "git branch",
    "git remote", "git tag", "git describe", "git rev-parse",
    # Python/Node
    "python --version", "python3 --version", "node --version", "npm --version",
}

# Commands that should never be allowed
BLOCKED_COMMANDS = {
    "rm -rf /", "rm -rf /*", "rm -rf ~", "rm -rf ~/*",
    "dd", "mkfs", "fdisk", "parted", "mount", "umount",
    "sudo", "su", "doas",
    "chmod -R 777", "chown -R",
    ":(){ :|:& };:",  # Fork bomb
}


class ShellHandler(ToolHandler):
    """
    Execute shell commands with sandboxing.

    Provides controlled shell access with:
    - Command allow/block lists
    - Working directory restriction
    - Timeout enforcement
    - Output capture and truncation
    """

    def __init__(
        self,
        safe_commands_only: bool = False,
        additional_blocked: set[str] | None = None,
    ):
        """
        Args:
            safe_commands_only: Only allow commands in SAFE_COMMANDS list.
            additional_blocked: Additional command patterns to block.
        """
        self.safe_commands_only = safe_commands_only
        self.additional_blocked = additional_blocked or set()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="shell",
            description=(
                "Execute shell commands. Use for automation, file operations, "
                "running scripts, and system tasks. Commands run in a sandboxed "
                "environment with the workspace as the working directory."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default: 30, max: 120).",
                        "default": 30,
                        "minimum": 1,
                        "maximum": 120,
                    },
                    "env": {
                        "type": "object",
                        "description": "Additional environment variables.",
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["command"],
            },
            category=ToolCategory.SHELL,
            energy_cost=3,
            is_read_only=False,
            requires_approval=True,
            supports_parallel=False,  # Shell commands should be sequential
            allowed_contexts={ToolContext.HEARTBEAT, ToolContext.CHAT},
        )

    def validate(self, arguments: dict[str, Any]) -> list[str]:
        errors = []
        command = arguments.get("command", "")

        if not command or not command.strip():
            errors.append("command is required")

        return errors

    def _is_command_allowed(self, command: str) -> tuple[bool, str | None]:
        """
        Check if a command is allowed to execute.

        Returns (allowed, reason) tuple.
        """
        command_lower = command.lower().strip()

        # Check blocked commands
        for blocked in BLOCKED_COMMANDS | self.additional_blocked:
            if blocked in command_lower:
                return False, f"Command contains blocked pattern: {blocked}"

        # Check for dangerous patterns
        dangerous_patterns = [
            ("> /dev/", "Cannot write to /dev/"),
            ("curl | sh", "Piping curl to shell is blocked"),
            ("wget | sh", "Piping wget to shell is blocked"),
            ("curl | bash", "Piping curl to bash is blocked"),
            ("| bash", "Piping to bash is discouraged"),
            ("&& rm -rf", "Chained rm -rf is blocked"),
        ]

        for pattern, reason in dangerous_patterns:
            if pattern in command_lower:
                return False, reason

        # If safe_commands_only, check whitelist
        if self.safe_commands_only:
            first_word = command.split()[0] if command.split() else ""
            if first_word not in SAFE_COMMANDS:
                # Check for compound commands like "git status"
                first_two = " ".join(command.split()[:2])
                if first_two not in SAFE_COMMANDS:
                    return False, f"Command '{first_word}' not in safe commands list"

        return True, None

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        if not context.allow_shell:
            return ToolResult.error_result(
                "Shell access not allowed in this context",
                ToolErrorType.SHELL_DISABLED,
            )

        command = arguments["command"]
        timeout = min(arguments.get("timeout", 30), 120)
        extra_env = arguments.get("env", {})

        # Validate command
        allowed, reason = self._is_command_allowed(command)
        if not allowed:
            return ToolResult.error_result(
                reason or "Command not allowed",
                ToolErrorType.PERMISSION_DENIED,
            )

        # Build environment
        env = os.environ.copy()
        env.update(extra_env)

        # Determine working directory
        cwd = context.workspace_path or os.getcwd()

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return ToolResult.error_result(
                    f"Command timed out after {timeout} seconds",
                    ToolErrorType.SHELL_TIMEOUT,
                )

            # Decode output
            stdout_str = stdout.decode("utf-8", errors="replace")
            stderr_str = stderr.decode("utf-8", errors="replace")

            # Truncate if too long
            max_output = 50000
            stdout_truncated = False
            stderr_truncated = False

            if len(stdout_str) > max_output:
                stdout_str = stdout_str[:max_output] + "\n...[truncated]"
                stdout_truncated = True

            if len(stderr_str) > max_output:
                stderr_str = stderr_str[:max_output] + "\n...[truncated]"
                stderr_truncated = True

            success = proc.returncode == 0

            return ToolResult(
                success=success,
                output={
                    "command": command,
                    "stdout": stdout_str,
                    "stderr": stderr_str,
                    "exit_code": proc.returncode,
                    "truncated": stdout_truncated or stderr_truncated,
                },
                display_output=stdout_str[:500] if success else f"Error: {stderr_str[:500]}",
                error=stderr_str if not success else None,
                error_type=ToolErrorType.SHELL_EXIT_ERROR if not success else None,
            )

        except Exception as e:
            logger.exception(f"Shell execution failed: {command[:50]}")
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class SafeShellHandler(ShellHandler):
    """
    Shell handler that only allows safe read-only commands.

    Good for heartbeat context where more restrictive access is desired.
    """

    def __init__(self):
        super().__init__(safe_commands_only=True)

    @property
    def spec(self) -> ToolSpec:
        base_spec = super().spec
        return ToolSpec(
            name="safe_shell",
            description=(
                "Execute safe read-only shell commands. Limited to common utilities "
                "like ls, cat, grep, git status, etc. Use for inspecting files and "
                "gathering system information without making changes."
            ),
            parameters=base_spec.parameters,
            category=ToolCategory.SHELL,
            energy_cost=2,  # Lower cost for safe commands
            is_read_only=True,
            requires_approval=False,  # Safe commands don't need approval
            supports_parallel=False,
            allowed_contexts={ToolContext.HEARTBEAT, ToolContext.CHAT},
        )


class ScriptRunnerHandler(ToolHandler):
    """
    Execute a script file with controlled permissions.

    Supports Python, bash, and node scripts.
    """

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="run_script",
            description=(
                "Execute a script file. Supports Python (.py), Bash (.sh), and "
                "Node.js (.js) scripts. Runs with controlled timeout and captures output."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the script file.",
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Arguments to pass to the script.",
                        "default": [],
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default: 60, max: 300).",
                        "default": 60,
                        "minimum": 1,
                        "maximum": 300,
                    },
                },
                "required": ["path"],
            },
            category=ToolCategory.SHELL,
            energy_cost=3,
            is_read_only=False,
            requires_approval=True,
            supports_parallel=False,
            allowed_contexts={ToolContext.HEARTBEAT, ToolContext.CHAT},
        )

    # Map file extensions to interpreters
    INTERPRETERS = {
        ".py": ["python3"],
        ".sh": ["bash"],
        ".bash": ["bash"],
        ".js": ["node"],
        ".mjs": ["node"],
        ".ts": ["npx", "ts-node"],
    }

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        if not context.allow_shell:
            return ToolResult.error_result(
                "Shell access not allowed",
                ToolErrorType.SHELL_DISABLED,
            )

        raw_path = arguments["path"]
        args = arguments.get("args", [])
        timeout = min(arguments.get("timeout", 60), 300)

        # Resolve path
        resolved_path = context.resolve_path(raw_path)

        if not context.is_path_allowed(resolved_path):
            return ToolResult.error_result(
                f"Script path not allowed: {raw_path}",
                ToolErrorType.PATH_NOT_ALLOWED,
            )

        from pathlib import Path
        script_path = Path(resolved_path)

        if not script_path.exists():
            return ToolResult.error_result(
                f"Script not found: {raw_path}",
                ToolErrorType.FILE_NOT_FOUND,
            )

        # Determine interpreter
        suffix = script_path.suffix.lower()
        interpreter = self.INTERPRETERS.get(suffix)

        if not interpreter:
            return ToolResult.error_result(
                f"Unsupported script type: {suffix}",
                ToolErrorType.INVALID_PARAMS,
            )

        # Build command
        cmd = interpreter + [str(script_path)] + args

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=context.workspace_path or script_path.parent,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return ToolResult.error_result(
                    f"Script timed out after {timeout} seconds",
                    ToolErrorType.SHELL_TIMEOUT,
                )

            stdout_str = stdout.decode("utf-8", errors="replace")
            stderr_str = stderr.decode("utf-8", errors="replace")

            # Truncate
            max_output = 50000
            if len(stdout_str) > max_output:
                stdout_str = stdout_str[:max_output] + "\n...[truncated]"
            if len(stderr_str) > max_output:
                stderr_str = stderr_str[:max_output] + "\n...[truncated]"

            success = proc.returncode == 0

            return ToolResult(
                success=success,
                output={
                    "script": str(script_path),
                    "args": args,
                    "stdout": stdout_str,
                    "stderr": stderr_str,
                    "exit_code": proc.returncode,
                },
                display_output=stdout_str[:500] if success else f"Error: {stderr_str[:500]}",
                error=stderr_str if not success else None,
                error_type=ToolErrorType.SHELL_EXIT_ERROR if not success else None,
            )

        except Exception as e:
            logger.exception(f"Script execution failed: {raw_path}")
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


def create_shell_tools(safe_only: bool = False) -> list[ToolHandler]:
    """
    Create shell tool handlers.

    Args:
        safe_only: If True, only include SafeShellHandler (no full shell access).

    Returns:
        List of shell tool handlers.
    """
    if safe_only:
        return [SafeShellHandler()]

    return [
        ShellHandler(),
        SafeShellHandler(),
        ScriptRunnerHandler(),
    ]
