"""Tests for Twitter/X research tools (E.6)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.tools.base import (
    ToolCategory,
    ToolContext,
    ToolErrorType,
    ToolExecutionContext,
)
from plugins.installed.twitter.tools import (
    SearchTwitterHandler,
    create_twitter_tools,
)


def _make_context():
    registry = MagicMock()
    registry.pool = MagicMock()
    return ToolExecutionContext(
        tool_context=ToolContext.CHAT,
        call_id="test-call",
        registry=registry,
    )


class TestSearchTwitterSpec:
    def test_spec_name(self):
        assert SearchTwitterHandler().spec.name == "twitter_search"

    def test_spec_category(self):
        assert SearchTwitterHandler().spec.category == ToolCategory.EXTERNAL

    def test_spec_read_only(self):
        assert SearchTwitterHandler().spec.is_read_only is True

    def test_spec_energy_cost(self):
        assert SearchTwitterHandler().spec.energy_cost == 2

    def test_spec_optional(self):
        assert SearchTwitterHandler().spec.optional is True

    def test_spec_required_params(self):
        assert "query" in SearchTwitterHandler().spec.parameters["required"]

    def test_spec_has_max_results_param(self):
        props = SearchTwitterHandler().spec.parameters["properties"]
        assert "max_results" in props
        assert props["max_results"]["type"] == "integer"


class TestTwitterNoAuthRequired:
    """Twitter fallback behavior is exercised without real network calls."""

    @staticmethod
    def _dummy_client():
        class _DummyClient:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return _DummyClient()

    @pytest.mark.asyncio
    async def test_tier1_fxtwitter_success(self):
        handler = SearchTwitterHandler()
        ctx = _make_context()

        handler._search_fxtwitter = AsyncMock(
            return_value=[
                {
                    "id": "1",
                    "text": "hello",
                    "author": "alice",
                    "created_at": "2026-01-01T00:00:00Z",
                    "metrics": {"likes": 1, "retweets": 0, "replies": 0},
                }
            ]
        )

        with patch("httpx.AsyncClient", return_value=self._dummy_client()):
            result = await handler.execute({"query": "test"}, ctx)

        assert result.success
        assert result.output["provider"] == "fxtwitter"
        assert result.output["tier"] == 1
        assert result.error_type != ToolErrorType.AUTH_FAILED

    @pytest.mark.asyncio
    async def test_falls_back_to_twitterapi_io(self):
        handler = SearchTwitterHandler()
        ctx = _make_context()

        handler._search_fxtwitter = AsyncMock(side_effect=RuntimeError("fx down"))
        handler._search_twitterapi_io = AsyncMock(
            return_value=[
                {
                    "id": "2",
                    "text": "fallback",
                    "author": "bob",
                    "created_at": "2026-01-01T00:00:00Z",
                    "metrics": {"likes": 2, "retweets": 1, "replies": 0},
                }
            ]
        )
        handler._search_xquik = AsyncMock(return_value=[])
        handler._search_twittrapi = AsyncMock(return_value=[])
        handler._search_x_api = AsyncMock(return_value=[])
        handler._search_xai = AsyncMock(return_value=[])

        async def _resolve_key(
            _context, *, explicit_resolver=None, config_key=None, env_names=()
        ):
            if config_key == "twitterapi_io":
                return "twapi-key"
            return None

        with (
            patch("httpx.AsyncClient", return_value=self._dummy_client()),
            patch(
                "plugins.installed.twitter.tools.resolve_api_key",
                side_effect=_resolve_key,
            ),
        ):
            result = await handler.execute({"query": "test"}, ctx)

        assert result.success
        assert result.output["provider"] == "twitterapi_io"
        assert result.output["tier"] == 2
        handler._search_twitterapi_io.assert_awaited_once()
        handler._search_xquik.assert_not_called()
        handler._search_twittrapi.assert_not_called()
        handler._search_x_api.assert_not_called()
        handler._search_xai.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_xquik(self):
        handler = SearchTwitterHandler()
        ctx = _make_context()

        handler._search_fxtwitter = AsyncMock(side_effect=RuntimeError("fx down"))
        handler._search_twitterapi_io = AsyncMock(return_value=[])
        handler._search_xquik = AsyncMock(
            return_value=[
                {
                    "id": "3",
                    "text": "xquik fallback",
                    "author": "carol",
                    "created_at": "2026-01-01T00:00:00Z",
                    "metrics": {"likes": 3, "retweets": 1, "replies": 1},
                }
            ]
        )
        handler._search_twittrapi = AsyncMock(return_value=[])
        handler._search_x_api = AsyncMock(return_value=[])
        handler._search_xai = AsyncMock(return_value=[])

        async def _resolve_key(
            _context, *, explicit_resolver=None, config_key=None, env_names=()
        ):
            if config_key == "twitterapi_io":
                return "twapi-key"
            if config_key == "xquik":
                return "xquik-key"
            return None

        with (
            patch("httpx.AsyncClient", return_value=self._dummy_client()),
            patch(
                "plugins.installed.twitter.tools.resolve_api_key",
                side_effect=_resolve_key,
            ),
        ):
            result = await handler.execute({"query": "test"}, ctx)

        assert result.success
        assert result.output["provider"] == "xquik"
        assert result.output["tier"] == 3
        handler._search_twitterapi_io.assert_awaited_once()
        handler._search_xquik.assert_awaited_once()
        handler._search_twittrapi.assert_not_called()
        handler._search_x_api.assert_not_called()
        handler._search_xai.assert_not_called()

    @pytest.mark.asyncio
    async def test_xquik_request_and_response_mapping(self):
        handler = SearchTwitterHandler()
        payload = {
            "tweets": [
                {
                    "id": "4",
                    "text": "mapped",
                    "createdAt": "2026-01-01T00:00:00Z",
                    "like_count": 4,
                    "retweet_count": 2,
                    "reply_count": 1,
                    "author": {"username": "dave"},
                }
            ],
            "has_next_page": False,
            "next_cursor": "",
        }

        with patch(
            "plugins.installed.twitter.tools.request_json",
            AsyncMock(return_value=payload),
        ) as request_json:
            tweets = await handler._search_xquik("from:dave", 25, "xquik-key")

        assert tweets == [
            {
                "id": "4",
                "text": "mapped",
                "author": "dave",
                "created_at": "2026-01-01T00:00:00Z",
                "metrics": {"likes": 4, "retweets": 2, "replies": 1},
            }
        ]
        request_json.assert_awaited_once()
        _, kwargs = request_json.await_args
        assert kwargs["headers"]["x-api-key"] == "xquik-key"
        assert kwargs["headers"]["xquik-api-contract"] == "2026-04-29"
        assert kwargs["params"] == {
            "q": "from:dave",
            "limit": 25,
            "queryType": "Latest",
        }

    @pytest.mark.asyncio
    async def test_falls_back_to_twittrapi(self):
        handler = SearchTwitterHandler()
        ctx = _make_context()

        handler._search_fxtwitter = AsyncMock(side_effect=RuntimeError("fx down"))
        handler._search_twitterapi_io = AsyncMock(return_value=[])
        handler._search_xquik = AsyncMock(return_value=[])
        handler._search_twittrapi = AsyncMock(
            return_value=[
                {
                    "id": "5",
                    "text": "twittrapi fallback",
                    "author": "erin",
                    "created_at": "2026-01-01T00:00:00Z",
                    "metrics": {"likes": 5, "retweets": 2, "replies": 1},
                }
            ]
        )
        handler._search_x_api = AsyncMock(return_value=[])
        handler._search_xai = AsyncMock(return_value=[])

        async def _resolve_key(
            _context, *, explicit_resolver=None, config_key=None, env_names=()
        ):
            if config_key == "twittrapi":
                return "twittrapi-key"
            return None

        with (
            patch("httpx.AsyncClient", return_value=self._dummy_client()),
            patch(
                "plugins.installed.twitter.tools.resolve_api_key",
                side_effect=_resolve_key,
            ),
        ):
            result = await handler.execute({"query": "test"}, ctx)

        assert result.success
        assert result.output["provider"] == "twittrapi"
        assert result.output["tier"] == 4
        handler._search_twitterapi_io.assert_not_called()
        handler._search_xquik.assert_not_called()
        handler._search_twittrapi.assert_awaited_once()
        handler._search_x_api.assert_not_called()
        handler._search_xai.assert_not_called()

    @pytest.mark.asyncio
    async def test_twittrapi_request_and_response_mapping(self):
        handler = SearchTwitterHandler()
        payload = {
            "data": [
                {
                    "id": "6",
                    "url": "https://x.com/frank/status/6",
                    "text": "mapped",
                    "createdAt": "2026-01-01T00:00:00Z",
                    "author": {"id": "42", "username": "frank", "name": "Frank"},
                    "counts": {
                        "likes": 6,
                        "retweets": 3,
                        "replies": 2,
                        "quotes": 0,
                        "bookmarks": 0,
                        "views": 100,
                    },
                }
            ],
            "nextCursor": None,
            "previousCursor": None,
            "hasNextPage": False,
        }

        with patch(
            "plugins.installed.twitter.tools.request_json",
            AsyncMock(return_value=payload),
        ) as request_json:
            tweets = await handler._search_twittrapi("from:frank", 25, "twittrapi-key")

        assert tweets == [
            {
                "id": "6",
                "text": "mapped",
                "author": "frank",
                "created_at": "2026-01-01T00:00:00Z",
                "metrics": {"likes": 6, "retweets": 3, "replies": 2},
            }
        ]
        request_json.assert_awaited_once()
        args, kwargs = request_json.await_args
        assert args[2] == "https://api.twittrapi.com/search"
        assert kwargs["headers"]["X-API-KEY"] == "twittrapi-key"
        assert kwargs["params"] == {
            "searchItem": "from:frank",
            "category": "Latest",
            "count": 25,
            "format": "simple",
        }

    @pytest.mark.asyncio
    async def test_twittrapi_skips_unavailable_placeholders(self):
        handler = SearchTwitterHandler()
        payload = {
            "data": [
                {"id": "7", "unavailable": True},
                {"id": "8", "unavailable": True},
            ],
            "nextCursor": None,
            "hasNextPage": False,
        }

        with patch(
            "plugins.installed.twitter.tools.request_json",
            AsyncMock(return_value=payload),
        ):
            tweets = await handler._search_twittrapi("from:gone", 25, "twittrapi-key")

        assert tweets == []

    @pytest.mark.asyncio
    async def test_all_tiers_fail_returns_error(self):
        handler = SearchTwitterHandler()
        ctx = _make_context()

        handler._search_fxtwitter = AsyncMock(side_effect=RuntimeError("fx down"))
        handler._search_twitterapi_io = AsyncMock(return_value=[])
        handler._search_xquik = AsyncMock(return_value=[])
        handler._search_twittrapi = AsyncMock(return_value=[])
        handler._search_x_api = AsyncMock(return_value=[])
        handler._search_xai = AsyncMock(return_value=[])

        async def _resolve_none(
            _context, *, explicit_resolver=None, config_key=None, env_names=()
        ):
            return None

        with (
            patch("httpx.AsyncClient", return_value=self._dummy_client()),
            patch(
                "plugins.installed.twitter.tools.resolve_api_key",
                side_effect=_resolve_none,
            ),
        ):
            result = await handler.execute({"query": "test"}, ctx)

        assert not result.success
        assert "all tiers" in (result.error or "").lower()
        assert result.error_type != ToolErrorType.AUTH_FAILED


class TestTwitterFactory:
    def test_factory_count(self):
        tools = create_twitter_tools()
        assert len(tools) == 1

    def test_factory_names(self):
        names = {t.spec.name for t in create_twitter_tools()}
        assert names == {"twitter_search"}

    def test_factory_passes_resolver(self):
        resolver = lambda: "test-key"
        tools = create_twitter_tools(api_key_resolver=resolver)
        assert tools[0]._api_key_resolver is resolver


class TestTwittrapiResponseValidation:
    @pytest.mark.asyncio
    async def test_malformed_entries_do_not_hide_later_valid_tweets(self, caplog):
        payload = {
            "data": [
                {},
                None,
                {"id": "deleted", "unavailable": True},
                {"id": "blank", "text": "  "},
                {"id": "bad-author", "text": "text", "author": ["not an object"]},
                {"id": "bad-counts", "text": "text", "counts": {"likes": "invalid"}},
                {
                    "id": "1865200039848858023",
                    "text": "A real tweet 🐦",
                    "author": {"username": "alice"},
                },
                {"id": "extra", "text": "Not requested"},
            ]
        }
        with patch(
            "plugins.installed.twitter.tools.request_json",
            AsyncMock(return_value=payload),
        ):
            tweets = await SearchTwitterHandler()._search_twittrapi("q", 1, "test-key")
        assert len(tweets) == 1
        assert tweets[0]["id"] == "1865200039848858023"
        assert tweets[0]["text"] == "A real tweet 🐦"
        assert tweets[0]["author"] == "alice"
        assert "Skipping" in caplog.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload",
        [
            None,
            [],
            {},
            {"data": None},
            {"data": {}},
            {"errors": [{"message": "upstream failed"}]},
        ],
    )
    async def test_invalid_envelopes_are_errors_not_empty_searches(self, payload):
        with patch(
            "plugins.installed.twitter.tools.request_json",
            AsyncMock(return_value=payload),
        ):
            with pytest.raises(ValueError, match="instead of tweets"):
                await SearchTwitterHandler()._search_twittrapi("q", 1, "test-key")

    @pytest.mark.asyncio
    async def test_empty_page_is_valid(self):
        with patch(
            "plugins.installed.twitter.tools.request_json",
            AsyncMock(return_value={"data": []}),
        ):
            assert (
                await SearchTwitterHandler()._search_twittrapi("q", 1, "test-key") == []
            )

    @pytest.mark.asyncio
    async def test_real_http_parser_preserves_the_documented_simple_format(self):
        import httpx

        client_type = httpx.AsyncClient
        captured = []

        def respond(request):
            captured.append(request)
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "1234567890123456789",
                            "text": "A long post " + "x" * 400,
                            "author": None,
                            "createdAt": None,
                            "counts": {"likes": 12, "retweets": 2, "replies": 1},
                        }
                    ],
                    "nextCursor": None,
                    "hasNextPage": False,
                },
            )

        def client(**kwargs):
            kwargs["transport"] = httpx.MockTransport(respond)
            return client_type(**kwargs)

        with patch("httpx.AsyncClient", side_effect=client):
            tweets = await SearchTwitterHandler()._search_twittrapi(
                "from:alice lang:en", 3, "test-key"
            )
        assert tweets[0]["id"] == "1234567890123456789"
        assert tweets[0]["text"] == "A long post " + "x" * 400
        assert tweets[0]["metrics"] == {"likes": 12, "retweets": 2, "replies": 1}
        assert len(captured) == 1
        assert captured[0].url.params["searchItem"] == "from:alice lang:en"
        assert captured[0].url.params["format"] == "simple"
        assert captured[0].headers["X-API-KEY"] == "test-key"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "twittr_payload",
        [
            {"data": []},
            {"data": [{"id": "deleted", "unavailable": True}]},
            {"errors": [{"message": "upstream failed"}]},
            {"data": [{}]},
        ],
    )
    async def test_configured_key_resolution_and_fallback_to_official_x(
        self, monkeypatch, twittr_payload
    ):
        from core.tools.config import ToolsConfig

        for name in (
            "TWITTERAPI_IO_API_KEY",
            "XQUIK_API_KEY",
            "TWITTRAPI_API_KEY",
            "X_API_BEARER_TOKEN",
            "TWITTER_BEARER_TOKEN",
            "XAI_API_KEY",
        ):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("HEXIS_TEST_TWITTRAPI", "test-twittr-key")
        monkeypatch.setenv("HEXIS_TEST_X_BEARER", "test-x-key")
        ctx = _make_context()
        ctx.registry.get_config = AsyncMock(
            return_value=ToolsConfig(
                api_keys={
                    "twittrapi": "env:HEXIS_TEST_TWITTRAPI",
                    "x_api_bearer": "env:HEXIS_TEST_X_BEARER",
                }
            )
        )
        calls = []

        async def request(provider, method, url, **kwargs):
            calls.append(provider)
            if provider == "fxtwitter":
                return {"tweets": []}
            if provider == "twittrapi":
                assert kwargs["headers"]["X-API-KEY"] == "test-twittr-key"
                return twittr_payload
            if provider == "x_api":
                return {"data": [{"id": "official", "text": "Fallback result"}]}
            raise AssertionError(f"Unexpected provider: {provider}")

        with patch("plugins.installed.twitter.tools.request_json", side_effect=request):
            result = await SearchTwitterHandler().execute({"query": "q"}, ctx)
        assert result.success
        assert result.output["provider"] == "x_api_v2"
        assert result.output["tier"] == 5
        assert calls == ["fxtwitter", "twittrapi", "x_api"]

    @pytest.mark.asyncio
    async def test_missing_key_skips_twittrapi_and_records_the_reason(
        self, monkeypatch
    ):
        from core.tools.config import ToolsConfig

        for name in (
            "TWITTERAPI_IO_API_KEY",
            "XQUIK_API_KEY",
            "TWITTRAPI_API_KEY",
            "X_API_BEARER_TOKEN",
            "TWITTER_BEARER_TOKEN",
            "XAI_API_KEY",
        ):
            monkeypatch.delenv(name, raising=False)
        ctx = _make_context()
        ctx.registry.get_config = AsyncMock(return_value=ToolsConfig())
        request = AsyncMock(return_value={"tweets": []})
        with patch("plugins.installed.twitter.tools.request_json", request):
            result = await SearchTwitterHandler().execute({"query": "q"}, ctx)
        assert not result.success
        assert "TwittrAPI: skipped (no API key)" in result.error
        assert request.await_count == 1
