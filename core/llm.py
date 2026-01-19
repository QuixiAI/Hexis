from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

try:
    import openai
except Exception:  # pragma: no cover
    openai = None  # type: ignore[assignment]

try:
    import anthropic
except Exception:  # pragma: no cover
    anthropic = None  # type: ignore[assignment]


OPENAI_COMPATIBLE = {
    "openai",
    "openai_compatible",
    "openai-chat-completions-endpoint",
    "ollama",
    "grok",
    "gemini",
}


def normalize_provider(provider: str | None) -> str:
    if not provider:
        return "openai"
    raw = provider.strip().lower()
    if raw in {"openai_chat_completions_endpoint"}:
        return "openai-chat-completions-endpoint"
    return raw


def normalize_endpoint(provider: str, endpoint: str | None) -> str | None:
    if endpoint:
        return endpoint.strip() or None
    if provider == "ollama":
        return "http://localhost:11434/v1"
    return None


def resolve_api_key(api_key_env: str | None) -> str | None:
    if not api_key_env:
        return None
    value = api_key_env.strip()
    if not value:
        return None
    import os

    return os.getenv(value)


def normalize_llm_config(config: dict[str, Any] | None, *, default_model: str = "gpt-4o") -> dict[str, Any]:
    config = config or {}
    provider = normalize_provider(str(config.get("provider") or "openai"))
    model = str(config.get("model") or default_model)
    endpoint = normalize_endpoint(provider, str(config.get("endpoint") or "").strip() or None)
    api_key = config.get("api_key")
    if not api_key:
        api_key = resolve_api_key(str(config.get("api_key_env") or "").strip() or None)
    if not api_key:
        api_key = os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
    return {
        "provider": provider,
        "model": model,
        "endpoint": endpoint,
        "api_key": api_key,
    }


def _extract_system_prompt(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    rest: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "system":
            system_parts.append(str(msg.get("content") or ""))
        else:
            rest.append(msg)
    return "\n\n".join([p for p in system_parts if p.strip()]), rest


def _openai_tool_calls(raw_calls: list[Any]) -> list[dict[str, Any]]:
    tool_calls: list[dict[str, Any]] = []
    for call in raw_calls or []:
        fn = getattr(call, "function", None) or {}
        name = getattr(fn, "name", None) or fn.get("name")
        raw_args = getattr(fn, "arguments", None) or fn.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except Exception:
            args = {}
        tool_calls.append({"id": getattr(call, "id", None), "name": name, "arguments": args})
    return tool_calls


def _anthropic_tools(openai_tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not openai_tools:
        return []
    tools: list[dict[str, Any]] = []
    for tool in openai_tools:
        fn = tool.get("function", {})
        tools.append(
            {
                "name": fn.get("name"),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return tools


async def chat_completion(
    *,
    provider: str,
    model: str,
    endpoint: str | None,
    api_key: str | None,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1200,
    response_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider = normalize_provider(provider)
    endpoint = normalize_endpoint(provider, endpoint)

    if provider in OPENAI_COMPATIBLE:
        if openai is None:
            raise RuntimeError("openai package is required for OpenAI-compatible providers.")
        client = openai.AsyncOpenAI(api_key=api_key, base_url=endpoint)
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if response_format:
            payload["response_format"] = response_format
        response = await client.chat.completions.create(**payload)
        message = response.choices[0].message
        content = message.content or ""
        tool_calls = _openai_tool_calls(message.tool_calls or [])
        return {"content": content, "tool_calls": tool_calls, "raw": response}

    if provider == "anthropic":
        if anthropic is None:
            raise RuntimeError("anthropic package is required for Anthropic provider.")
        client = anthropic.AsyncAnthropic(api_key=api_key)
        system_prompt, rest = _extract_system_prompt(messages)
        anthropic_tools = _anthropic_tools(tools)
        response = await client.messages.create(
            model=model,
            system=system_prompt or None,
            messages=rest,
            tools=anthropic_tools or None,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in response.content or []:
            if block.type == "text":
                text_parts.append(block.text)
            if block.type == "tool_use":
                tool_calls.append({"id": block.id, "name": block.name, "arguments": block.input})
        return {"content": "".join(text_parts), "tool_calls": tool_calls, "raw": response}

    raise ValueError(f"Unsupported provider: {provider}")


async def stream_text_completion(
    *,
    provider: str,
    model: str,
    endpoint: str | None,
    api_key: str | None,
    messages: list[dict[str, Any]],
    temperature: float = 0.7,
    max_tokens: int = 1400,
) -> AsyncIterator[str]:
    provider = normalize_provider(provider)
    endpoint = normalize_endpoint(provider, endpoint)

    if provider in OPENAI_COMPATIBLE:
        if openai is None:
            raise RuntimeError("openai package is required for OpenAI-compatible providers.")
        client = openai.AsyncOpenAI(api_key=api_key, base_url=endpoint)
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        async for event in response:
            delta = event.choices[0].delta
            if delta and delta.content:
                yield delta.content
        return

    if provider == "anthropic":
        if anthropic is None:
            raise RuntimeError("anthropic package is required for Anthropic provider.")
        client = anthropic.AsyncAnthropic(api_key=api_key)
        system_prompt, rest = _extract_system_prompt(messages)
        response = await client.messages.create(
            model=model,
            system=system_prompt or None,
            messages=rest,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        for chunk in _chunk_text(text):
            yield chunk
        return

    raise ValueError(f"Unsupported provider: {provider}")


def _chunk_text(text: str, *, chunk_size: int = 120) -> list[str]:
    if not text:
        return []
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
