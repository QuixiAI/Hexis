"""
Hexis Tools System - Filesystem Tools

Tools for filesystem operations (read, write, glob, grep).
All operations respect workspace restrictions and file permissions.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
from pathlib import Path
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


class ReadFileHandler(ToolHandler):
    """Read contents of a file."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="read_file",
            description=(
                "Read the contents of a file. Use for reading configuration files, "
                "source code, logs, or any text file. Supports line offset and limit "
                "for reading portions of large files."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file (absolute or relative to workspace).",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Line number to start reading from (0-indexed).",
                        "default": 0,
                        "minimum": 0,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of lines to read (default: 1000).",
                        "default": 1000,
                        "minimum": 1,
                        "maximum": 10000,
                    },
                },
                "required": ["path"],
            },
            category=ToolCategory.FILESYSTEM,
            energy_cost=1,
            is_read_only=True,
        )

    def validate(self, arguments: dict[str, Any]) -> list[str]:
        errors = []
        path = arguments.get("path", "")
        if not path:
            errors.append("path is required")
        return errors

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        if not context.allow_file_read:
            return ToolResult.error_result(
                "File read access not allowed in this context",
                ToolErrorType.PERMISSION_DENIED,
            )

        raw_path = arguments["path"]
        offset = arguments.get("offset", 0)
        limit = arguments.get("limit", 1000)

        # Resolve and validate path
        resolved_path = context.resolve_path(raw_path)

        if not context.is_path_allowed(resolved_path):
            return ToolResult.error_result(
                f"Path not allowed: {raw_path} (outside workspace)",
                ToolErrorType.PATH_NOT_ALLOWED,
            )

        path = Path(resolved_path)

        if not path.exists():
            return ToolResult.error_result(
                f"File not found: {raw_path}",
                ToolErrorType.FILE_NOT_FOUND,
            )

        if path.is_dir():
            return ToolResult.error_result(
                f"Path is a directory, not a file: {raw_path}",
                ToolErrorType.INVALID_PARAMS,
            )

        # Check file size
        try:
            size = path.stat().st_size
            if size > 10 * 1024 * 1024:  # 10MB limit
                return ToolResult.error_result(
                    f"File too large: {size:,} bytes (max 10MB)",
                    ToolErrorType.FILE_TOO_LARGE,
                )
        except OSError as e:
            return ToolResult.error_result(
                f"Cannot access file: {e}",
                ToolErrorType.PERMISSION_DENIED,
            )

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            total_lines = len(lines)

            # Apply offset and limit
            selected_lines = lines[offset : offset + limit]
            content = "".join(selected_lines)

            # Truncate content if too long
            max_chars = 100000
            truncated = False
            if len(content) > max_chars:
                content = content[:max_chars]
                truncated = True

            return ToolResult.success_result(
                output={
                    "path": str(path),
                    "content": content,
                    "total_lines": total_lines,
                    "lines_read": len(selected_lines),
                    "offset": offset,
                    "truncated": truncated,
                },
                display_output=f"Read {len(selected_lines)} lines from {path.name}",
            )

        except PermissionError:
            return ToolResult.error_result(
                f"Permission denied: {raw_path}",
                ToolErrorType.PERMISSION_DENIED,
            )
        except Exception as e:
            logger.exception(f"Failed to read file: {raw_path}")
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class WriteFileHandler(ToolHandler):
    """Write content to a file."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="write_file",
            description=(
                "Write content to a file. Creates the file if it doesn't exist, "
                "or overwrites if it does. Use with caution as this is destructive."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file (absolute or relative to workspace).",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write to the file.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["overwrite", "append"],
                        "description": "Write mode - 'overwrite' replaces, 'append' adds to end.",
                        "default": "overwrite",
                    },
                    "create_dirs": {
                        "type": "boolean",
                        "description": "Create parent directories if they don't exist.",
                        "default": False,
                    },
                },
                "required": ["path", "content"],
            },
            category=ToolCategory.FILESYSTEM,
            energy_cost=2,
            is_read_only=False,
            requires_approval=True,
        )

    def validate(self, arguments: dict[str, Any]) -> list[str]:
        errors = []
        if not arguments.get("path"):
            errors.append("path is required")
        if "content" not in arguments:
            errors.append("content is required")
        return errors

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        if not context.allow_file_write:
            return ToolResult.error_result(
                "File write access not allowed in this context",
                ToolErrorType.PERMISSION_DENIED,
            )

        raw_path = arguments["path"]
        content = arguments["content"]
        mode = arguments.get("mode", "overwrite")
        create_dirs = arguments.get("create_dirs", False)

        # Resolve and validate path
        resolved_path = context.resolve_path(raw_path)

        if not context.is_path_allowed(resolved_path):
            return ToolResult.error_result(
                f"Path not allowed: {raw_path} (outside workspace)",
                ToolErrorType.PATH_NOT_ALLOWED,
            )

        path = Path(resolved_path)

        # Create parent directories if requested
        if create_dirs:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                return ToolResult.error_result(
                    f"Failed to create directories: {e}",
                    ToolErrorType.PERMISSION_DENIED,
                )
        elif not path.parent.exists():
            return ToolResult.error_result(
                f"Directory not found: {path.parent}",
                ToolErrorType.DIRECTORY_NOT_FOUND,
            )

        try:
            write_mode = "w" if mode == "overwrite" else "a"
            with open(path, write_mode, encoding="utf-8") as f:
                f.write(content)

            return ToolResult.success_result(
                output={
                    "path": str(path),
                    "bytes_written": len(content.encode("utf-8")),
                    "mode": mode,
                },
                display_output=f"Wrote {len(content)} chars to {path.name}",
            )

        except PermissionError:
            return ToolResult.error_result(
                f"Permission denied: {raw_path}",
                ToolErrorType.PERMISSION_DENIED,
            )
        except Exception as e:
            logger.exception(f"Failed to write file: {raw_path}")
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class EditFileHandler(ToolHandler):
    """Edit a file using search and replace."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="edit_file",
            description=(
                "Edit a file by replacing specific text. Use for making targeted "
                "changes without rewriting the entire file. More precise than write_file."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file.",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "Exact text to find and replace (must match exactly).",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "Text to replace old_string with.",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace all occurrences (default: first only).",
                        "default": False,
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
            category=ToolCategory.FILESYSTEM,
            energy_cost=2,
            is_read_only=False,
            requires_approval=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        if not context.allow_file_write:
            return ToolResult.error_result(
                "File write access not allowed",
                ToolErrorType.PERMISSION_DENIED,
            )

        raw_path = arguments["path"]
        old_string = arguments["old_string"]
        new_string = arguments["new_string"]
        replace_all = arguments.get("replace_all", False)

        resolved_path = context.resolve_path(raw_path)

        if not context.is_path_allowed(resolved_path):
            return ToolResult.error_result(
                f"Path not allowed: {raw_path}",
                ToolErrorType.PATH_NOT_ALLOWED,
            )

        path = Path(resolved_path)

        if not path.exists():
            return ToolResult.error_result(
                f"File not found: {raw_path}",
                ToolErrorType.FILE_NOT_FOUND,
            )

        try:
            content = path.read_text(encoding="utf-8")

            if old_string not in content:
                return ToolResult.error_result(
                    "old_string not found in file",
                    ToolErrorType.INVALID_PARAMS,
                )

            count = content.count(old_string)

            if replace_all:
                new_content = content.replace(old_string, new_string)
                replacements = count
            else:
                new_content = content.replace(old_string, new_string, 1)
                replacements = 1

            path.write_text(new_content, encoding="utf-8")

            return ToolResult.success_result(
                output={
                    "path": str(path),
                    "replacements": replacements,
                    "occurrences_found": count,
                },
                display_output=f"Made {replacements} replacement(s) in {path.name}",
            )

        except Exception as e:
            logger.exception(f"Failed to edit file: {raw_path}")
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class GlobHandler(ToolHandler):
    """Find files matching a glob pattern."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="glob",
            description=(
                "Find files matching a glob pattern. Use for discovering files, "
                "listing directory contents, or finding specific file types. "
                "Supports ** for recursive matching."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern (e.g., '*.py', 'src/**/*.js', 'data/*.csv').",
                    },
                    "path": {
                        "type": "string",
                        "description": "Base directory to search in (default: workspace root).",
                        "default": ".",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results.",
                        "default": 100,
                        "minimum": 1,
                        "maximum": 1000,
                    },
                    "include_hidden": {
                        "type": "boolean",
                        "description": "Include hidden files (starting with '.').",
                        "default": False,
                    },
                },
                "required": ["pattern"],
            },
            category=ToolCategory.FILESYSTEM,
            energy_cost=1,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        if not context.allow_file_read:
            return ToolResult.error_result(
                "File read access not allowed",
                ToolErrorType.PERMISSION_DENIED,
            )

        pattern = arguments["pattern"]
        raw_path = arguments.get("path", ".")
        max_results = arguments.get("max_results", 100)
        include_hidden = arguments.get("include_hidden", False)

        resolved_path = context.resolve_path(raw_path)

        if not context.is_path_allowed(resolved_path):
            return ToolResult.error_result(
                f"Path not allowed: {raw_path}",
                ToolErrorType.PATH_NOT_ALLOWED,
            )

        base_path = Path(resolved_path)

        if not base_path.exists():
            return ToolResult.error_result(
                f"Directory not found: {raw_path}",
                ToolErrorType.DIRECTORY_NOT_FOUND,
            )

        try:
            matches = []

            # Use Path.glob for recursive patterns
            for match in base_path.glob(pattern):
                # Skip hidden files unless requested
                if not include_hidden:
                    if any(part.startswith(".") for part in match.parts):
                        continue

                # Verify still within allowed path
                if not context.is_path_allowed(str(match)):
                    continue

                try:
                    stat = match.stat()
                    matches.append({
                        "path": str(match),
                        "name": match.name,
                        "is_file": match.is_file(),
                        "is_dir": match.is_dir(),
                        "size": stat.st_size if match.is_file() else None,
                    })
                except OSError:
                    continue  # Skip inaccessible files

                if len(matches) >= max_results:
                    break

            return ToolResult.success_result(
                output={
                    "pattern": pattern,
                    "base_path": str(base_path),
                    "matches": matches,
                    "count": len(matches),
                    "truncated": len(matches) >= max_results,
                },
                display_output=f"Found {len(matches)} files matching '{pattern}'",
            )

        except Exception as e:
            logger.exception(f"Glob failed for pattern: {pattern}")
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class GrepHandler(ToolHandler):
    """Search file contents with regex."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="grep",
            description=(
                "Search file contents using regular expressions. Use for finding "
                "code patterns, searching logs, or locating specific content. "
                "Returns matching lines with context."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regular expression pattern to search for.",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory to search (default: workspace root).",
                        "default": ".",
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "Glob pattern to filter files (e.g., '*.py', '*.js').",
                        "default": "*",
                    },
                    "case_insensitive": {
                        "type": "boolean",
                        "description": "Perform case-insensitive search.",
                        "default": False,
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": "Number of context lines before/after match.",
                        "default": 0,
                        "minimum": 0,
                        "maximum": 10,
                    },
                    "max_matches": {
                        "type": "integer",
                        "description": "Maximum matches to return.",
                        "default": 50,
                        "minimum": 1,
                        "maximum": 500,
                    },
                },
                "required": ["pattern"],
            },
            category=ToolCategory.FILESYSTEM,
            energy_cost=1,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        if not context.allow_file_read:
            return ToolResult.error_result(
                "File read access not allowed",
                ToolErrorType.PERMISSION_DENIED,
            )

        pattern = arguments["pattern"]
        raw_path = arguments.get("path", ".")
        file_pattern = arguments.get("file_pattern", "*")
        case_insensitive = arguments.get("case_insensitive", False)
        context_lines = arguments.get("context_lines", 0)
        max_matches = arguments.get("max_matches", 50)

        resolved_path = context.resolve_path(raw_path)

        if not context.is_path_allowed(resolved_path):
            return ToolResult.error_result(
                f"Path not allowed: {raw_path}",
                ToolErrorType.PATH_NOT_ALLOWED,
            )

        base_path = Path(resolved_path)

        # Compile regex
        try:
            flags = re.IGNORECASE if case_insensitive else 0
            regex = re.compile(pattern, flags)
        except re.error as e:
            return ToolResult.error_result(
                f"Invalid regex pattern: {e}",
                ToolErrorType.INVALID_PARAMS,
            )

        matches = []
        files_searched = 0

        try:
            # Determine files to search
            if base_path.is_file():
                files = [base_path]
            else:
                files = base_path.glob(f"**/{file_pattern}")

            for file_path in files:
                if not file_path.is_file():
                    continue

                if not context.is_path_allowed(str(file_path)):
                    continue

                # Skip binary files
                try:
                    with open(file_path, "rb") as f:
                        chunk = f.read(1024)
                        if b"\x00" in chunk:
                            continue
                except Exception:
                    continue

                files_searched += 1

                try:
                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()

                    for i, line in enumerate(lines):
                        if regex.search(line):
                            # Get context
                            start = max(0, i - context_lines)
                            end = min(len(lines), i + context_lines + 1)
                            context_text = "".join(lines[start:end])

                            matches.append({
                                "file": str(file_path),
                                "line_number": i + 1,
                                "line": line.rstrip("\n"),
                                "context": context_text if context_lines > 0 else None,
                            })

                            if len(matches) >= max_matches:
                                break

                except Exception:
                    continue  # Skip files that can't be read

                if len(matches) >= max_matches:
                    break

            return ToolResult.success_result(
                output={
                    "pattern": pattern,
                    "base_path": str(base_path),
                    "file_pattern": file_pattern,
                    "matches": matches,
                    "count": len(matches),
                    "files_searched": files_searched,
                    "truncated": len(matches) >= max_matches,
                },
                display_output=f"Found {len(matches)} matches in {files_searched} files",
            )

        except Exception as e:
            logger.exception(f"Grep failed for pattern: {pattern}")
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class ListDirectoryHandler(ToolHandler):
    """List directory contents."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="list_directory",
            description=(
                "List contents of a directory. Shows files and subdirectories "
                "with basic metadata like size and type."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path (default: workspace root).",
                        "default": ".",
                    },
                    "show_hidden": {
                        "type": "boolean",
                        "description": "Include hidden files.",
                        "default": False,
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "List recursively (depth 1 only).",
                        "default": False,
                    },
                },
            },
            category=ToolCategory.FILESYSTEM,
            energy_cost=1,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        if not context.allow_file_read:
            return ToolResult.error_result(
                "File read access not allowed",
                ToolErrorType.PERMISSION_DENIED,
            )

        raw_path = arguments.get("path", ".")
        show_hidden = arguments.get("show_hidden", False)
        recursive = arguments.get("recursive", False)

        resolved_path = context.resolve_path(raw_path)

        if not context.is_path_allowed(resolved_path):
            return ToolResult.error_result(
                f"Path not allowed: {raw_path}",
                ToolErrorType.PATH_NOT_ALLOWED,
            )

        dir_path = Path(resolved_path)

        if not dir_path.exists():
            return ToolResult.error_result(
                f"Directory not found: {raw_path}",
                ToolErrorType.DIRECTORY_NOT_FOUND,
            )

        if not dir_path.is_dir():
            return ToolResult.error_result(
                f"Not a directory: {raw_path}",
                ToolErrorType.INVALID_PARAMS,
            )

        try:
            entries = []

            def process_dir(d: Path, depth: int = 0):
                try:
                    for entry in sorted(d.iterdir()):
                        if not show_hidden and entry.name.startswith("."):
                            continue

                        try:
                            stat = entry.stat()
                            entries.append({
                                "name": entry.name,
                                "path": str(entry),
                                "type": "directory" if entry.is_dir() else "file",
                                "size": stat.st_size if entry.is_file() else None,
                                "depth": depth,
                            })

                            if recursive and entry.is_dir() and depth < 1:
                                process_dir(entry, depth + 1)

                        except OSError:
                            continue
                except PermissionError:
                    pass

            process_dir(dir_path)

            return ToolResult.success_result(
                output={
                    "path": str(dir_path),
                    "entries": entries,
                    "count": len(entries),
                },
                display_output=f"Listed {len(entries)} entries in {dir_path.name}",
            )

        except Exception as e:
            logger.exception(f"Failed to list directory: {raw_path}")
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


def create_filesystem_tools() -> list[ToolHandler]:
    """Create all filesystem tool handlers."""
    return [
        ReadFileHandler(),
        WriteFileHandler(),
        EditFileHandler(),
        GlobHandler(),
        GrepHandler(),
        ListDirectoryHandler(),
    ]
