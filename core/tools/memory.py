"""
Hexis Tools System - Memory Tools

Tools for memory operations (recall, remember, etc.).
These wrap the existing CognitiveMemory API.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from .base import (
    ToolCategory,
    ToolContext,
    ToolErrorType,
    ToolExecutionContext,
    ToolHandler,
    ToolResult,
    ToolSpec,
)


class RecallHandler(ToolHandler):
    """Search memories by semantic similarity."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="recall",
            description=(
                "Search memories by semantic similarity. Use this to find memories "
                "related to a topic, concept, or question. Returns the most relevant "
                "memories based on meaning, not just keyword matching."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language query describing what you want to remember.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of memories to return (default: 5, max: 20)",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 20,
                    },
                    "memory_types": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["episodic", "semantic", "procedural", "strategic"],
                        },
                        "description": "Filter by memory types. Omit to search all types.",
                    },
                    "min_importance": {
                        "type": "number",
                        "description": "Minimum importance score (0.0-1.0).",
                        "default": 0.0,
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                },
                "required": ["query"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        from core.cognitive_memory_api import CognitiveMemory, MemoryType

        query = arguments["query"]
        limit = min(arguments.get("limit", 5), 20)
        memory_types_raw = arguments.get("memory_types")
        min_importance = arguments.get("min_importance", 0.0)

        # Parse memory types
        memory_types = None
        if memory_types_raw:
            try:
                memory_types = [MemoryType(t) for t in memory_types_raw]
            except ValueError as e:
                return ToolResult.error_result(
                    f"Invalid memory type: {e}",
                    ToolErrorType.INVALID_PARAMS,
                )

        try:
            # Get connection from registry's pool
            async with context.registry.pool.acquire() as conn:
                # Use fast_recall directly for efficiency
                rows = await conn.fetch(
                    "SELECT * FROM fast_recall($1, $2)",
                    query,
                    limit,
                )

                memories = []
                for row in rows:
                    mem = dict(row)
                    # Apply filters
                    if memory_types:
                        if mem.get("type") not in [t.value for t in memory_types]:
                            continue
                    if mem.get("importance", 0) < min_importance:
                        continue
                    memories.append({
                        "memory_id": str(mem.get("id")),
                        "content": mem.get("content"),
                        "type": mem.get("type"),
                        "similarity": mem.get("similarity"),
                        "importance": mem.get("importance"),
                    })

                # Touch accessed memories
                if memories:
                    memory_ids = [m["memory_id"] for m in memories]
                    await conn.execute(
                        "SELECT touch_memories($1::uuid[])",
                        memory_ids,
                    )

            return ToolResult.success_result(
                output={"memories": memories, "count": len(memories), "query": query},
                display_output=f"Found {len(memories)} memories for '{query}'",
            )

        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class RememberHandler(ToolHandler):
    """Store a new memory."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="remember",
            description=(
                "Store a new memory. Use this to save important information, "
                "events, or learnings for future recall."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The content to remember.",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["episodic", "semantic", "procedural", "strategic"],
                        "default": "episodic",
                        "description": "Type of memory to create.",
                    },
                    "importance": {
                        "type": "number",
                        "description": "Importance score (0.0-1.0).",
                        "default": 0.5,
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                    "concepts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Concepts to link this memory to.",
                    },
                },
                "required": ["content"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=False,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        content = arguments["content"]
        memory_type = arguments.get("type", "episodic")
        importance = arguments.get("importance", 0.5)
        concepts = arguments.get("concepts", [])

        try:
            async with context.registry.pool.acquire() as conn:
                # Create the memory
                memory_id = await conn.fetchval(
                    """
                    SELECT create_memory(
                        p_type := $1::memory_type,
                        p_content := $2,
                        p_importance := $3
                    )
                    """,
                    memory_type,
                    content,
                    importance,
                )

                # Link concepts
                for concept in concepts:
                    await conn.execute(
                        "SELECT link_memory_to_concept($1::uuid, $2)",
                        memory_id,
                        concept,
                    )

            return ToolResult.success_result(
                output={"memory_id": str(memory_id), "content": content[:100]},
                display_output=f"Stored memory: {content[:50]}...",
            )

        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class SenseMemoryAvailabilityHandler(ToolHandler):
    """Quick feeling-of-knowing check before full recall."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="sense_memory_availability",
            description=(
                "Sense whether you likely have relevant memories before doing a full recall. "
                "Use this for a quick feeling-of-knowing check."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Topic to check memory availability for.",
                    },
                },
                "required": ["query"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=0,  # Free - lightweight check
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        query = arguments["query"]

        try:
            async with context.registry.pool.acquire() as conn:
                result = await conn.fetchrow(
                    "SELECT * FROM sense_memory_availability($1)",
                    query,
                )

                if result:
                    return ToolResult.success_result(
                        output=dict(result),
                        display_output=f"Memory availability: {result.get('activation_strength', 0):.2f}",
                    )
                else:
                    return ToolResult.success_result(
                        output={"has_memories": False, "activation_strength": 0.0},
                        display_output="No strong memory activation",
                    )

        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class ExploreConceptHandler(ToolHandler):
    """Explore memories connected to a concept."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="explore_concept",
            description=(
                "Explore memories connected to a specific concept. Shows how different "
                "memories relate to an idea and what other concepts are connected."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "concept": {
                        "type": "string",
                        "description": "The concept to explore.",
                    },
                    "include_related": {
                        "type": "boolean",
                        "description": "Also return memories linked to related concepts.",
                        "default": True,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum memories to return.",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 20,
                    },
                },
                "required": ["concept"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        concept = arguments["concept"]
        include_related = arguments.get("include_related", True)
        limit = arguments.get("limit", 5)

        try:
            async with context.registry.pool.acquire() as conn:
                # Find memories linked to concept
                rows = await conn.fetch(
                    """
                    SELECT memory_id, memory_content, memory_type, memory_importance, link_strength
                    FROM find_memories_by_concept($1, $2)
                    """,
                    concept,
                    limit,
                )

                memories = [
                    {
                        "memory_id": str(row["memory_id"]),
                        "content": row["memory_content"],
                        "type": row["memory_type"],
                        "importance": row["memory_importance"],
                        "concept_strength": row["link_strength"],
                    }
                    for row in rows
                ]

                related_concepts = []
                if include_related and memories:
                    memory_ids = [m["memory_id"] for m in memories]
                    related_rows = await conn.fetch(
                        """
                        SELECT name, shared_memories
                        FROM find_related_concepts_for_memories($1::uuid[], $2, 10)
                        """,
                        memory_ids,
                        concept,
                    )
                    related_concepts = [dict(r) for r in related_rows]

            return ToolResult.success_result(
                output={
                    "concept": concept,
                    "memories": memories,
                    "related_concepts": related_concepts,
                    "count": len(memories),
                },
                display_output=f"Found {len(memories)} memories for concept '{concept}'",
            )

        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class GetProceduresHandler(ToolHandler):
    """Retrieve procedural memories for a task."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="get_procedures",
            description=(
                "Retrieve procedural memories (how-to knowledge) for a specific task. "
                "Returns step-by-step instructions and prerequisites."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "The task you want to know how to do.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum procedures to return.",
                        "default": 3,
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": ["task"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        task = arguments["task"]
        limit = arguments.get("limit", 3)

        try:
            async with context.registry.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM fast_recall($1, $2)
                    WHERE type = 'procedural'
                    """,
                    task,
                    limit * 2,  # Fetch more to filter
                )

                procedures = [
                    {
                        "memory_id": str(row["id"]),
                        "content": row["content"],
                        "similarity": row.get("similarity"),
                    }
                    for row in rows[:limit]
                ]

            return ToolResult.success_result(
                output={"procedures": procedures, "count": len(procedures), "task": task},
                display_output=f"Found {len(procedures)} procedures for '{task}'",
            )

        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class GetStrategiesHandler(ToolHandler):
    """Retrieve strategic memories for a situation."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="get_strategies",
            description=(
                "Retrieve strategic memories (patterns, heuristics, lessons learned) "
                "applicable to a situation. These are meta-level insights about what works."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "situation": {
                        "type": "string",
                        "description": "The situation you need strategic guidance for.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum strategies to return.",
                        "default": 3,
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": ["situation"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        situation = arguments["situation"]
        limit = arguments.get("limit", 3)

        try:
            async with context.registry.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM fast_recall($1, $2)
                    WHERE type = 'strategic'
                    """,
                    situation,
                    limit * 2,
                )

                strategies = [
                    {
                        "memory_id": str(row["id"]),
                        "content": row["content"],
                        "similarity": row.get("similarity"),
                    }
                    for row in rows[:limit]
                ]

            return ToolResult.success_result(
                output={"strategies": strategies, "count": len(strategies), "situation": situation},
                display_output=f"Found {len(strategies)} strategies for '{situation}'",
            )

        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class CreateGoalHandler(ToolHandler):
    """Create a new goal for the agent."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="create_goal",
            description=(
                "Create a new goal for the agent to pursue. Use this for reminders, "
                "TODOs, or longer-term objectives."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short goal title.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional longer description.",
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["active", "queued", "backburner"],
                        "default": "queued",
                        "description": "Desired priority.",
                    },
                    "source": {
                        "type": "string",
                        "enum": ["curiosity", "user_request", "identity", "derived", "external"],
                        "default": "user_request",
                        "description": "Why this goal exists.",
                    },
                },
                "required": ["title"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=False,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        title = arguments["title"]
        description = arguments.get("description")
        priority = arguments.get("priority", "queued")
        source = arguments.get("source", "user_request")

        try:
            async with context.registry.pool.acquire() as conn:
                goal_id = await conn.fetchval(
                    """
                    SELECT create_goal(
                        p_title := $1,
                        p_description := $2,
                        p_priority := $3,
                        p_source := $4
                    )
                    """,
                    title,
                    description,
                    priority,
                    source,
                )

            return ToolResult.success_result(
                output={"goal_id": str(goal_id), "title": title, "priority": priority},
                display_output=f"Created goal: {title}",
            )

        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class ScheduleTaskHandler(ToolHandler):
    """Create a scheduled (cron-like) task."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="schedule_task",
            description=(
                "Create a scheduled task (cron-like). Use for recurring reminders or timed actions."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short task name."},
                    "description": {"type": "string", "description": "Optional longer description."},
                    "schedule_kind": {
                        "type": "string",
                        "enum": ["once", "interval", "daily", "weekly"],
                        "description": "Schedule type.",
                    },
                    "schedule": {"type": "object", "description": "Schedule details for the selected type."},
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone name (e.g., America/Los_Angeles).",
                    },
                    "action_kind": {
                        "type": "string",
                        "enum": ["queue_user_message", "create_goal"],
                        "description": "Action to perform when the schedule fires.",
                    },
                    "action_payload": {"type": "object", "description": "Action payload."},
                    "max_runs": {
                        "type": "integer",
                        "description": "Optional max number of runs before auto-disable.",
                    },
                },
                "required": ["name", "schedule_kind", "schedule", "action_kind", "action_payload"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=False,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        import json

        name = arguments["name"]
        schedule_kind = arguments["schedule_kind"]
        schedule = arguments.get("schedule") or {}
        action_kind = arguments["action_kind"]
        action_payload = arguments.get("action_payload") or {}
        timezone = arguments.get("timezone")
        description = arguments.get("description")
        max_runs = arguments.get("max_runs")

        try:
            async with context.registry.pool.acquire() as conn:
                task_id = await conn.fetchval(
                    """
                    SELECT create_scheduled_task(
                        $1,
                        $2,
                        $3::jsonb,
                        $4,
                        $5::jsonb,
                        $6,
                        $7,
                        'active',
                        $8,
                        'agent'
                    )
                    """,
                    name,
                    schedule_kind,
                    json.dumps(schedule),
                    action_kind,
                    json.dumps(action_payload),
                    timezone,
                    description,
                    max_runs,
                )

            return ToolResult.success_result(
                output={"task_id": str(task_id), "name": name},
                display_output=f"Scheduled task: {name}",
            )

        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class QueueUserMessageHandler(ToolHandler):
    """Queue a message for the user."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="queue_user_message",
            description="Queue a message for external delivery to the user.",
            parameters={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Message body for the user.",
                    },
                    "intent": {
                        "type": "string",
                        "description": "Optional intent/category (e.g. 'reminder', 'status', 'question').",
                    },
                },
                "required": ["message"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=0,  # Free - just queuing
            is_read_only=False,
            allowed_contexts={ToolContext.HEARTBEAT},  # Only for autonomous use
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        import json

        message = arguments["message"]
        intent = arguments.get("intent")

        try:
            async with context.registry.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO external_calls (call_type, input, status)
                    VALUES ('outbox_message', $1::jsonb, 'pending')
                    """,
                    json.dumps({"message": message, "intent": intent}),
                )

            return ToolResult.success_result(
                output={"queued": True, "message": message[:50]},
                display_output=f"Queued message: {message[:50]}...",
            )

        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


def create_memory_tools() -> list[ToolHandler]:
    """Create all memory tool handlers."""
    return [
        RecallHandler(),
        RememberHandler(),
        SenseMemoryAvailabilityHandler(),
        ExploreConceptHandler(),
        GetProceduresHandler(),
        GetStrategiesHandler(),
        CreateGoalHandler(),
        ScheduleTaskHandler(),
        QueueUserMessageHandler(),
    ]
