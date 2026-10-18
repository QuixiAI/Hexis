"""The dashboard's Models step: keys to the auth store, config to the DB, and
one entry per vendor whose form offers the vendor's subscription login."""
from __future__ import annotations

import base64
import json
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from core import init_api, llm
from core.auth import store, ui_flow
from core.auth import openai_codex as codex

pytestmark = [pytest.mark.core, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture(autouse=True)
def isolated_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "AUTH_DIR", tmp_path / "auth")
    monkeypatch.setenv("CHATGPT_TOKEN_DIR", str(tmp_path / "litellm-chatgpt"))
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def codex_credentials():
    payload = base64.urlsafe_b64encode(
        json.dumps({"https://api.openai.com/auth": {"chatgpt_account_id": "acct"}}).encode()
    ).decode().rstrip("=")
    return codex.OpenAICodexCredentials(f"x.{payload}.x", "refresh-secret", int(time.time() * 1000) + 600000, "acct")


class _Conn:
    def __init__(self):
        self.calls = []

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        return json.dumps({"stage": "llm"})


# ---------------------------------------------------------------------------
# key_source / validate / save
# ---------------------------------------------------------------------------


async def test_typed_keys_go_to_the_auth_store_and_the_config_keeps_only_a_reference():
    conn = _Conn()
    roles = {
        "conscious": {"provider": "openrouter", "model": "anthropic/claude-sonnet-4.5", "api_key": "or-secret"},
        "subconscious": {"provider": "openrouter", "model": "anthropic/claude-sonnet-4.5", "api_key": "or-secret"},
    }
    result = await init_api.save_llm_setup(conn, roles)
    assert "or-secret" not in json.dumps(result)
    assert result["config"]["conscious"] == {
        "provider": "openrouter", "model": "anthropic/claude-sonnet-4.5",
        "endpoint": "https://openrouter.ai/api/v1", "api_key_ref": "llm.conscious",
    }
    assert store.load_auth("llm.conscious")["api_key"] == "or-secret"
    assert (store.AUTH_DIR / "llm.conscious.json").stat().st_mode & 0o777 == 0o600
    # The running agent resolves the reference through the normal config path.
    assert llm.normalize_llm_config(result["config"]["conscious"])["api_key"] == "or-secret"
    assert init_api.key_source("openrouter", "conscious") == "stored"
    # A key saved for one provider is not another provider's key.
    assert init_api.key_source("deepseek", "conscious") is None


async def test_reusing_the_primary_copies_its_saved_key_server_side():
    conn = _Conn()
    await init_api.save_llm_setup(conn, {
        "conscious": {"provider": "openai", "model": "gpt-4o", "api_key": "sk-first"},
        "subconscious": {"provider": "anthropic", "model": "claude", "api_key": "sk-ant-second"},
    })
    result = await init_api.save_llm_setup(conn, {
        "conscious": {"provider": "openai", "model": "gpt-4o", "api_key": ""},
    }, reuse_primary=True)
    assert result["config"]["subconscious"]["provider"] == "openai"
    assert result["config"]["subconscious"]["api_key_ref"] == "llm.subconscious"
    assert store.load_auth("llm.subconscious")["api_key"] == "sk-first"


async def test_switching_provider_without_a_key_drops_the_old_one():
    conn = _Conn()
    await init_api.save_llm_setup(conn, {
        "conscious": {"provider": "openai", "model": "gpt-4o", "api_key": "sk-first"},
        "subconscious": {"provider": "openai", "model": "gpt-4o", "api_key": "sk-first"},
    })
    with pytest.raises(ValueError, match="conscious API key"):
        await init_api.save_llm_setup(conn, {
            "conscious": {"provider": "deepseek", "model": "deepseek-v4", "api_key": ""},
            "subconscious": {"provider": "deepseek", "model": "deepseek-v4", "api_key": ""},
        })


def test_validation_names_what_each_provider_needs(monkeypatch):
    def missing(config):
        return init_api.validate_llm_setup({"conscious": config, "subconscious": config})

    assert "conscious endpoint" in missing({"provider": "azure_openai", "model": "dep", "api_key": "k"})
    assert "conscious API key" in missing({"provider": "openrouter", "model": "m"})
    assert "conscious Google Cloud project" in missing({"provider": "vertex", "model": "m", "endpoint": ""})
    # Bedrock and Vertex can rely on the server's own cloud credentials.
    assert missing({"provider": "bedrock", "model": "m", "endpoint": "us-east-1"}) == []
    assert "conscious ChatGPT sign-in" in missing({"provider": "openai-codex", "model": "m"})
    assert "conscious GitHub Copilot sign-in" in missing({"provider": "github-copilot", "model": "m"})
    assert "conscious provider (nope is not supported)" in missing({"provider": "nope", "model": "m"})
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
    assert missing({"provider": "openrouter", "model": "m"}) == []
    assert init_api.key_source("openrouter", "conscious") == "env"


async def test_a_claude_setup_token_counts_as_the_anthropic_credential():
    from core.auth.anthropic_setup_token import AnthropicSetupTokenCredentials, save_credentials

    token = "sk-ant-oat01-" + "t" * 80
    save_credentials(AnthropicSetupTokenCredentials(token=token))
    assert init_api.key_source("anthropic", "conscious") == "token"
    assert init_api.validate_llm_setup({r: {"provider": "anthropic", "model": "claude"} for r in init_api.LLM_ROLES}) == []
    resolved = await init_api.resolve_setup_credentials("conscious", {"provider": "anthropic", "model": "claude"})
    assert (resolved["api_key"], resolved["auth_mode"]) == (token, "setup-token")
    # A typed key wins over the token.
    typed = await init_api.resolve_setup_credentials("conscious", {"provider": "anthropic", "model": "claude", "api_key": "sk-ant-api"})
    assert typed["api_key"] == "sk-ant-api" and "auth_mode" not in typed


async def test_a_subscription_sign_in_is_the_credential_and_typed_keys_are_ignored(monkeypatch):
    codex.save_openai_codex_credentials(codex_credentials())
    assert init_api.key_source("openai-codex", "conscious") == "oauth"
    conn = _Conn()
    result = await init_api.save_llm_setup(conn, {
        "conscious": {"provider": "openai-codex", "model": "gpt-5.2", "api_key": "must-not-save", "endpoint": "https://wrong.test"},
    }, reuse_primary=True)
    assert "must-not-save" not in json.dumps(result)
    assert store.load_auth("llm.conscious") is None
    assert result["config"]["conscious"]["endpoint"] == "https://chatgpt.com/backend-api"
    models = AsyncMock(return_value=["account-model"])
    monkeypatch.setattr(codex, "list_openai_codex_models", models)
    assert await init_api.list_models("conscious", {"provider": "openai-codex"}) == {
        "models": ["account-model"], "key_source": "oauth",
    }


async def test_listing_asks_the_endpoint_with_the_resolved_key(monkeypatch):
    seen = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"data": [{"id": "b"}, {"id": "a"}]}

    class _Client:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, headers=None):
            seen.update(url=url, headers=headers)
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    result = await init_api.list_models("conscious", {"provider": "deepseek", "api_key": "ds-key"})
    assert result == {"models": ["a", "b"], "key_source": "typed"}
    assert seen == {"url": "https://api.deepseek.com/models", "headers": {"Authorization": "Bearer ds-key"}}
    # Providers without a list come back empty so the catalog fills in.
    assert await init_api.list_models("conscious", {"provider": "anthropic", "api_key": "k"}) == {"models": [], "key_source": "typed"}


async def test_listing_models_never_sends_another_providers_saved_key(monkeypatch):
    # A key saved for one provider must not reach a different provider's
    # endpoint when the user switches provider in the form without typing a key.
    store.save_auth("llm.conscious", {"api_key": "old-anthropic-key", "provider": "anthropic"})
    seen = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"data": [{"id": "local-model"}]}

    class _Client:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, headers=None):
            seen.update(url=url, headers=headers or {})
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    result = await init_api.list_models("conscious", {
        "provider": "openai_compatible", "endpoint": "https://new-provider.invalid/v1",
    })
    assert result["models"] == ["local-model"]
    assert result["key_source"] is None
    assert "Authorization" not in seen["headers"]
    cfg = init_api.resolve_setup_config("conscious", {"provider": "openai", "model": "m"})
    assert cfg["api_key"] is None
    monkeypatch.setenv("OPENAI_API_KEY", "new-provider-env-key")
    cfg = init_api.resolve_setup_config("conscious", {"provider": "openai", "model": "m"})
    assert cfg["api_key"] == "new-provider-env-key"
    assert cfg["key_source"] == "env"


# ---------------------------------------------------------------------------
# The HTTP surface the dashboard proxies to
# ---------------------------------------------------------------------------


def _app():
    from apps import hexis_api

    return hexis_api


async def test_setup_endpoints_save_list_test_and_forget(monkeypatch):
    api = _app()
    conn = _Conn()

    class _Pool:
        def acquire(self):
            class _Ctx:
                async def __aenter__(self_inner): return conn
                async def __aexit__(self_inner, *a): pass
            return _Ctx()

    monkeypatch.setattr(api, "_pool", _Pool())
    monkeypatch.setattr(init_api, "test_llm_connection", AsyncMock(return_value={"ok": True, "status": "ok", "message": "Connected."}))
    app = FastAPI()
    for route in api.app.routes:
        if getattr(route, "path", "").startswith("/api/init/"):
            app.router.routes.append(route)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        saved = await client.post("/api/init/llm", json={
            "conscious": {"provider": "zai", "model": "glm-5.3", "api_key": "zai-secret"}, "reuse_primary": True,
        })
        assert saved.status_code == 200 and "zai-secret" not in saved.text
        assert saved.json()["config"]["subconscious"]["api_key_ref"] == "llm.subconscious"

        rejected = await client.post("/api/init/llm", json={"conscious": {"provider": "openrouter", "model": ""}})
        assert rejected.status_code == 400 and "conscious model" in rejected.json()["error"]

        tested = await client.post("/api/init/llm/test", json={"role": "conscious", "provider": "zai", "model": "glm-5.3"})
        assert tested.json() == {"ok": True, "status": "ok", "message": "Connected.", "key_source": "stored"}
        passed = init_api.test_llm_connection.await_args.args[0]
        assert passed["api_key"] == "zai-secret" and passed["model"] == "glm-5.3"

        token = "sk-ant-oat01-" + "t" * 80
        assert (await client.post("/api/init/auth/setup-token", json={"provider": "anthropic", "token": "bad"})).status_code == 400
        ok = await client.post("/api/init/auth/setup-token", json={"provider": "anthropic", "token": token})
        assert ok.json() == {"provider": "anthropic", "configured": True}
        assert (await client.get("/api/init/auth/status?provider=anthropic")).json()["configured"]
        assert (await client.post("/api/init/auth/forget", json={"provider": "anthropic"})).json()["configured"] is False


# ---------------------------------------------------------------------------
# Browser sign-ins: one-time codes for ChatGPT and xAI
# ---------------------------------------------------------------------------


async def _wait_for(coordinator, session_id, statuses, timeout=15.0):
    """The coordinator polls at the provider's interval (at least a second)."""
    import asyncio

    deadline = time.monotonic() + timeout
    while True:
        session = coordinator.session(session_id)
        if session["status"] in statuses or time.monotonic() > deadline:
            return session
        await asyncio.sleep(0.05)


async def test_chatgpt_login_shows_a_code_and_stores_the_tokens_when_approved(monkeypatch):
    device = codex.DeviceCode("device-id", "ABCD-1234", 0)
    monkeypatch.setattr(codex, "request_device_code", AsyncMock(return_value=device))
    monkeypatch.setattr(codex, "poll_device_code", AsyncMock(side_effect=[None, codex_credentials()]))
    coordinator = ui_flow.AuthFlowCoordinator(enable_callbacks=False)
    started = await coordinator.start("openai-codex")
    assert started["flow"] == "device_code"
    assert started["user_code"] == "ABCD-1234"
    assert started["verification_uri"] == "https://auth.openai.com/codex/device"
    assert "device-id" not in json.dumps(started)
    session = await _wait_for(coordinator, started["session_id"], {"complete"})
    assert session["status"] == "complete"
    assert session["credential"]["configured"] is True
    assert "refresh-secret" not in json.dumps(session)
    assert codex.load_openai_codex_credentials().refresh == "refresh-secret"
    await coordinator.close()


async def test_chatgpt_login_falls_back_to_the_browser_when_openai_gives_no_code(monkeypatch):
    monkeypatch.setattr(codex, "request_device_code", AsyncMock(side_effect=RuntimeError("no code")))
    coordinator = ui_flow.AuthFlowCoordinator(enable_callbacks=False)
    started = await coordinator.start("openai-codex")
    assert started["flow"] == "authorization_code"
    assert "auth.openai.com/oauth/authorize" in started["authorization_url"]
    await coordinator.close()


async def test_xai_login_saves_tokens_and_a_denied_login_ends(monkeypatch):
    from core.auth import xai_oauth as xai

    device = xai.XaiDeviceCode("dev", "XAI-1", "https://accounts.x.ai/device?code=XAI-1", 0, 300, "https://auth.x.ai/oauth2/token")
    monkeypatch.setattr(xai, "request_device_code", AsyncMock(return_value=device))
    creds = xai.XaiCredentials("xai-access", "xai-refresh-secret", int(time.time() * 1000) + 600000, device.token_endpoint)
    monkeypatch.setattr(xai, "poll_device_code", AsyncMock(side_effect=[None, xai.SlowDown(), creds]))
    coordinator = ui_flow.AuthFlowCoordinator(enable_callbacks=False)
    assert coordinator.status("xai-oauth")["configured"] is False
    started = await coordinator.start("xai-oauth")
    assert started["verification_uri"] == "https://accounts.x.ai/device?code=XAI-1"
    session = await _wait_for(coordinator, started["session_id"], {"complete"})
    assert session["status"] == "complete"
    assert "xai-refresh-secret" not in json.dumps(session)
    assert xai.load_xai_credentials().refresh == "xai-refresh-secret"
    assert coordinator.status("xai-oauth")["configured"] is True

    monkeypatch.setattr(xai, "poll_device_code", AsyncMock(side_effect=xai.SignInEnded("denied")))
    started = await coordinator.start("xai-oauth")
    session = await _wait_for(coordinator, started["session_id"], {"error", "expired"})
    assert session["status"] == "error" and "denied" in session["error"]
    await coordinator.close()
