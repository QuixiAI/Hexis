"""Provider registry for the model-facing ``web_search`` tool.

The agent should see one stable search capability. Provider choice, credential
availability, keyless fallback, and result normalization belong here.
"""

from __future__ import annotations

import html
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from core.integration_reliability import (
    IntegrationHttpError,
    format_provider_error,
    request_json,
    request_text_response,
)

from .base import ToolErrorType
from .config import ToolsConfig

logger = logging.getLogger(__name__)

_BING_RSS_ENDPOINT = "https://www.bing.com/search"
_DUCKDUCKGO_LITE_ENDPOINT = "https://lite.duckduckgo.com/lite/"
_SEARCH_USER_AGENT = "Hexis/1.0 (+https://github.com/QuixiAI/Hexis)"
_CACHE_TTL_SECONDS = 600.0


class WebSearchProviderError(Exception):
    """Typed provider failure that can be surfaced as a tool error."""

    def __init__(
        self,
        message: str,
        error_type: ToolErrorType = ToolErrorType.EXECUTION_FAILED,
    ):
        super().__init__(message)
        self.error_type = error_type


@dataclass(frozen=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str = ""
    site_name: str = ""
    score: float | None = None
    published: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "site_name": self.site_name,
            "score": self.score,
        }
        if self.published:
            payload["published"] = self.published
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload


@dataclass(frozen=True)
class WebSearchResponse:
    query: str
    provider_id: str
    provider_label: str
    results: list[WebSearchResult]
    took_ms: int
    cached: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": self.query,
            "provider": {
                "id": self.provider_id,
                "label": self.provider_label,
            },
            "count": len(self.results),
            "took_ms": self.took_ms,
            "cached": self.cached,
            "external_content": {
                "untrusted": True,
                "source": "web_search",
                "provider": self.provider_id,
            },
            "results": [result.to_dict() for result in self.results],
        }
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload


@dataclass(frozen=True)
class WebSearchProviderStatus:
    id: str
    label: str
    available: bool
    requires_credential: bool
    credential_hint: str
    configured: bool = False
    selected: bool = False
    order: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "available": self.available,
            "requires_credential": self.requires_credential,
            "credential_hint": self.credential_hint,
            "configured": self.configured,
            "selected": self.selected,
            "order": self.order,
            "reason": self.reason,
        }


class WebSearchProvider(ABC):
    id: str
    label: str
    requires_credential: bool
    credential_hint: str

    @abstractmethod
    def is_available(self, config: ToolsConfig) -> bool:
        """Whether this provider can execute with current config/env."""

    @abstractmethod
    async def search(
        self,
        *,
        query: str,
        max_results: int,
        config: ToolsConfig,
    ) -> WebSearchResponse:
        """Execute a search and return normalized results."""


def _env(name: str) -> str | None:
    value = (os.getenv(name) or "").strip()
    return value or None


def _configured_provider(config: ToolsConfig) -> str:
    provider = str(config.web_search.get("provider") or "").strip().lower()
    if provider in {"", "auto", "default"}:
        return ""
    return provider


def _resolve_api_key(config: ToolsConfig, *names: str) -> str | None:
    for name in names:
        value = config.get_api_key(name)
        if value:
            return value
    return None


def _site_name(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    return BeautifulSoup(value, "html.parser").get_text(" ", strip=True)


def _strip_html(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(value))).strip()


def _decode_duckduckgo_href(href: str | None) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        href = f"https:{href}"
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        values = parse_qs(parsed.query).get("uddg")
        if values:
            return values[0]
    return href


def _format_display_output(response: WebSearchResponse) -> str:
    display_lines = [
        f"Search results for: {response.query}",
        f"Provider: {response.provider_label}",
    ]
    for i, result in enumerate(response.results[:5], 1):
        display_lines.append(f"{i}. {result.title}")
        if result.url:
            display_lines.append(f"   {result.url}")
        if result.snippet:
            display_lines.append(f"   {result.snippet[:160]}...")
    return "\n".join(display_lines)


def _tool_error_type(exc: IntegrationHttpError) -> ToolErrorType:
    if exc.error_kind == "auth_failed":
        return ToolErrorType.AUTH_FAILED
    if exc.error_kind == "rate_limited":
        return ToolErrorType.RATE_LIMITED
    if exc.error_kind == "timeout":
        return ToolErrorType.FETCH_TIMEOUT
    if exc.error_kind == "network":
        return ToolErrorType.NETWORK_ERROR
    return ToolErrorType.HTTP_ERROR


def _raise_web_search_error(provider_label: str, exc: IntegrationHttpError) -> None:
    raise WebSearchProviderError(format_provider_error(provider_label, exc), _tool_error_type(exc)) from exc


class TavilySearchProvider(WebSearchProvider):
    id = "tavily"
    label = "Tavily"
    requires_credential = True
    credential_hint = "Set TAVILY_API_KEY or configure api_keys.tavily."

    def __init__(self, api_key_resolver: Callable[[], str | None] | None = None):
        self._api_key_resolver = api_key_resolver

    def _api_key(self, config: ToolsConfig) -> str | None:
        if self._api_key_resolver:
            value = self._api_key_resolver()
            if value:
                return value
        return _resolve_api_key(config, "tavily") or _env("TAVILY_API_KEY")

    def is_available(self, config: ToolsConfig) -> bool:
        return bool(self._api_key(config))

    async def search(
        self,
        *,
        query: str,
        max_results: int,
        config: ToolsConfig,
    ) -> WebSearchResponse:
        api_key = self._api_key(config)
        if not api_key:
            raise WebSearchProviderError(self.credential_hint, ToolErrorType.MISSING_CONFIG)

        started = time.monotonic()
        try:
            data = await request_json(
                "tavily",
                "POST",
                "https://api.tavily.com/search",
                json_body={
                    "api_key": api_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                    "include_answer": False,
                },
                timeout=30.0,
                attempts=3,
                max_delay=15.0,
                retry_unsafe_methods=True,
            )
        except IntegrationHttpError as exc:
            _raise_web_search_error("Tavily", exc)

        if not isinstance(data, dict):
            raise WebSearchProviderError("Tavily returned an invalid payload.", ToolErrorType.HTTP_ERROR)
        results = [
            WebSearchResult(
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
                snippet=str(item.get("content") or "")[:500],
                score=item.get("score") if isinstance(item.get("score"), (int, float)) else None,
                site_name=_site_name(str(item.get("url") or "")),
            )
            for item in data.get("results", [])
            if isinstance(item, dict)
        ]
        return WebSearchResponse(
            query=query,
            provider_id=self.id,
            provider_label=self.label,
            results=results[:max_results],
            took_ms=int((time.monotonic() - started) * 1000),
        )


class BraveSearchProvider(WebSearchProvider):
    id = "brave"
    label = "Brave Search"
    requires_credential = True
    credential_hint = "Set BRAVE_SEARCH_API_KEY or configure api_keys.brave_search."

    def is_available(self, config: ToolsConfig) -> bool:
        return bool(_resolve_api_key(config, "brave_search", "brave") or _env("BRAVE_SEARCH_API_KEY"))

    async def search(
        self,
        *,
        query: str,
        max_results: int,
        config: ToolsConfig,
    ) -> WebSearchResponse:
        token = _resolve_api_key(config, "brave_search", "brave") or _env("BRAVE_SEARCH_API_KEY")
        if not token:
            raise WebSearchProviderError(self.credential_hint, ToolErrorType.MISSING_CONFIG)

        started = time.monotonic()
        try:
            data = await request_json(
                "brave_search",
                "GET",
                "https://api.search.brave.com/res/v1/web/search",
                headers={"X-Subscription-Token": token, "Accept": "application/json"},
                params={"q": query, "count": max_results},
                timeout=15.0,
                attempts=3,
                max_delay=15.0,
            )
        except IntegrationHttpError as exc:
            _raise_web_search_error("Brave Search", exc)
        if not isinstance(data, dict):
            raise WebSearchProviderError("Brave Search returned an invalid payload.", ToolErrorType.HTTP_ERROR)
        results = [
            WebSearchResult(
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
                snippet=str(item.get("description") or "")[:500],
                site_name=_site_name(str(item.get("url") or "")),
            )
            for item in data.get("web", {}).get("results", [])
            if isinstance(item, dict)
        ]
        return WebSearchResponse(
            query=query,
            provider_id=self.id,
            provider_label=self.label,
            results=results[:max_results],
            took_ms=int((time.monotonic() - started) * 1000),
        )


class KeenableSearchProvider(WebSearchProvider):
    """Keenable web search. Works without a key via the public endpoint.

    Opt-in: it only joins the automatic chain when a Keenable key is
    configured, or runs when selected explicitly (``provider: keenable``),
    in which case no key is needed.
    """

    id = "keenable"
    label = "Keenable"
    requires_credential = False
    credential_hint = (
        "Select provider 'keenable' (no key needed), or set KEENABLE_API_KEY / "
        "api_keys.keenable to include it in automatic selection."
    )
    _PUBLIC_ENDPOINT = "https://api.keenable.ai/v1/search/public"
    _KEYED_ENDPOINT = "https://api.keenable.ai/v1/search"

    def _api_key(self, config: ToolsConfig) -> str | None:
        return _resolve_api_key(config, "keenable") or _env("KEENABLE_API_KEY")

    def is_available(self, config: ToolsConfig) -> bool:
        return _configured_provider(config) == self.id or bool(self._api_key(config))

    async def search(
        self,
        *,
        query: str,
        max_results: int,
        config: ToolsConfig,
    ) -> WebSearchResponse:
        api_key = self._api_key(config)
        headers = {"X-Keenable-Title": "Hexis"}
        endpoint = self._PUBLIC_ENDPOINT
        if api_key:
            headers["X-API-Key"] = api_key
            endpoint = self._KEYED_ENDPOINT

        started = time.monotonic()
        try:
            data = await request_json(
                "keenable",
                "POST",
                endpoint,
                headers=headers,
                json_body={"query": query, "max_results": max_results},
                timeout=15.0,
                attempts=3,
                max_delay=15.0,
                retry_unsafe_methods=True,
            )
        except IntegrationHttpError as exc:
            _raise_web_search_error("Keenable", exc)
        if not isinstance(data, dict):
            raise WebSearchProviderError("Keenable returned an invalid payload.", ToolErrorType.HTTP_ERROR)
        results = [
            WebSearchResult(
                title=str(item.get("title") or ""),
                url=str(item["url"]),
                snippet=str(item.get("description") or item.get("snippet") or "")[:500],
                site_name=_site_name(str(item["url"])),
            )
            for item in data.get("results", [])
            if isinstance(item, dict) and item.get("url")
        ]
        return WebSearchResponse(
            query=query,
            provider_id=self.id,
            provider_label=self.label,
            results=results[:max_results],
            took_ms=int((time.monotonic() - started) * 1000),
        )


class SearxngSearchProvider(WebSearchProvider):
    id = "searxng"
    label = "SearXNG"
    requires_credential = False
    credential_hint = "Set SEARXNG_URL or configure web_search.searxng_url."

    def _base_url(self, config: ToolsConfig) -> str | None:
        value = str(config.web_search.get("searxng_url") or "").strip() or _env("SEARXNG_URL")
        return value.rstrip("/") if value else None

    def is_available(self, config: ToolsConfig) -> bool:
        return bool(self._base_url(config))

    async def search(
        self,
        *,
        query: str,
        max_results: int,
        config: ToolsConfig,
    ) -> WebSearchResponse:
        base_url = self._base_url(config)
        if not base_url:
            raise WebSearchProviderError(self.credential_hint, ToolErrorType.MISSING_CONFIG)

        started = time.monotonic()
        try:
            data = await request_json(
                "searxng",
                "GET",
                f"{base_url}/search",
                params={"q": query, "format": "json"},
                headers={"User-Agent": _SEARCH_USER_AGENT},
                timeout=20.0,
                attempts=3,
                max_delay=15.0,
                follow_redirects=True,
            )
        except IntegrationHttpError as exc:
            _raise_web_search_error("SearXNG", exc)
        if not isinstance(data, dict):
            raise WebSearchProviderError("SearXNG returned an invalid payload.", ToolErrorType.HTTP_ERROR)
        results = [
            WebSearchResult(
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
                snippet=str(item.get("content") or "")[:500],
                published=str(item.get("publishedDate") or "") or None,
                site_name=_site_name(str(item.get("url") or "")),
                metadata={"engine": item.get("engine")} if item.get("engine") else {},
            )
            for item in data.get("results", [])
            if isinstance(item, dict)
        ]
        return WebSearchResponse(
            query=query,
            provider_id=self.id,
            provider_label=self.label,
            results=results[:max_results],
            took_ms=int((time.monotonic() - started) * 1000),
        )


class DuckDuckGoLiteSearchProvider(WebSearchProvider):
    id = "duckduckgo_lite"
    label = "DuckDuckGo Lite"
    requires_credential = False
    credential_hint = "No key required."

    def is_available(self, config: ToolsConfig) -> bool:
        return True

    async def search(
        self,
        *,
        query: str,
        max_results: int,
        config: ToolsConfig,
    ) -> WebSearchResponse:
        started = time.monotonic()
        try:
            response = await request_text_response(
                "duckduckgo_lite",
                "GET",
                _DUCKDUCKGO_LITE_ENDPOINT,
                params={"q": query},
                headers={"User-Agent": _SEARCH_USER_AGENT},
                timeout=15.0,
                attempts=3,
                max_delay=15.0,
                follow_redirects=True,
            )
        except IntegrationHttpError as exc:
            _raise_web_search_error("DuckDuckGo Lite", exc)

        soup = BeautifulSoup(response.text, "html.parser")
        links = soup.select("a.result-link")
        snippets = soup.select(".result-snippet")
        if not links and "anomaly-modal" in response.text:
            raise WebSearchProviderError(
                "DuckDuckGo Lite returned a bot-detection page.",
                ToolErrorType.RATE_LIMITED,
            )

        results: list[WebSearchResult] = []
        for index, link in enumerate(links):
            url = _decode_duckduckgo_href(link.get("href"))
            snippet = ""
            if index < len(snippets):
                snippet = _clean_text(snippets[index].get_text(" ", strip=True))
            title = _clean_text(link.get_text(" ", strip=True))
            if title and url:
                results.append(
                    WebSearchResult(
                        title=title,
                        url=url,
                        snippet=snippet[:500],
                        site_name=_site_name(url),
                    )
                )
            if len(results) >= max_results:
                break

        if not results:
            raise WebSearchProviderError(
                "DuckDuckGo Lite returned no parseable results.",
                ToolErrorType.EXECUTION_FAILED,
            )

        return WebSearchResponse(
            query=query,
            provider_id=self.id,
            provider_label=self.label,
            results=results,
            took_ms=int((time.monotonic() - started) * 1000),
        )


class BingRssSearchProvider(WebSearchProvider):
    id = "bing_rss"
    label = "Bing RSS"
    requires_credential = False
    credential_hint = "No key required."

    def is_available(self, config: ToolsConfig) -> bool:
        return True

    async def search(
        self,
        *,
        query: str,
        max_results: int,
        config: ToolsConfig,
    ) -> WebSearchResponse:
        started = time.monotonic()
        try:
            response = await request_text_response(
                "bing_rss",
                "GET",
                _BING_RSS_ENDPOINT,
                params={"q": query, "format": "rss"},
                headers={"User-Agent": _SEARCH_USER_AGENT},
                timeout=15.0,
                attempts=3,
                max_delay=15.0,
                follow_redirects=True,
            )
        except IntegrationHttpError as exc:
            _raise_web_search_error("Bing RSS", exc)

        root = ET.fromstring(response.text)
        results: list[WebSearchResult] = []
        for item in root.findall("./channel/item"):
            title = _clean_text(item.findtext("title"))
            url = (item.findtext("link") or "").strip()
            snippet = _strip_html(item.findtext("description"))[:500]
            if title and url:
                results.append(
                    WebSearchResult(
                        title=title,
                        url=url,
                        snippet=snippet,
                        site_name=_site_name(url),
                    )
                )
            if len(results) >= max_results:
                break

        return WebSearchResponse(
            query=query,
            provider_id=self.id,
            provider_label=self.label,
            results=results,
            took_ms=int((time.monotonic() - started) * 1000),
        )


class WebSearchProviderRegistry:
    def __init__(self, providers: list[WebSearchProvider]):
        self._providers = {provider.id: provider for provider in providers}
        self._order = [provider.id for provider in providers]
        self._cache: dict[str, tuple[float, WebSearchResponse]] = {}

    @property
    def providers(self) -> list[WebSearchProvider]:
        return [self._providers[id_] for id_ in self._order]

    def provider(self, provider_id: str) -> WebSearchProvider | None:
        return self._providers.get(provider_id)

    def candidates(self, config: ToolsConfig) -> tuple[list[WebSearchProvider], bool]:
        configured = _configured_provider(config)
        if configured:
            provider = self.provider(configured)
            return ([provider] if provider else []), True
        return (
            [
                provider
                for provider in self.providers
                if provider.is_available(config)
            ],
            False,
        )

    def statuses(self, config: ToolsConfig) -> list[WebSearchProviderStatus]:
        candidates, explicit = self.candidates(config)
        selected = candidates[0].id if candidates else ""
        configured = _configured_provider(config)
        statuses: list[WebSearchProviderStatus] = []
        for index, provider in enumerate(self.providers):
            available = provider.is_available(config)
            is_configured = configured == provider.id
            reason = ""
            if not available:
                reason = provider.credential_hint
            statuses.append(
                WebSearchProviderStatus(
                    id=provider.id,
                    label=provider.label,
                    available=available,
                    requires_credential=provider.requires_credential,
                    credential_hint=provider.credential_hint,
                    configured=is_configured,
                    selected=provider.id == selected,
                    order=index,
                    reason=reason,
                )
            )
        if explicit and configured and configured not in self._providers:
            statuses.append(
                WebSearchProviderStatus(
                    id=configured,
                    label=configured,
                    available=False,
                    requires_credential=False,
                    credential_hint="Unknown web search provider.",
                    configured=True,
                    selected=False,
                    order=len(statuses),
                    reason="Unknown web search provider.",
                )
            )
        return statuses

    def _cache_key(self, provider: WebSearchProvider, query: str, max_results: int) -> str:
        return f"{provider.id}\0{max_results}\0{query.strip().lower()}"

    def _cached(self, provider: WebSearchProvider, query: str, max_results: int) -> WebSearchResponse | None:
        key = self._cache_key(provider, query, max_results)
        item = self._cache.get(key)
        if not item:
            return None
        expires_at, response = item
        if expires_at < time.monotonic():
            self._cache.pop(key, None)
            return None
        return WebSearchResponse(
            query=response.query,
            provider_id=response.provider_id,
            provider_label=response.provider_label,
            results=response.results,
            took_ms=response.took_ms,
            cached=True,
            metadata=response.metadata,
        )

    def _store_cache(self, provider: WebSearchProvider, query: str, max_results: int, response: WebSearchResponse) -> None:
        self._cache[self._cache_key(provider, query, max_results)] = (
            time.monotonic() + _CACHE_TTL_SECONDS,
            response,
        )

    async def search(
        self,
        *,
        query: str,
        max_results: int,
        config: ToolsConfig,
    ) -> tuple[WebSearchResponse, list[str]]:
        candidates, explicit = self.candidates(config)
        if not candidates:
            configured = _configured_provider(config)
            if configured:
                raise WebSearchProviderError(
                    f"Unknown web search provider '{configured}'.",
                    ToolErrorType.MISSING_CONFIG,
                )
            raise WebSearchProviderError(
                "No web search provider is available.",
                ToolErrorType.MISSING_CONFIG,
            )

        errors: list[str] = []
        for provider in candidates:
            cached = self._cached(provider, query, max_results)
            if cached:
                return cached, errors
            try:
                response = await provider.search(
                    query=query,
                    max_results=max_results,
                    config=config,
                )
                self._store_cache(provider, query, max_results, response)
                return response, errors
            except WebSearchProviderError as exc:
                errors.append(f"{provider.label}: {exc}")
                if explicit:
                    raise
            except Exception as exc:
                logger.exception("Web search provider %s failed", provider.id)
                errors.append(f"{provider.label}: {exc}")
                if explicit:
                    raise WebSearchProviderError(str(exc), ToolErrorType.EXECUTION_FAILED) from exc

        raise WebSearchProviderError(
            "All web search providers failed. " + " ".join(errors),
            ToolErrorType.EXECUTION_FAILED,
        )


def create_default_web_search_registry(
    tavily_api_key_resolver: Callable[[], str | None] | None = None,
) -> WebSearchProviderRegistry:
    return WebSearchProviderRegistry([
        TavilySearchProvider(tavily_api_key_resolver),
        BraveSearchProvider(),
        KeenableSearchProvider(),
        SearxngSearchProvider(),
        DuckDuckGoLiteSearchProvider(),
        BingRssSearchProvider(),
    ])


def display_output_for_search(response: WebSearchResponse) -> str:
    return _format_display_output(response)
