"""
Hexis Tools System - Web Tools

Tools for web operations (search, fetch content).
"""

from __future__ import annotations

import logging
from typing import Any, Callable

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


class WebSearchHandler(ToolHandler):
    """
    Web search using Tavily API.

    Provides up-to-date information from the web for questions about
    recent events, facts, or topics the agent is uncertain about.
    """

    def __init__(self, api_key_resolver: Callable[[], str | None] | None = None):
        """
        Args:
            api_key_resolver: Callable that returns the API key, or None to use config.
        """
        self._api_key_resolver = api_key_resolver

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="web_search",
            description=(
                "Search the web for current information. Use for questions about "
                "recent events, facts you're uncertain about, or topics that may have "
                "changed since your knowledge cutoff. Returns relevant search results "
                "with titles, URLs, and snippets."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query - be specific and include relevant keywords.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return (default: 5, max: 10).",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 10,
                    },
                    "search_depth": {
                        "type": "string",
                        "enum": ["basic", "advanced"],
                        "description": "Search depth - 'advanced' provides more detailed results.",
                        "default": "basic",
                    },
                    "include_answer": {
                        "type": "boolean",
                        "description": "Include AI-generated answer summary.",
                        "default": False,
                    },
                },
                "required": ["query"],
            },
            category=ToolCategory.WEB,
            energy_cost=2,
            is_read_only=True,
        )

    def validate(self, arguments: dict[str, Any]) -> list[str]:
        errors = []
        query = arguments.get("query", "")
        if not query or not query.strip():
            errors.append("query cannot be empty")
        if len(query) > 1000:
            errors.append("query too long (max 1000 characters)")
        return errors

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        # Check network access
        if not context.allow_network:
            return ToolResult.error_result(
                "Network access not allowed in this context",
                ToolErrorType.PERMISSION_DENIED,
            )

        # Get API key
        api_key = None
        if self._api_key_resolver:
            api_key = self._api_key_resolver()
        else:
            # Try to get from config
            try:
                config = await context.registry.get_config()
                api_key = config.get_api_key("tavily")
            except Exception:
                pass

        if not api_key:
            return ToolResult.error_result(
                "Web search API key not configured. Set TAVILY_API_KEY environment variable "
                "or configure via 'hexis tools set-api-key tavily env:TAVILY_API_KEY'",
                ToolErrorType.MISSING_CONFIG,
            )

        query = arguments["query"]
        max_results = min(arguments.get("max_results", 5), 10)
        search_depth = arguments.get("search_depth", "basic")
        include_answer = arguments.get("include_answer", False)

        try:
            import aiohttp
        except ImportError:
            return ToolResult.error_result(
                "aiohttp not installed - required for web search",
                ToolErrorType.MISSING_DEPENDENCY,
            )

        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "api_key": api_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": search_depth,
                    "include_answer": include_answer,
                }

                async with session.post(
                    "https://api.tavily.com/search",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 401:
                        return ToolResult.error_result(
                            "Invalid Tavily API key",
                            ToolErrorType.AUTH_FAILED,
                        )
                    if resp.status == 429:
                        return ToolResult.error_result(
                            "Rate limit exceeded - try again later",
                            ToolErrorType.RATE_LIMITED,
                        )
                    if resp.status != 200:
                        text = await resp.text()
                        return ToolResult.error_result(
                            f"Search failed with status {resp.status}: {text[:200]}",
                            ToolErrorType.EXECUTION_FAILED,
                        )

                    data = await resp.json()

            # Parse results
            results = []
            for r in data.get("results", []):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", "")[:500],
                    "score": r.get("score"),
                })

            output = {
                "query": query,
                "results": results,
                "count": len(results),
            }

            if include_answer and data.get("answer"):
                output["answer"] = data["answer"]

            # Format display output
            display_lines = [f"Search results for: {query}"]
            for i, r in enumerate(results[:5], 1):
                display_lines.append(f"{i}. {r['title']}")
                display_lines.append(f"   {r['snippet'][:100]}...")

            return ToolResult.success_result(
                output=output,
                display_output="\n".join(display_lines),
            )

        except aiohttp.ClientTimeout:
            return ToolResult.error_result(
                "Search request timed out",
                ToolErrorType.TIMEOUT,
            )
        except Exception as e:
            logger.exception("Web search failed")
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class WebFetchHandler(ToolHandler):
    """
    Fetch and extract readable content from a URL.

    Uses trafilatura for intelligent content extraction that removes
    navigation, ads, and other non-content elements.
    """

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="web_fetch",
            description=(
                "Fetch content from a URL and extract readable text. Use for reading "
                "articles, documentation, blog posts, or web pages. Automatically "
                "removes navigation, ads, and other non-content elements."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to fetch - must be a valid HTTP or HTTPS URL.",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Maximum characters to return (default: 10000, max: 50000).",
                        "default": 10000,
                        "minimum": 1000,
                        "maximum": 50000,
                    },
                    "include_tables": {
                        "type": "boolean",
                        "description": "Include table content in extraction.",
                        "default": True,
                    },
                    "include_links": {
                        "type": "boolean",
                        "description": "Include hyperlinks in output.",
                        "default": False,
                    },
                },
                "required": ["url"],
            },
            category=ToolCategory.WEB,
            energy_cost=2,
            is_read_only=True,
        )

    def validate(self, arguments: dict[str, Any]) -> list[str]:
        errors = []
        url = arguments.get("url", "")

        if not url:
            errors.append("url is required")
        elif not (url.startswith("http://") or url.startswith("https://")):
            errors.append("url must start with http:// or https://")

        # Basic URL validation
        if url:
            # Block local/internal URLs
            import urllib.parse
            try:
                parsed = urllib.parse.urlparse(url)
                host = parsed.hostname or ""
                if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
                    errors.append("Cannot fetch localhost URLs")
                if host.startswith("192.168.") or host.startswith("10.") or host.startswith("172."):
                    errors.append("Cannot fetch internal network URLs")
            except Exception:
                errors.append("Invalid URL format")

        return errors

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        # Check network access
        if not context.allow_network:
            return ToolResult.error_result(
                "Network access not allowed in this context",
                ToolErrorType.PERMISSION_DENIED,
            )

        try:
            import trafilatura
        except ImportError:
            return ToolResult.error_result(
                "trafilatura not installed. Install with: pip install trafilatura",
                ToolErrorType.MISSING_DEPENDENCY,
            )

        url = arguments["url"]
        max_chars = min(arguments.get("max_chars", 10000), 50000)
        include_tables = arguments.get("include_tables", True)
        include_links = arguments.get("include_links", False)

        try:
            # Fetch the URL
            downloaded = trafilatura.fetch_url(url)

            if not downloaded:
                return ToolResult.error_result(
                    f"Failed to fetch URL: {url}",
                    ToolErrorType.EXECUTION_FAILED,
                )

            # Extract content
            content = trafilatura.extract(
                downloaded,
                include_tables=include_tables,
                include_links=include_links,
                output_format="txt",
            )

            if not content:
                return ToolResult.error_result(
                    f"Failed to extract content from URL - page may be empty or use JavaScript rendering",
                    ToolErrorType.EXECUTION_FAILED,
                )

            # Get metadata
            metadata = trafilatura.extract_metadata(downloaded)
            title = metadata.title if metadata else None
            author = metadata.author if metadata else None
            date = str(metadata.date) if metadata and metadata.date else None

            # Truncate if needed
            truncated = False
            if len(content) > max_chars:
                content = content[:max_chars]
                truncated = True

            output = {
                "url": url,
                "title": title,
                "author": author,
                "date": date,
                "content": content,
                "char_count": len(content),
                "truncated": truncated,
            }

            # Format display output
            display_parts = []
            if title:
                display_parts.append(f"Title: {title}")
            display_parts.append(f"URL: {url}")
            display_parts.append(f"Extracted {len(content)} characters")
            if truncated:
                display_parts.append("(truncated)")

            return ToolResult.success_result(
                output=output,
                display_output="\n".join(display_parts),
            )

        except Exception as e:
            logger.exception(f"Web fetch failed for {url}")
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class WebSummarizeHandler(ToolHandler):
    """
    Fetch a URL and summarize its content using LLM.

    Combines web fetch with LLM summarization for efficient
    information extraction.
    """

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="web_summarize",
            description=(
                "Fetch a URL and get an AI-generated summary of its content. "
                "Useful when you need the key points from a page without reading "
                "the full content."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to fetch and summarize.",
                    },
                    "focus": {
                        "type": "string",
                        "description": "Optional focus area - what aspect to focus the summary on.",
                    },
                    "max_length": {
                        "type": "string",
                        "enum": ["brief", "standard", "detailed"],
                        "description": "Desired summary length.",
                        "default": "standard",
                    },
                },
                "required": ["url"],
            },
            category=ToolCategory.WEB,
            energy_cost=4,  # Higher cost due to LLM call
            is_read_only=True,
        )

    def validate(self, arguments: dict[str, Any]) -> list[str]:
        errors = []
        url = arguments.get("url", "")

        if not url:
            errors.append("url is required")
        elif not (url.startswith("http://") or url.startswith("https://")):
            errors.append("url must start with http:// or https://")

        return errors

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        if not context.allow_network:
            return ToolResult.error_result(
                "Network access not allowed in this context",
                ToolErrorType.PERMISSION_DENIED,
            )

        try:
            import trafilatura
        except ImportError:
            return ToolResult.error_result(
                "trafilatura not installed",
                ToolErrorType.MISSING_DEPENDENCY,
            )

        url = arguments["url"]
        focus = arguments.get("focus")
        max_length = arguments.get("max_length", "standard")

        # First fetch the content
        try:
            downloaded = trafilatura.fetch_url(url)
            if not downloaded:
                return ToolResult.error_result(
                    f"Failed to fetch URL: {url}",
                    ToolErrorType.EXECUTION_FAILED,
                )

            content = trafilatura.extract(downloaded, include_tables=True)
            if not content:
                return ToolResult.error_result(
                    "Failed to extract content from URL",
                    ToolErrorType.EXECUTION_FAILED,
                )

            metadata = trafilatura.extract_metadata(downloaded)
            title = metadata.title if metadata else None

        except Exception as e:
            return ToolResult.error_result(
                f"Failed to fetch URL: {e}",
                ToolErrorType.EXECUTION_FAILED,
            )

        # Truncate content for summarization
        max_content = 15000
        if len(content) > max_content:
            content = content[:max_content] + "\n\n[Content truncated for summarization]"

        # Build summarization prompt
        length_guide = {
            "brief": "2-3 sentences",
            "standard": "1-2 paragraphs",
            "detailed": "3-5 paragraphs with key points",
        }

        prompt = f"""Summarize the following web page content in {length_guide.get(max_length, '1-2 paragraphs')}.

Title: {title or 'Unknown'}
URL: {url}
"""
        if focus:
            prompt += f"\nFocus on: {focus}\n"

        prompt += f"\nContent:\n{content}"

        # Use external_calls to queue LLM request
        try:
            import json

            async with context.registry.pool.acquire() as conn:
                # Queue the summarization request
                call_id = await conn.fetchval(
                    """
                    INSERT INTO external_calls (call_type, input, status)
                    VALUES ('llm_completion', $1::jsonb, 'pending')
                    RETURNING id
                    """,
                    json.dumps({
                        "prompt": prompt,
                        "max_tokens": 500,
                        "purpose": "web_summarize",
                    }),
                )

                # Wait for completion (with timeout)
                import asyncio
                for _ in range(30):  # 30 second timeout
                    result = await conn.fetchrow(
                        "SELECT status, output, error FROM external_calls WHERE id = $1",
                        call_id,
                    )
                    if result["status"] == "completed":
                        output_data = json.loads(result["output"]) if result["output"] else {}
                        summary = output_data.get("text", "")
                        return ToolResult.success_result(
                            output={
                                "url": url,
                                "title": title,
                                "summary": summary,
                                "focus": focus,
                            },
                            display_output=f"Summary of {title or url}:\n{summary}",
                        )
                    elif result["status"] == "failed":
                        return ToolResult.error_result(
                            result["error"] or "Summarization failed",
                            ToolErrorType.EXECUTION_FAILED,
                        )
                    await asyncio.sleep(1)

                return ToolResult.error_result(
                    "Summarization timed out",
                    ToolErrorType.TIMEOUT,
                )

        except Exception as e:
            logger.exception("Web summarize failed")
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


def create_web_tools(
    api_key_resolver: Callable[[], str | None] | None = None,
) -> list[ToolHandler]:
    """
    Create all web tool handlers.

    Args:
        api_key_resolver: Optional callable to resolve Tavily API key.
            If not provided, tools will look up the key from config.

    Returns:
        List of web tool handlers.
    """
    return [
        WebSearchHandler(api_key_resolver),
        WebFetchHandler(),
        WebSummarizeHandler(),
    ]
