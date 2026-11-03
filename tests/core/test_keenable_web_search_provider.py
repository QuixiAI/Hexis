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
    assert provider.is_available(ToolsConfig(web_search={"provider": "keenable"})) is True
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
                {"title": "A", "url": "https://a.example/x", "description": "desc", "snippet": "s"},
                {"title": "No URL", "description": "dropped"},
                {"title": "B", "url": "https://b.example/y", "snippet": "page text"},
            ]
        },
    )
    response = await wsp.KeenableSearchProvider().search(
        query="hexis", max_results=5, config=ToolsConfig(web_search={"provider": "keenable"})
    )
    assert captured["url"] == "https://api.keenable.ai/v1/search/public"
    assert captured["method"] == "POST"
    assert captured["json_body"] == {"query": "hexis", "max_results": 5}
    assert captured["headers"] == {"X-Keenable-Title": "Hexis"}
    assert [r.url for r in response.results] == ["https://a.example/x", "https://b.example/y"]
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
