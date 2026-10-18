"""Each provider's route through LiteLLM: model string and connection arguments."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core import llm

pytestmark = pytest.mark.core


@pytest.mark.parametrize("provider,model,endpoint,key,expected_model,expected", [
    ("openai", "gpt-5", None, "k", "openai/gpt-5", {"api_key": "k"}),
    ("openai_compatible", "qwen3", "http://host.docker.internal:8000/v1", None,
     "openai/qwen3", {"api_base": "http://host.docker.internal:8000/v1", "api_key": "not-needed"}),
    ("openai-chat-completions-endpoint", "m", "http://box:8000/v1", "k",
     "openai/m", {"api_base": "http://box:8000/v1", "api_key": "k"}),
    ("grok", "grok-4", "https://api.x.ai/v1", "k", "xai/grok-4", {"api_base": "https://api.x.ai/v1", "api_key": "k"}),
    ("chutes", "deepseek-ai/DeepSeek-V3", "https://api.chutes.ai/v1", "oauth-access",
     "openai/deepseek-ai/DeepSeek-V3", {"api_base": "https://api.chutes.ai/v1", "api_key": "oauth-access"}),
    ("qwen-portal", "qwen-max", "https://portal.qwen.ai/v1", "oauth-access",
     "openai/qwen-max", {"api_base": "https://portal.qwen.ai/v1", "api_key": "oauth-access"}),
    ("minimax-portal", "MiniMax-M2", "https://api.minimax.io/anthropic", "oauth-access",
     "anthropic/MiniMax-M2", {"api_base": "https://api.minimax.io/anthropic", "api_key": "oauth-access"}),
    ("openrouter", "anthropic/claude-sonnet-4.5", "https://openrouter.ai/api/v1", "k",
     "openrouter/anthropic/claude-sonnet-4.5", {"api_base": "https://openrouter.ai/api/v1", "api_key": "k"}),
    ("anthropic", "claude-sonnet-4-5", None, "k", "anthropic/claude-sonnet-4-5", {"api_key": "k"}),
    ("gemini", "gemini-2.5-pro", None, "k", "gemini/gemini-2.5-pro", {"api_key": "k"}),
    ("azure_openai", "my-deployment", "https://res.openai.azure.com/openai/v1", "k",
     "azure/my-deployment", {"api_base": "https://res.openai.azure.com", "api_version": "v1", "api_key": "k"}),
    ("bedrock", "us.anthropic.claude-sonnet-4-5-20250929-v1:0", "us-west-2", None,
     "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0", {"aws_region_name": "us-west-2"}),
    ("bedrock", "anthropic.claude-x", None, "bedrock-key",
     "bedrock/anthropic.claude-x", {"aws_region_name": "us-east-1", "api_key": "bedrock-key"}),
    ("vertex", "gemini-2.5-pro", "projects/my-proj/locations/us-central1", None,
     "vertex_ai/gemini-2.5-pro", {"vertex_project": "my-proj", "vertex_location": "us-central1"}),
    ("vertex", "claude-sonnet-4-5@20250929", "projects/my-proj/locations/us-east5", '{"type": "service_account"}',
     "vertex_ai/claude-sonnet-4-5@20250929",
     {"vertex_project": "my-proj", "vertex_location": "us-east5", "vertex_credentials": '{"type": "service_account"}'}),
])
def test_each_provider_routes_through_litellm(provider, model, endpoint, key, expected_model, expected):
    assert llm._litellm_target(provider, model, endpoint, key) == (expected_model, expected)


def test_github_copilot_sends_the_editor_headers():
    from core.auth.github_copilot import COPILOT_REQUEST_HEADERS

    target, connection = llm._litellm_target(
        "github-copilot", "gpt-4o", "https://api.individual.githubcopilot.com", "copilot-token",
    )
    assert target == "openai/gpt-4o"
    assert connection["api_base"] == "https://api.individual.githubcopilot.com"
    assert connection["api_key"] == "copilot-token"
    assert connection["extra_headers"] == COPILOT_REQUEST_HEADERS


def test_anthropic_setup_token_adds_the_claude_code_headers():
    _, connection = llm._litellm_target("anthropic", "claude-sonnet-4-5", None, "sk-ant-oat01-x", auth_mode="setup-token")
    assert connection["api_key"] == "sk-ant-oat01-x"
    assert connection["extra_headers"] == llm._CLAUDE_CODE_HEADERS
    # An API key has no such headers.
    assert "extra_headers" not in llm._litellm_target("anthropic", "claude-sonnet-4-5", None, "sk-ant-api")[1]


def test_vertex_without_a_project_is_a_clear_error():
    with pytest.raises(RuntimeError, match="project"):
        llm._litellm_target("vertex", "gemini-2.5-pro", "", None)


def test_codex_goes_through_litellms_chatgpt_provider_with_no_key_or_endpoint():
    # LiteLLM reads the ChatGPT token from its own file; nothing else is passed.
    assert llm._litellm_target("openai-codex", "gpt-5.5", "https://wrong.test", "typed") == ("chatgpt/responses/gpt-5.5", {})


def test_code_assist_providers_are_not_litellm_targets():
    for provider in llm.CODE_ASSIST_PROVIDERS:
        with pytest.raises(ValueError):
            llm._litellm_target(provider, "gemini-2.5-flash", None, "tok")


@pytest.mark.parametrize("provider,model,expected_model,expected_base", [
    ("zai", "glm-5.3", "zai/glm-5.3", "https://api.z.ai/api/paas/v4"),
    ("deepseek", "deepseek-v4-pro", "deepseek/deepseek-v4-pro", "https://api.deepseek.com"),
    ("qwen", "qwen3.8-max", "dashscope/qwen3.8-max", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
    ("qwen_coding", "qwen3-coder-plus", "dashscope/qwen3-coder-plus", "https://coding-intl.dashscope.aliyuncs.com/v1"),
    # Meta serves Muse only through the Responses API.
    ("meta", "muse-spark-1.3", "openai/responses/muse-spark-1.3", "https://api.meta.ai/v1"),
])
def test_added_providers_route_through_litellm_with_their_default_endpoints(provider, model, expected_model, expected_base):
    endpoint = llm.normalize_endpoint(provider, None)
    assert llm._litellm_target(provider, model, endpoint, "k") == (expected_model, {"api_base": expected_base, "api_key": "k"})


def test_a_chosen_plan_endpoint_is_kept():
    assert llm._litellm_target("zai", "glm-5.3", "https://open.bigmodel.cn/api/coding/paas/v4", "k")[1]["api_base"] == \
        "https://open.bigmodel.cn/api/coding/paas/v4"


@pytest.mark.asyncio(loop_scope="session")
async def test_xai_oauth_calls_grok_with_a_fresh_access_token(monkeypatch):
    from core.auth import xai_oauth

    creds = xai_oauth.XaiCredentials("fresh-access", "refresh", 0, "https://auth.x.ai/oauth2/token")
    monkeypatch.setattr(xai_oauth, "ensure_fresh_xai_credentials", AsyncMock(return_value=creds))
    sent = {}

    async def acompletion(**request):
        sent.update(request)
        choice = SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=[]), finish_reason="stop")
        return SimpleNamespace(choices=[choice])

    monkeypatch.setattr(llm.litellm, "acompletion", acompletion)
    result = await llm.chat_completion(
        provider="xai-oauth", model="grok-4", endpoint=None, api_key="ignored-typed-key",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result["content"] == "ok"
    assert (sent["model"], sent["api_key"], sent["api_base"]) == ("xai/grok-4", "fresh-access", "https://api.x.ai/v1")


def test_xai_discovery_must_stay_on_x_ai():
    from core.auth import xai_oauth as xai

    assert xai._trusted("https://auth.x.ai/oauth2/token", "t") == "https://auth.x.ai/oauth2/token"
    for bad in ("https://evil.example/token", "http://auth.x.ai/token", "https://x.ai.evil.example/token"):
        with pytest.raises(RuntimeError):
            xai._trusted(bad, "t")


@pytest.mark.asyncio(loop_scope="session")
async def test_xai_poll_reads_pending_and_slow_down_from_the_error_body(monkeypatch):
    import json

    from core.auth import xai_oauth as xai
    from core.integration_reliability import IntegrationHttpError

    device = xai.XaiDeviceCode("dev", "XAI-1", "https://accounts.x.ai/device", 5, 300, "https://auth.x.ai/oauth2/token")

    def error(code):
        return IntegrationHttpError("x", provider="p", error_kind="http", status_code=400, response_body=json.dumps({"error": code}))

    monkeypatch.setattr(xai, "request_json", AsyncMock(side_effect=error("authorization_pending")))
    assert await xai.poll_device_code(device) is None
    monkeypatch.setattr(xai, "request_json", AsyncMock(side_effect=error("slow_down")))
    with pytest.raises(xai.SlowDown):
        await xai.poll_device_code(device)
    monkeypatch.setattr(xai, "request_json", AsyncMock(side_effect=error("access_denied")))
    with pytest.raises(xai.SignInEnded):
        await xai.poll_device_code(device)
    monkeypatch.setattr(xai, "request_json", AsyncMock(return_value={"access_token": "a", "refresh_token": "r", "expires_in": 3600}))
    assert (await xai.poll_device_code(device)).refresh == "r"
