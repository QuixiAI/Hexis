"""
Hexis Tools System - Policy Enforcement

Handles policy checks for tool execution:
- Energy budget enforcement (heartbeat)
- Boundary checks (worldview restrictions)
- Consent requirements (first-use approval)
- Context-specific permissions
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .base import ToolContext, ToolErrorType, ToolResult, ToolSpec
from .config import ToolsConfig

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class PolicyCheckResult:
    """Result of a policy check."""

    allowed: bool
    reason: str | None = None
    error_type: ToolErrorType | None = None

    @classmethod
    def allow(cls) -> "PolicyCheckResult":
        return cls(allowed=True)

    @classmethod
    def deny(cls, reason: str, error_type: ToolErrorType) -> "PolicyCheckResult":
        return cls(allowed=False, reason=reason, error_type=error_type)

    def to_result(self) -> ToolResult:
        """Convert to a ToolResult if denied."""
        if self.allowed:
            raise ValueError("Cannot convert allowed policy to error result")
        return ToolResult.error_result(
            error=self.reason or "Policy denied",
            error_type=self.error_type or ToolErrorType.EXECUTION_FAILED,
        )


class ToolPolicy:
    """
    Policy enforcement for tool execution.

    Checks are performed in order:
    1. Tool enabled (global and context-specific)
    2. Context allowed (spec.allowed_contexts)
    3. Energy budget (heartbeat only)
    4. Boundary restrictions (worldview)
    5. Approval requirements (sensitive tools)
    """

    def __init__(self, pool: "asyncpg.Pool"):
        self.pool = pool

    async def check_all(
        self,
        spec: ToolSpec,
        context: ToolContext,
        config: ToolsConfig,
        energy_available: int | None = None,
    ) -> PolicyCheckResult:
        """Run all policy checks."""

        # 1. Check if tool is enabled
        result = self._check_enabled(spec, context, config)
        if not result.allowed:
            return result

        # 2. Check if context is allowed
        result = self._check_context(spec, context)
        if not result.allowed:
            return result

        # 3. Check energy budget (heartbeat only)
        if context == ToolContext.HEARTBEAT:
            result = self._check_energy(spec, config, energy_available)
            if not result.allowed:
                return result

        # 4. Check boundary restrictions
        result = await self._check_boundaries(spec)
        if not result.allowed:
            return result

        # 5. Check approval requirements
        result = await self._check_approval(spec, context)
        if not result.allowed:
            return result

        return PolicyCheckResult.allow()

    def _check_enabled(
        self,
        spec: ToolSpec,
        context: ToolContext,
        config: ToolsConfig,
    ) -> PolicyCheckResult:
        """Check if tool is enabled in configuration."""
        if not config.is_tool_enabled_for_context(spec.name, spec.category, context):
            return PolicyCheckResult.deny(
                f"Tool '{spec.name}' is disabled",
                ToolErrorType.DISABLED,
            )
        return PolicyCheckResult.allow()

    def _check_context(
        self,
        spec: ToolSpec,
        context: ToolContext,
    ) -> PolicyCheckResult:
        """Check if tool is allowed in this context."""
        if context not in spec.allowed_contexts:
            return PolicyCheckResult.deny(
                f"Tool '{spec.name}' not allowed in {context.value} context",
                ToolErrorType.CONTEXT_DENIED,
            )
        return PolicyCheckResult.allow()

    def _check_energy(
        self,
        spec: ToolSpec,
        config: ToolsConfig,
        energy_available: int | None,
    ) -> PolicyCheckResult:
        """Check energy budget for heartbeat context."""
        if energy_available is None:
            return PolicyCheckResult.allow()

        cost = config.get_energy_cost(spec.name, spec.energy_cost)

        # Check max energy per tool limit
        ctx_override = config.get_context_overrides(ToolContext.HEARTBEAT)
        if ctx_override.max_energy_per_tool is not None:
            if cost > ctx_override.max_energy_per_tool:
                return PolicyCheckResult.deny(
                    f"Tool '{spec.name}' cost ({cost}) exceeds max per tool ({ctx_override.max_energy_per_tool})",
                    ToolErrorType.INSUFFICIENT_ENERGY,
                )

        # Check available energy
        if cost > energy_available:
            return PolicyCheckResult.deny(
                f"Insufficient energy: need {cost}, have {energy_available}",
                ToolErrorType.INSUFFICIENT_ENERGY,
            )

        return PolicyCheckResult.allow()

    async def _check_boundaries(self, spec: ToolSpec) -> PolicyCheckResult:
        """Check if any worldview boundary restricts this tool."""
        async with self.pool.acquire() as conn:
            # Check for boundary that restricts this specific tool
            boundary = await conn.fetchval(
                """
                SELECT content FROM memories
                WHERE type = 'worldview'
                  AND metadata->>'category' = 'boundary'
                  AND metadata->'restricts_tools' ? $1
                  AND status = 'active'
                LIMIT 1
                """,
                spec.name,
            )

            if boundary:
                return PolicyCheckResult.deny(
                    f"Boundary restriction: {boundary}",
                    ToolErrorType.BOUNDARY_VIOLATION,
                )

            # Check for boundary that restricts this category
            boundary = await conn.fetchval(
                """
                SELECT content FROM memories
                WHERE type = 'worldview'
                  AND metadata->>'category' = 'boundary'
                  AND metadata->'restricts_categories' ? $1
                  AND status = 'active'
                LIMIT 1
                """,
                spec.category.value,
            )

            if boundary:
                return PolicyCheckResult.deny(
                    f"Boundary restriction on category '{spec.category.value}': {boundary}",
                    ToolErrorType.BOUNDARY_VIOLATION,
                )

        return PolicyCheckResult.allow()

    async def _check_approval(
        self,
        spec: ToolSpec,
        context: ToolContext,
    ) -> PolicyCheckResult:
        """Check if tool requires approval and if it's been granted."""
        if not spec.requires_approval:
            return PolicyCheckResult.allow()

        # In chat context, we assume user interaction provides implicit approval
        if context == ToolContext.CHAT:
            return PolicyCheckResult.allow()

        # For heartbeat, check if tool has been approved
        async with self.pool.acquire() as conn:
            approved = await conn.fetchval(
                """
                SELECT 1 FROM config
                WHERE key = 'tools.approvals'
                  AND value ? $1
                """,
                spec.name,
            )

            if not approved:
                return PolicyCheckResult.deny(
                    f"Tool '{spec.name}' requires approval for autonomous use",
                    ToolErrorType.APPROVAL_REQUIRED,
                )

        return PolicyCheckResult.allow()


async def grant_tool_approval(pool: "asyncpg.Pool", tool_name: str) -> None:
    """Grant approval for autonomous use of a tool."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO config (key, value, description, updated_at)
            VALUES ('tools.approvals', jsonb_build_array($1), 'Approved tools for autonomous use', NOW())
            ON CONFLICT (key) DO UPDATE SET
                value = config.value || jsonb_build_array($1),
                updated_at = NOW()
            WHERE NOT config.value ? $1
            """,
            tool_name,
        )


async def revoke_tool_approval(pool: "asyncpg.Pool", tool_name: str) -> None:
    """Revoke approval for autonomous use of a tool."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE config
            SET value = value - $1, updated_at = NOW()
            WHERE key = 'tools.approvals'
            """,
            tool_name,
        )


async def list_approved_tools(pool: "asyncpg.Pool") -> list[str]:
    """List tools approved for autonomous use."""
    async with pool.acquire() as conn:
        row = await conn.fetchval(
            "SELECT value FROM config WHERE key = 'tools.approvals'"
        )
        if row:
            import json

            try:
                return json.loads(row) if isinstance(row, str) else list(row)
            except (json.JSONDecodeError, TypeError):
                return []
        return []


async def create_tool_boundary(
    pool: "asyncpg.Pool",
    content: str,
    restricts_tools: list[str] | None = None,
    restricts_categories: list[str] | None = None,
) -> str:
    """Create a worldview boundary that restricts tools."""
    import json
    from uuid import uuid4

    metadata = {"category": "boundary"}
    if restricts_tools:
        metadata["restricts_tools"] = restricts_tools
    if restricts_categories:
        metadata["restricts_categories"] = restricts_categories

    async with pool.acquire() as conn:
        memory_id = await conn.fetchval(
            """
            INSERT INTO memories (type, content, embedding, metadata, importance)
            VALUES ('worldview', $1, (get_embedding(ARRAY[$1]))[1], $2::jsonb, 0.9)
            RETURNING id
            """,
            content,
            json.dumps(metadata),
        )
        return str(memory_id)
