"""Tests for the opt-in Keenable web_search provider."""

from __future__ import annotations

import pytest

from core.tools import web_search_providers as wsp
from core.tools.config import ToolsConfig


def _capture(monkeypatch, payload):
    captured: dict = {}

    async def _request_json(provider, method, url, **kwargs):
        captured.update(provider=provider, method=method, url=url, **kwargs)
        return payload

    monkeypatch.setattr(wsp, "request_json", _request_json)
    return captured


def test_not_in_auto_chain_without_key_or_selection(monkeypatch):
    monkeypatch.delenv("KEENABLE_API_KEY", raising=False)
    provider = wsp.KeenableSearchProvider()
    assert provider.is_available(ToolsConfig()) is False
    assert (
        provider.is_available(ToolsConfig(web_search={"provider": "keenable"})) is True
    )
    assert provider.is_available(ToolsConfig(api_keys={"keenable": "kn"})) is True


def test_registered_in_default_registry():
    registry = wsp.create_default_web_search_registry()
    assert registry.provider("keenable") is not None


@pytest.mark.asyncio
async def test_keyless_public_endpoint(monkeypatch):
    monkeypatch.delenv("KEENABLE_API_KEY", raising=False)
    captured = _capture(
        monkeypatch,
        {
            "results": [
                {
                    "title": "A",
                    "url": "https://a.example/x",
                    "description": "desc",
                    "snippet": "s",
                },
                {"title": "No URL", "description": "dropped"},
                {"title": "B", "url": "https://b.example/y", "snippet": "page text"},
            ]
        },
    )
    response = await wsp.KeenableSearchProvider().search(
        query="hexis",
        max_results=5,
        config=ToolsConfig(web_search={"provider": "keenable"}),
    )
    assert captured["url"] == "https://api.keenable.ai/v1/search/public"
    assert captured["method"] == "POST"
    assert captured["json_body"] == {"query": "hexis", "max_results": 5}
    assert captured["headers"] == {"X-Keenable-Title": "Hexis"}
    assert [r.url for r in response.results] == [
        "https://a.example/x",
        "https://b.example/y",
    ]
    assert [r.snippet for r in response.results] == ["desc", "page text"]
    assert response.provider_id == "keenable"


@pytest.mark.asyncio
async def test_keyed_endpoint(monkeypatch):
    captured = _capture(monkeypatch, {"results": []})
    await wsp.KeenableSearchProvider().search(
        query="q", max_results=3, config=ToolsConfig(api_keys={"keenable": "kn"})
    )
    assert captured["url"] == "https://api.keenable.ai/v1/search"
    assert captured["headers"]["X-API-Key"] == "kn"


@pytest.mark.asyncio
async def test_registry_cache_separates_keyless_and_keyed(monkeypatch):
    monkeypatch.delenv("KEENABLE_API_KEY", raising=False)
    urls: list[str] = []

    async def _request_json(provider, method, url, **kwargs):
        urls.append(url)
        return {
            "results": [{"title": "A", "url": "https://a.example/x", "snippet": "s"}]
        }

    monkeypatch.setattr(wsp, "request_json", _request_json)
    registry = wsp.create_default_web_search_registry()
    await registry.search(
        query="q",
        max_results=3,
        config=ToolsConfig(web_search={"provider": "keenable"}),
    )
    await registry.search(
        query="q",
        max_results=3,
        config=ToolsConfig(
            web_search={"provider": "keenable"}, api_keys={"keenable": "kn"}
        ),
    )
    assert urls == [
        "https://api.keenable.ai/v1/search/public",
        "https://api.keenable.ai/v1/search",
    ]


async def test_registry_cache_keeps_variant_when_key_added_mid_request(monkeypatch):
    monkeypatch.delenv("KEENABLE_API_KEY", raising=False)
    urls: list[str] = []
    config = ToolsConfig(web_search={"provider": "keenable"})

    async def _request_json(provider, method, url, **kwargs):
        urls.append(url)
        if len(urls) == 1:
            # A key shows up while the keyless request is still in flight.
            config.api_keys["keenable"] = "kn"
        return {
            "results": [{"title": "A", "url": "https://a.example/x", "snippet": "s"}]
        }

    monkeypatch.setattr(wsp, "request_json", _request_json)
    registry = wsp.create_default_web_search_registry()
    await registry.search(query="q", max_results=3, config=config)
    response, _ = await registry.search(query="q", max_results=3, config=config)
    assert urls == [
        "https://api.keenable.ai/v1/search/public",
        "https://api.keenable.ai/v1/search",
    ]
    assert response.cached is False


@pytest.mark.parametrize(
    "payload", [None, [], {}, {"results": None}, {"results": "error"}]
)
async def test_invalid_payload_has_actionable_http_error(monkeypatch, payload):
    _capture(monkeypatch, payload)
    with pytest.raises(
        wsp.WebSearchProviderError, match="Try again or choose another"
    ) as error:
        await wsp.KeenableSearchProvider().search(
            query="q",
            max_results=3,
            config=ToolsConfig(web_search={"provider": "keenable"}),
        )
    assert error.value.error_type == wsp.ToolErrorType.HTTP_ERROR


@pytest.mark.parametrize(
    "kind, status, expected",
    [
        ("auth_failed", 401, wsp.ToolErrorType.AUTH_FAILED),
        ("rate_limited", 429, wsp.ToolErrorType.RATE_LIMITED),
        ("timeout", None, wsp.ToolErrorType.FETCH_TIMEOUT),
    ],
)
async def test_provider_failures_keep_their_error_category(
    monkeypatch, kind, status, expected
):
    async def request(*args, **kwargs):
        raise wsp.IntegrationHttpError(
            "Test provider failure",
            provider="keenable",
            error_kind=kind,
            status_code=status,
        )

    monkeypatch.setattr(wsp, "request_json", request)
    with pytest.raises(wsp.WebSearchProviderError) as error:
        await wsp.KeenableSearchProvider().search(
            query="q", max_results=3, config=ToolsConfig()
        )
    assert error.value.error_type == expected


@pytest.mark.asyncio(loop_scope="session")
async def test_cli_selection_reaches_the_web_search_tool(db_pool, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from apps.hexis_cli import _tools_web_search_set_provider, build_parser
    from core.tools.base import ToolContext, ToolExecutionContext
    from core.tools.config import load_tools_config, save_tools_config
    from core.tools.web import WebSearchHandler
    from tests.utils import _db_dsn

    args = build_parser().parse_args(
        ["tools", "web-search", "set-provider", "keenable"]
    )
    original = await load_tools_config(db_pool)
    try:
        assert await _tools_web_search_set_provider(_db_dsn(), args.provider) == 0
        config = await load_tools_config(db_pool)
        assert config.web_search["provider"] == "keenable"
        captured = _capture(
            monkeypatch,
            {
                "results": [
                    {"title": "A", "url": "https://a.example", "snippet": "text"}
                ]
            },
        )
        monkeypatch.delenv("KEENABLE_API_KEY", raising=False)
        context = ToolExecutionContext(
            tool_context=ToolContext.CHAT,
            call_id="keenable-cli-test",
            registry=SimpleNamespace(get_config=AsyncMock(return_value=config)),
            allow_network=True,
        )
        result = await WebSearchHandler().execute({"query": "q"}, context)
        assert result.success
        assert result.output["provider"]["id"] == "keenable"
        assert result.output["external_content"]["untrusted"] is True
        assert captured["url"].endswith("/search/public")
    finally:
        await save_tools_config(db_pool, original)
