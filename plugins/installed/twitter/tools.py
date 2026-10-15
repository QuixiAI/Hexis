"""Hexis Tools System - Twitter/X Research (E.6).

Implements a multi-tier fallback strategy:
1) FxTwitter (free, unauthenticated)
2) TwitterAPI.io (if key configured)
3) Xquik (if key configured)
4) TwittrAPI (if key configured)
5) Official X API v2 (if bearer token configured)
6) xAI Search endpoint (best-effort fallback, if key configured)
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from core.integration_reliability import request_json
from core.tools.base import (
    ToolCategory,
    ToolContext,
    ToolErrorType,
    ToolExecutionContext,
    ToolHandler,
    ToolResult,
    ToolSpec,
)
from core.tools.api_keys import resolve_api_key

logger = logging.getLogger(__name__)

_FXTWITTER_BASE = "https://api.fxtwitter.com"
_TWITTERAPI_IO_BASE = "https://api.twitterapi.io"
_XQUIK_BASE = "https://xquik.com/api/v1"
_XQUIK_API_CONTRACT = "2026-04-29"
_TWITTRAPI_BASE = "https://api.twittrapi.com"
_X_API_BASE = "https://api.twitter.com/2"
_XAI_SEARCH_BASE = "https://api.x.ai/v1"


def _tweet_dict(
    *,
    tweet_id: str | None,
    text: str,
    author: str,
    created_at: str | None,
    likes: int = 0,
    retweets: int = 0,
    replies: int = 0,
) -> dict[str, Any]:
    return {
        "id": tweet_id,
        "text": text,
        "author": author,
        "created_at": created_at,
        "metrics": {
            "likes": likes,
            "retweets": retweets,
            "replies": replies,
        },
    }


class SearchTwitterHandler(ToolHandler):
    """Search tweets with multi-tier provider fallback."""

    def __init__(self, api_key_resolver: Callable[[], str | None] | None = None):
        # Kept for interface consistency; FxTwitter is free/unauthenticated
        self._api_key_resolver = api_key_resolver

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="twitter_search",
            description=(
                "Search Twitter/X for recent tweets matching a query. "
                "Uses automatic fallback across FxTwitter, TwitterAPI.io, Xquik, "
                "TwittrAPI, official X API, and xAI Search when keys are available."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of tweets to return (default 10)",
                    },
                },
                "required": ["query"],
            },
            category=ToolCategory.EXTERNAL,
            energy_cost=2,
            is_read_only=True,
            optional=True,
        )

    async def execute(
        self, arguments: dict[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        query = arguments["query"]
        max_results = max(1, min(arguments.get("max_results", 10), 100))
        attempts: list[str] = []

        # Tier 1: FxTwitter (free)
        try:
            tweets = await self._search_fxtwitter(query, max_results)
            if tweets:
                return ToolResult.success_result(
                    {
                        "tweets": tweets,
                        "count": len(tweets),
                        "provider": "fxtwitter",
                        "tier": 1,
                    },
                    display_output=f"Found {len(tweets)} tweet(s) for '{query}' (FxTwitter)",
                )
            attempts.append("FxTwitter: no results")
        except Exception as e:
            logger.warning("FxTwitter search failed: %s", e)
            attempts.append(f"FxTwitter: {e}")

        # Tier 2: TwitterAPI.io
        twitterapi_key = await resolve_api_key(
            context,
            config_key="twitterapi_io",
            env_names=("TWITTERAPI_IO_API_KEY",),
        )
        if twitterapi_key:
            try:
                tweets = await self._search_twitterapi_io(
                    query,
                    max_results,
                    twitterapi_key,
                )
                if tweets:
                    return ToolResult.success_result(
                        {
                            "tweets": tweets,
                            "count": len(tweets),
                            "provider": "twitterapi_io",
                            "tier": 2,
                        },
                        display_output=f"Found {len(tweets)} tweet(s) for '{query}' (TwitterAPI.io)",
                    )
                attempts.append("TwitterAPI.io: no results")
            except Exception as e:
                attempts.append(f"TwitterAPI.io: {e}")
        else:
            attempts.append("TwitterAPI.io: skipped (no API key)")

        # Tier 3: Xquik
        xquik_key = await resolve_api_key(
            context,
            config_key="xquik",
            env_names=("XQUIK_API_KEY",),
        )
        if xquik_key:
            try:
                tweets = await self._search_xquik(query, max_results, xquik_key)
                if tweets:
                    return ToolResult.success_result(
                        {
                            "tweets": tweets,
                            "count": len(tweets),
                            "provider": "xquik",
                            "tier": 3,
                        },
                        display_output=f"Found {len(tweets)} tweet(s) for '{query}' (Xquik)",
                    )
                attempts.append("Xquik: no results")
            except Exception as e:
                attempts.append(f"Xquik: {e}")
        else:
            attempts.append("Xquik: skipped (no API key)")

        # Tier 4: TwittrAPI
        twittrapi_key = await resolve_api_key(
            context,
            config_key="twittrapi",
            env_names=("TWITTRAPI_API_KEY",),
        )
        if twittrapi_key:
            try:
                tweets = await self._search_twittrapi(query, max_results, twittrapi_key)
                if tweets:
                    return ToolResult.success_result(
                        {
                            "tweets": tweets,
                            "count": len(tweets),
                            "provider": "twittrapi",
                            "tier": 4,
                        },
                        display_output=f"Found {len(tweets)} tweet(s) for '{query}' (TwittrAPI)",
                    )
                attempts.append("TwittrAPI: no results")
            except Exception as e:
                attempts.append(f"TwittrAPI: {e}")
        else:
            attempts.append("TwittrAPI: skipped (no API key)")

        # Tier 5: Official X API v2
        x_bearer = await resolve_api_key(
            context,
            config_key="x_api_bearer",
            env_names=("X_API_BEARER_TOKEN", "TWITTER_BEARER_TOKEN"),
        )
        if x_bearer:
            try:
                tweets = await self._search_x_api(query, max_results, x_bearer)
                if tweets:
                    return ToolResult.success_result(
                        {
                            "tweets": tweets,
                            "count": len(tweets),
                            "provider": "x_api_v2",
                            "tier": 5,
                        },
                        display_output=f"Found {len(tweets)} tweet(s) for '{query}' (X API v2)",
                    )
                attempts.append("X API v2: no results")
            except Exception as e:
                attempts.append(f"X API v2: {e}")
        else:
            attempts.append("X API v2: skipped (no bearer token)")

        # Tier 6: xAI Search fallback (best-effort)
        xai_key = await resolve_api_key(
            context,
            explicit_resolver=self._api_key_resolver,
            config_key="xai",
            env_names=("XAI_API_KEY",),
        )
        if xai_key:
            try:
                tweets = await self._search_xai(query, max_results, xai_key)
                if tweets:
                    return ToolResult.success_result(
                        {
                            "tweets": tweets,
                            "count": len(tweets),
                            "provider": "xai_search",
                            "tier": 6,
                        },
                        display_output=f"Found {len(tweets)} tweet(s) for '{query}' (xAI search)",
                    )
                attempts.append("xAI search: no results")
            except Exception as e:
                attempts.append(f"xAI search: {e}")
        else:
            attempts.append("xAI search: skipped (no API key)")

        return ToolResult.error_result(
            "Twitter search unavailable across all tiers. "
            f"Try manual search at https://x.com/search?q={query}. "
            f"Attempts: {' | '.join(attempts)}",
        )

    async def _search_fxtwitter(
        self, query: str, max_results: int
    ) -> list[dict[str, Any]]:
        data = await request_json(
            "fxtwitter",
            "GET",
            f"{_FXTWITTER_BASE}/search",
            params={"query": query},
            timeout=15.0,
            attempts=3,
            max_delay=10.0,
        )
        tweets_raw = data.get("tweets", data.get("results", [])) if isinstance(data, dict) else []
        tweets: list[dict[str, Any]] = []
        for t in tweets_raw[:max_results]:
            author = t.get("author", {}) if isinstance(t.get("author"), dict) else {}
            tweets.append(
                _tweet_dict(
                    tweet_id=t.get("id"),
                    text=t.get("text", ""),
                    author=author.get("screen_name") or author.get("name", "unknown"),
                    created_at=t.get("created_at"),
                    likes=int(t.get("likes", 0) or 0),
                    retweets=int(t.get("retweets", 0) or 0),
                    replies=int(t.get("replies", 0) or 0),
                )
            )
        return tweets

    async def _search_twitterapi_io(
        self,
        query: str,
        max_results: int,
        api_key: str,
    ) -> list[dict[str, Any]]:
        # Endpoint format is kept flexible to tolerate provider-side schema changes.
        data = await request_json(
            "twitterapi_io",
            "GET",
            f"{_TWITTERAPI_IO_BASE}/twitter/tweet/advanced_search",
            headers={"X-API-Key": api_key, "Accept": "application/json"},
            params={"query": query, "limit": max_results},
            timeout=20.0,
            attempts=3,
            max_delay=10.0,
        )

        candidates = (
            data.get("tweets") or data.get("results") or data.get("data") or []
            if isinstance(data, dict) else []
        )
        tweets: list[dict[str, Any]] = []
        for t in candidates[:max_results]:
            user = t.get("user") or t.get("author") or {}
            metrics = t.get("metrics") or t.get("public_metrics") or {}
            tweets.append(
                _tweet_dict(
                    tweet_id=t.get("id") or t.get("tweet_id"),
                    text=t.get("text", ""),
                    author=user.get("username")
                    or user.get("screen_name")
                    or user.get("name", "unknown"),
                    created_at=t.get("created_at"),
                    likes=int(metrics.get("like_count") or t.get("likes") or 0),
                    retweets=int(
                        metrics.get("retweet_count") or t.get("retweets") or 0
                    ),
                    replies=int(metrics.get("reply_count") or t.get("replies") or 0),
                )
            )
        return tweets

    async def _search_xquik(
        self,
        query: str,
        max_results: int,
        api_key: str,
    ) -> list[dict[str, Any]]:
        data = await request_json(
            "xquik",
            "GET",
            f"{_XQUIK_BASE}/x/tweets/search",
            headers={
                "x-api-key": api_key,
                "xquik-api-contract": _XQUIK_API_CONTRACT,
                "Accept": "application/json",
            },
            params={"q": query, "limit": min(max_results, 100), "queryType": "Latest"},
            timeout=20.0,
            attempts=3,
            max_delay=10.0,
        )

        candidates = (
            data.get("tweets") or data.get("results") or data.get("data") or []
            if isinstance(data, dict) else []
        )
        tweets: list[dict[str, Any]] = []
        for t in candidates[:max_results]:
            user = t.get("author") or t.get("user") or {}
            metrics = t.get("metrics") or t.get("public_metrics") or {}
            tweets.append(
                _tweet_dict(
                    tweet_id=t.get("id") or t.get("tweet_id"),
                    text=t.get("text", ""),
                    author=(
                        user.get("username")
                        or user.get("userName")
                        or user.get("screen_name")
                        or user.get("name", "unknown")
                    ),
                    created_at=t.get("createdAt") or t.get("created_at"),
                    likes=int(
                        metrics.get("like_count")
                        or t.get("like_count")
                        or t.get("likeCount")
                        or t.get("likes")
                        or 0
                    ),
                    retweets=int(
                        metrics.get("retweet_count")
                        or t.get("retweet_count")
                        or t.get("retweetCount")
                        or t.get("retweets")
                        or 0,
                    ),
                    replies=int(
                        metrics.get("reply_count")
                        or t.get("reply_count")
                        or t.get("replyCount")
                        or t.get("replies")
                        or 0
                    ),
                )
            )
        return tweets

    async def _search_twittrapi(
        self,
        query: str,
        max_results: int,
        api_key: str,
    ) -> list[dict[str, Any]]:
        # format=simple returns {data: [Tweet], nextCursor, hasNextPage} with
        # flat camelCase tweets and counts under "counts".
        data = await request_json(
            "twittrapi",
            "GET",
            f"{_TWITTRAPI_BASE}/search",
            headers={"X-API-KEY": api_key, "Accept": "application/json"},
            params={
                "searchItem": query,
                "category": "Latest",
                "count": min(max_results, 100),
                "format": "simple",
            },
            timeout=20.0,
            attempts=3,
            max_delay=10.0,
        )

        rows = data.get("data") if isinstance(data, dict) else []
        tweets: list[dict[str, Any]] = []
        for t in (rows or [])[:max_results]:
            user = t.get("author") or {}
            counts = t.get("counts") or {}
            tweets.append(
                _tweet_dict(
                    tweet_id=t.get("id"),
                    text=t.get("text", ""),
                    author=user.get("username") or user.get("name") or "unknown",
                    created_at=t.get("createdAt"),
                    likes=int(counts.get("likes") or 0),
                    retweets=int(counts.get("retweets") or 0),
                    replies=int(counts.get("replies") or 0),
                )
            )
        return tweets

    async def _search_x_api(
        self,
        query: str,
        max_results: int,
        bearer_token: str,
    ) -> list[dict[str, Any]]:
        capped = max(10, min(max_results, 100))
        data = await request_json(
            "x_api",
            "GET",
            f"{_X_API_BASE}/tweets/search/recent",
            headers={"Authorization": f"Bearer {bearer_token}"},
            params={
                "query": query,
                "max_results": capped,
                "tweet.fields": "created_at,public_metrics,author_id",
                "expansions": "author_id",
                "user.fields": "username,name",
            },
            timeout=20.0,
            attempts=3,
            max_delay=10.0,
        )

        user_map: dict[str, dict[str, Any]] = {}
        includes = data.get("includes", {}) if isinstance(data, dict) else {}
        for u in includes.get("users", []):
            if u.get("id"):
                user_map[str(u["id"])] = u

        tweets: list[dict[str, Any]] = []
        rows = data.get("data") if isinstance(data, dict) else []
        for t in (rows or [])[:max_results]:
            metrics = t.get("public_metrics") or {}
            user = user_map.get(str(t.get("author_id")), {})
            tweets.append(
                _tweet_dict(
                    tweet_id=t.get("id"),
                    text=t.get("text", ""),
                    author=user.get("username") or user.get("name", "unknown"),
                    created_at=t.get("created_at"),
                    likes=int(metrics.get("like_count") or 0),
                    retweets=int(metrics.get("retweet_count") or 0),
                    replies=int(metrics.get("reply_count") or 0),
                )
            )
        return tweets

    async def _search_xai(
        self,
        query: str,
        max_results: int,
        api_key: str,
    ) -> list[dict[str, Any]]:
        data = await request_json(
            "xai_search",
            "GET",
            f"{_XAI_SEARCH_BASE}/search",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            params={"q": query, "limit": max_results},
            timeout=20.0,
            attempts=3,
            max_delay=10.0,
        )
        results = data.get("results") or data.get("tweets") or [] if isinstance(data, dict) else []
        tweets: list[dict[str, Any]] = []
        for r in results[:max_results]:
            tweets.append(
                _tweet_dict(
                    tweet_id=r.get("id"),
                    text=r.get("text", ""),
                    author=r.get("author") or "unknown",
                    created_at=r.get("created_at"),
                    likes=int(r.get("likes") or 0),
                    retweets=int(r.get("retweets") or 0),
                    replies=int(r.get("replies") or 0),
                )
            )
        return tweets


def create_twitter_tools(
    api_key_resolver: Callable[[], str | None] | None = None,
) -> list[ToolHandler]:
    """Create Twitter/X research tools."""
    return [
        SearchTwitterHandler(api_key_resolver),
    ]
