from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from core.cognitive_memory_api import CognitiveMemory, GoalPriority, GoalSource, MemoryType
from core.memory_tools import MEMORY_TOOLS


def get_tool_definitions(allowed: list[str] | None = None) -> list[dict[str, Any]]:
    if allowed is None:
        return MEMORY_TOOLS
    allowed_set = {name for name in allowed if isinstance(name, str)}
    return [
        tool
        for tool in MEMORY_TOOLS
        if tool.get("function", {}).get("name") in allowed_set
    ]


async def execute_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    mem_client: CognitiveMemory,
) -> dict[str, Any]:
    handlers = {
        "recall": _handle_recall,
        "sense_memory_availability": _handle_sense_memory_availability,
        "request_background_search": _handle_request_background_search,
        "recall_recent": _handle_recall_recent,
        "recall_episode": _handle_recall_episode,
        "explore_concept": _handle_explore_concept,
        "explore_cluster": _handle_explore_cluster,
        "get_procedures": _handle_get_procedures,
        "get_strategies": _handle_get_strategies,
        "list_recent_episodes": _handle_list_recent_episodes,
        "create_goal": _handle_create_goal,
        "schedule_task": _handle_schedule_task,
        "list_scheduled_tasks": _handle_list_scheduled_tasks,
        "update_scheduled_task": _handle_update_scheduled_task,
        "delete_scheduled_task": _handle_delete_scheduled_task,
        "queue_user_message": _handle_queue_user_message,
    }
    handler = handlers.get(tool_name)
    if not handler:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        return await handler(arguments or {}, mem_client)
    except Exception as exc:
        return {"error": str(exc)}


async def _handle_recall(args: dict[str, Any], mem_client: CognitiveMemory) -> dict[str, Any]:
    query = str(args.get("query", "")).strip()
    limit = min(int(args.get("limit", 5)), 20)
    memory_types = args.get("memory_types")
    min_importance = float(args.get("min_importance", 0.0) or 0.0)

    parsed_types = None
    if isinstance(memory_types, list) and memory_types:
        parsed_types = [MemoryType(str(t)) for t in memory_types]

    result = await mem_client.recall(
        query,
        limit=limit,
        memory_types=parsed_types,
        min_importance=min_importance,
        include_partial=False,
    )
    if result.memories:
        await mem_client.touch_memories([m.id for m in result.memories])
    memories = [
        {
            "memory_id": str(m.id),
            "content": m.content,
            "memory_type": m.type.value,
            "score": m.similarity,
            "source": m.source,
            "importance": m.importance,
            "trust_level": m.trust_level,
            "source_attribution": m.source_attribution,
        }
        for m in result.memories
    ]
    return {"memories": memories, "count": len(memories), "query": query}


async def _handle_sense_memory_availability(args: dict[str, Any], mem_client: CognitiveMemory) -> dict[str, Any]:
    query = str(args.get("query", "")).strip()
    if not query:
        return {"error": "Missing query"}
    return await mem_client.sense_memory_availability(query)


async def _handle_request_background_search(args: dict[str, Any], mem_client: CognitiveMemory) -> dict[str, Any]:
    query = str(args.get("query", "")).strip()
    if not query:
        return {"error": "Missing query"}
    activation_id = await mem_client.request_background_search(query)
    return {"activation_id": str(activation_id) if activation_id else None}


async def _handle_recall_recent(args: dict[str, Any], mem_client: CognitiveMemory) -> dict[str, Any]:
    limit = min(int(args.get("limit", 5)), 20)
    memory_types = args.get("memory_types")
    by_access = bool(args.get("by_access", True))

    mt = None
    if isinstance(memory_types, list) and memory_types:
        mt = MemoryType(str(memory_types[0]))

    rows = await mem_client.recall_recent(limit=limit, memory_type=mt)
    if by_access and rows:
        await mem_client.touch_memories([m.id for m in rows])
    results = [
        {
            "memory_id": str(m.id),
            "content": m.content,
            "memory_type": m.type.value,
            "importance": m.importance,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "last_accessed": None,
            "trust_level": m.trust_level,
            "source_attribution": m.source_attribution,
        }
        for m in rows
    ]
    return {"memories": results, "count": len(results)}


async def _handle_recall_episode(args: dict[str, Any], mem_client: CognitiveMemory) -> dict[str, Any]:
    raw_id = str(args.get("episode_id", "")).strip()
    if not raw_id:
        return {"error": "Missing episode_id"}
    episode_id = UUID(raw_id)
    rows = await mem_client.recall_episode(episode_id)
    memories = [
        {
            "memory_id": str(m.id),
            "content": m.content,
            "memory_type": m.type.value,
            "importance": m.importance,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "trust_level": m.trust_level,
            "source_attribution": m.source_attribution,
        }
        for m in rows
    ]
    return {"episode_id": raw_id, "memories": memories, "count": len(memories)}


async def _handle_explore_concept(args: dict[str, Any], mem_client: CognitiveMemory) -> dict[str, Any]:
    concept = str(args.get("concept", "")).strip()
    include_related = bool(args.get("include_related", True))
    limit = min(int(args.get("limit", 5)), 20)
    if not concept:
        return {"error": "Missing concept"}

    direct = await mem_client.find_by_concept(concept, limit=limit)
    combined: dict[str, dict[str, Any]] = {
        str(m.id): {
            "memory_id": str(m.id),
            "content": m.content,
            "memory_type": m.type.value,
            "importance": m.importance,
            "trust_level": m.trust_level,
            "source_attribution": m.source_attribution,
            "source": "concept",
            "score": None,
        }
        for m in direct
    }
    if include_related:
        rr = await mem_client.recall(concept, limit=limit, include_partial=False)
        for m in rr.memories:
            combined.setdefault(
                str(m.id),
                {
                    "memory_id": str(m.id),
                    "content": m.content,
                    "memory_type": m.type.value,
                    "importance": m.importance,
                    "trust_level": m.trust_level,
                    "source_attribution": m.source_attribution,
                    "source": m.source,
                    "score": m.similarity,
                },
            )
    out = list(combined.values())[:limit]
    return {"concept": concept, "memories": out, "count": len(out)}


async def _handle_explore_cluster(args: dict[str, Any], mem_client: CognitiveMemory) -> dict[str, Any]:
    query = str(args.get("query", "")).strip()
    limit = min(int(args.get("limit", 3)), 10)
    if not query:
        return {"clusters": [], "count": 0, "query": query}
    result_clusters = await mem_client.explore_clusters(query, limit=limit, sample_size=3)
    return {"clusters": result_clusters, "count": len(result_clusters), "query": query}


async def _handle_get_procedures(args: dict[str, Any], mem_client: CognitiveMemory) -> dict[str, Any]:
    task = str(args.get("task", "")).strip()
    limit = min(int(args.get("limit", 3)), 10)
    if not task:
        return {"procedures": [], "count": 0, "task": task}
    res = await mem_client.recall(task, limit=limit, memory_types=[MemoryType.PROCEDURAL], include_partial=False)
    return {
        "procedures": [{"memory_id": str(m.id), "content": m.content, "score": m.similarity} for m in res.memories],
        "count": len(res.memories),
        "task": task,
    }


async def _handle_get_strategies(args: dict[str, Any], mem_client: CognitiveMemory) -> dict[str, Any]:
    situation = str(args.get("situation", "")).strip()
    limit = min(int(args.get("limit", 3)), 10)
    if not situation:
        return {"strategies": [], "count": 0, "situation": situation}
    res = await mem_client.recall(situation, limit=limit, memory_types=[MemoryType.STRATEGIC], include_partial=False)
    return {
        "strategies": [{"memory_id": str(m.id), "content": m.content, "score": m.similarity} for m in res.memories],
        "count": len(res.memories),
        "situation": situation,
    }


async def _handle_list_recent_episodes(args: dict[str, Any], mem_client: CognitiveMemory) -> dict[str, Any]:
    limit = min(int(args.get("limit", 5)), 20)
    episodes = await mem_client.list_recent_episodes(limit=limit)
    return {"episodes": episodes, "count": len(episodes)}


async def _handle_create_goal(args: dict[str, Any], mem_client: CognitiveMemory) -> dict[str, Any]:
    title = str(args.get("title", "")).strip()
    if not title:
        return {"error": "Missing title"}
    description = args.get("description")
    priority = str(args.get("priority") or GoalPriority.QUEUED.value)
    source = str(args.get("source") or GoalSource.USER_REQUEST.value)
    due_at_raw = args.get("due_at")

    due_at = None
    if isinstance(due_at_raw, str) and due_at_raw.strip():
        try:
            due_at = datetime.fromisoformat(due_at_raw)
        except Exception:
            due_at = None

    goal_id = await mem_client.create_goal(
        title,
        description=description,
        source=source,
        priority=priority,
        due_at=due_at,
    )
    return {"goal_id": str(goal_id), "title": title}


async def _handle_schedule_task(args: dict[str, Any], mem_client: CognitiveMemory) -> dict[str, Any]:
    name = str(args.get("name", "")).strip()
    schedule_kind = str(args.get("schedule_kind", "")).strip()
    schedule = args.get("schedule") or {}
    action_kind = str(args.get("action_kind", "")).strip()
    action_payload = args.get("action_payload") or {}
    timezone = args.get("timezone")
    description = args.get("description")
    max_runs = args.get("max_runs")

    if not name:
        return {"error": "Missing name"}
    if not schedule_kind:
        return {"error": "Missing schedule_kind"}
    if not isinstance(schedule, dict):
        return {"error": "schedule must be an object"}
    if not action_kind:
        return {"error": "Missing action_kind"}
    if not isinstance(action_payload, dict):
        return {"error": "action_payload must be an object"}

    task_id = await mem_client.create_scheduled_task(
        name,
        schedule_kind=schedule_kind,
        schedule=schedule,
        action_kind=action_kind,
        action_payload=action_payload,
        timezone=timezone,
        description=description,
        max_runs=max_runs,
    )
    return {"task_id": str(task_id), "name": name, "schedule_kind": schedule_kind, "action_kind": action_kind}


async def _handle_list_scheduled_tasks(args: dict[str, Any], mem_client: CognitiveMemory) -> dict[str, Any]:
    status = args.get("status")
    due_before = args.get("due_before")
    limit = args.get("limit", 50)
    tasks = await mem_client.list_scheduled_tasks(status=status, due_before=due_before, limit=int(limit))
    return {"tasks": tasks, "count": len(tasks)}


async def _handle_update_scheduled_task(args: dict[str, Any], mem_client: CognitiveMemory) -> dict[str, Any]:
    task_id = str(args.get("task_id", "")).strip()
    if not task_id:
        return {"error": "Missing task_id"}
    updated = await mem_client.update_scheduled_task(
        task_id,
        name=args.get("name"),
        description=args.get("description"),
        schedule_kind=args.get("schedule_kind"),
        schedule=args.get("schedule"),
        timezone=args.get("timezone"),
        action_kind=args.get("action_kind"),
        action_payload=args.get("action_payload"),
        status=args.get("status"),
        max_runs=args.get("max_runs"),
    )
    return {"task": updated}


async def _handle_delete_scheduled_task(args: dict[str, Any], mem_client: CognitiveMemory) -> dict[str, Any]:
    task_id = str(args.get("task_id", "")).strip()
    if not task_id:
        return {"error": "Missing task_id"}
    hard_delete = bool(args.get("hard_delete", False))
    reason = args.get("reason")
    ok = await mem_client.delete_scheduled_task(task_id, hard_delete=hard_delete, reason=reason)
    return {"deleted": bool(ok), "task_id": task_id}


async def _handle_queue_user_message(args: dict[str, Any], mem_client: CognitiveMemory) -> dict[str, Any]:
    message = str(args.get("message", "")).strip()
    if not message:
        return {"error": "Missing message"}
    intent = args.get("intent")
    context = args.get("context")
    outbox_message = await mem_client.queue_user_message(message, intent=intent, context=context)
    return {"outbox_message": outbox_message, "queued": True}
