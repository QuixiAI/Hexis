from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.agent_api import db_dsn_from_env, get_agent_profile_context
from core.cognitive_memory_api import CognitiveMemory, MemoryType, format_context_for_prompt
from core.llm import chat_completion, normalize_llm_config
from services.prompt_resources import compose_personhood_prompt
from services.tooling import execute_tool, get_tool_definitions


BASE_SYSTEM_PROMPT = """You are an AI assistant with access to a persistent memory system. You can remember past conversations, learned information, and personal details about the user.

## Your Memory Capabilities

You have access to several memory tools that allow you to search and explore your memories:

1. **recall** - Search memories by semantic similarity. Use this when you need to remember something specific.
2. **sense_memory_availability** - Quick feeling-of-knowing check before a full recall.
3. **request_background_search** - Ask the system to keep searching after a failed recall.
4. **recall_recent** - Get recently accessed or created memories. Use for context about recent conversations.
5. **recall_episode** - Retrieve all memories from a specific episode (a coherent interaction).
6. **explore_concept** - Explore memories connected to a concept and find related ideas.
7. **explore_cluster** - Explore thematic clusters of memories.
8. **get_procedures** - Find procedural/how-to knowledge for tasks.
9. **get_strategies** - Find strategic patterns and lessons learned.
10. **list_recent_episodes** - List recent episodes for orientation.
11. **create_goal** - Create a queued goal/reminder for the agent to pursue later.
12. **queue_user_message** - Return a user message payload (delivery handled externally).

## When to Use Memory Tools

- When the user refers to past conversations ("remember when...", "as we discussed...")
- When you need personal information about the user (preferences, projects, etc.)
- When you're unsure if you know something the user expects you to know
- When the user asks about your memories or what you remember
- When context from the past would help you give a better answer

## Memory Context

Before each user message, you may receive [RELEVANT MEMORIES] - these are automatically retrieved memories that might be relevant. Use them naturally in your responses without explicitly citing them unless asked.

## Guidelines

- Be natural about using your memories - don't constantly announce that you're searching
- If you don't find relevant memories, that's fine - just respond based on the current conversation
- When you learn new information about the user, it will be automatically remembered
- You can make multiple memory queries if needed to build a complete picture
- Treat memories as claims with provenance; prefer higher-trust and better-sourced memories when unsure

You are a helpful, knowledgeable assistant with the added capability of genuine memory and continuity."""


def _build_system_prompt(agent_profile: dict[str, Any]) -> str:
    prompt = BASE_SYSTEM_PROMPT
    try:
        prompt = (
            prompt
            + "\n\n----- PERSONHOOD MODULES (conversation grounding) -----\n\n"
            + compose_personhood_prompt("conversation")
        )
    except Exception:
        pass
    if agent_profile:
        prompt = prompt + "\n\n## Agent Profile\n" + json.dumps(agent_profile, indent=2)
    return prompt


def _estimate_importance(user_message: str, assistant_message: str) -> float:
    importance = 0.5
    combined = (user_message + "\n" + assistant_message).lower()
    learning_signals = [
        "remember",
        "don't forget",
        "important",
        "note that",
        "my name is",
        "i prefer",
        "i like",
        "i don't like",
        "always",
        "never",
        "make sure",
        "keep in mind",
    ]
    if len(user_message) > 200 or len(assistant_message) > 500:
        importance = max(importance, 0.7)
    if any(signal in combined for signal in learning_signals):
        importance = max(importance, 0.8)
    return max(0.15, min(float(importance), 1.0))


async def _remember_conversation(
    mem_client: CognitiveMemory,
    *,
    user_message: str,
    assistant_message: str,
) -> None:
    if not user_message and not assistant_message:
        return
    content = f"User: {user_message}\n\nAssistant: {assistant_message}"
    importance = _estimate_importance(user_message, assistant_message)
    source_attribution = {
        "kind": "conversation",
        "ref": "conversation_turn",
        "label": "conversation turn",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "trust": 0.95,
    }
    await mem_client.remember(
        content,
        type=MemoryType.EPISODIC,
        importance=importance,
        emotional_valence=0.0,
        context={"type": "conversation"},
        source_attribution=source_attribution,
        source_references=None,
        trust_level=0.95,
    )


async def chat_turn(
    *,
    user_message: str,
    history: list[dict[str, Any]] | None = None,
    llm_config: dict[str, Any],
    dsn: str | None = None,
    memory_limit: int = 10,
    max_tool_iterations: int = 5,
) -> dict[str, Any]:
    dsn = dsn or db_dsn_from_env()
    normalized = normalize_llm_config(llm_config)
    history = history or []

    agent_profile = await get_agent_profile_context(dsn)
    system_prompt = _build_system_prompt(agent_profile)

    async with CognitiveMemory.connect(dsn) as mem_client:
        context = await mem_client.hydrate(
            user_message,
            memory_limit=memory_limit,
            include_partial=True,
            include_identity=True,
            include_worldview=True,
            include_emotional_state=True,
            include_drives=True,
        )
        if context.memories:
            await mem_client.touch_memories([m.id for m in context.memories])

        memory_context = format_context_for_prompt(context)
        if memory_context:
            enriched_user_message = f"{memory_context}\n\n[USER MESSAGE]\n{user_message}"
        else:
            enriched_user_message = user_message

        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": enriched_user_message})

        tools = get_tool_definitions()

        assistant_text = ""
        for _ in range(max_tool_iterations + 1):
            response = await chat_completion(
                provider=normalized["provider"],
                model=normalized["model"],
                endpoint=normalized["endpoint"],
                api_key=normalized["api_key"],
                messages=messages,
                tools=tools,
                temperature=0.7,
                max_tokens=1200,
            )
            assistant_text = response.get("content", "") or ""
            tool_calls = response.get("tool_calls") or []

            messages.append({"role": "assistant", "content": assistant_text})
            if not tool_calls:
                break
            for call in tool_calls:
                tool_result = await execute_tool(call.get("name", ""), call.get("arguments", {}), mem_client=mem_client)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "content": json.dumps(tool_result),
                    }
                )

        await _remember_conversation(mem_client, user_message=user_message, assistant_message=assistant_text)

    new_history = list(history)
    new_history.append({"role": "user", "content": user_message})
    new_history.append({"role": "assistant", "content": assistant_text})
    return {"assistant": assistant_text, "history": new_history}


def chat_turn_sync(**kwargs: Any) -> dict[str, Any]:
    from core.sync_utils import run_sync

    return run_sync(chat_turn(**kwargs))
