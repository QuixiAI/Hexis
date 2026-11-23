from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

import apps.hexis_api as hexis_api
from apps.hexis_api import IntegrationActionRequest, _integration_action_arguments, app
from core.tools import ToolResult


def test_integration_action_arguments_add_web_source_context() -> None:
    args = _integration_action_arguments(
        "start_setup",
        {"connector_id": "Telegram"},
        "web-connections",
    )

    assert args["connector_id"] == "telegram"
    assert args["source_channel"] == "web"
    assert args["source_session_id"] == "web-connections"


def test_integration_action_arguments_accept_channel_configure() -> None:
    args = _integration_action_arguments(
        "configure_channel",
        {
            "connector_id": "Slack",
            "settings": {
                "bot_token": "SLACK_BOT_TOKEN",
                "app_token": "SLACK_APP_TOKEN",
            },
        },
        "web-connections",
    )

    assert args == {
        "connector_id": "slack",
        "settings": {
            "bot_token": "SLACK_BOT_TOKEN",
            "app_token": "SLACK_APP_TOKEN",
        },
    }


def test_integration_action_request_accepts_legacy_flat_payload() -> None:
    request = IntegrationActionRequest(
        action="start_setup",
        connector_id="signal",
        source_session_id="web-connections",
    )

    assert request.model_extra == {"connector_id": "signal"}


def test_integration_action_arguments_reject_gmail_manual_setup() -> None:
    with pytest.raises(HTTPException) as excinfo:
        _integration_action_arguments(
            "start_setup",
            {"connector_id": "gmail"},
            "web-connections",
        )

    assert excinfo.value.status_code == 422
    assert "connect_gmail" in str(excinfo.value.detail)


def test_integration_action_arguments_reject_github_manual_setup() -> None:
    with pytest.raises(HTTPException) as excinfo:
        _integration_action_arguments(
            "start_setup",
            {"connector_id": "github-issues"},
            "web-connections",
        )

    assert excinfo.value.status_code == 422
    assert "start_github_setup" in str(excinfo.value.detail)


def test_integration_action_arguments_add_web_source_for_github_setup() -> None:
    args = _integration_action_arguments(
        "start_github_setup",
        {"pending_action": {"repo": "QuixiAI/Hexis", "action": "create_issue"}},
        "web-chat-1",
    )
    assert args["source_channel"] == "web"
    assert args["source_session_id"] == "web-chat-1"


@pytest.mark.asyncio
async def test_connect_github_pat_and_revoke_actions_never_leak_secret(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_complete(_pool, *, token, capabilities, attempt_id, source_channel, source_session_id):
        captured["token"] = token
        captured["capabilities"] = capabilities
        captured["attempt_id"] = attempt_id
        captured["source_channel"] = source_channel
        captured["source_session_id"] = source_session_id
        return {
            "connected": True,
            "connector_id": "github_issues",
            "account_key": "octocat",
            "display_name": "@octocat",
            "login": "octocat",
            "credential_ref": "token.github_pat",
            "capabilities": capabilities or ["search_issues", "read_issues", "create_issue"],
            "connection_id": "conn-123",
            "pending_action": {"repo": "QuixiAI/Hexis", "action": "create_issue"},
        }

    async def fake_revoke(_pool, *, account_key, reason):
        captured["revoked_account"] = account_key
        captured["revoked_reason"] = reason
        return {"revoked": True, "connector_id": "github_issues", "account_key": account_key}

    monkeypatch.setattr(hexis_api, "_pool", object())
    monkeypatch.setattr("core.auth.github_pat.complete_github_connection_db", fake_complete)
    monkeypatch.setattr("core.auth.github_pat.revoke_github_connection_db", fake_revoke)

    secret_token = "github_pat_super_secret_do_not_echo_12345"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/integrations/action",
            json={
                "action": "connect_github_pat",
                "arguments": {
                    "token": secret_token,
                    "capabilities": ["search_issues", "create_issue"],
                },
                "source_session_id": "web-connections",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["output"]["login"] == "octocat"
        assert body["output"]["ui"]["kind"] == "connector_setup"
        assert body["output"]["ui"]["status"] == "connected"
        assert body["output"]["pending_action"]["repo"] == "QuixiAI/Hexis"
        assert secret_token not in response.text
        assert captured["token"] == secret_token
        assert captured["source_channel"] == "web"

        revoke_resp = await client.post(
            "/api/integrations/action",
            json={
                "action": "revoke_github",
                "arguments": {"account_key": "octocat", "reason": "done"},
            },
        )
        assert revoke_resp.status_code == 200
        assert revoke_resp.json()["success"] is True
        assert captured["revoked_account"] == "octocat"


@pytest.mark.asyncio
async def test_connect_github_pat_reports_validation_and_verification_errors(monkeypatch) -> None:
    async def fail_format(_pool, **_kwargs):
        raise ValueError("Token must start with 'github_pat_'")

    async def fail_verify(_pool, **_kwargs):
        raise RuntimeError("GitHub rejected the token (401 Unauthorized)")

    monkeypatch.setattr(hexis_api, "_pool", object())
    monkeypatch.setattr("core.auth.github_pat.complete_github_connection_db", fail_format)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        resp_empty = await client.post(
            "/api/integrations/action",
            json={"action": "connect_github_pat", "arguments": {"token": "   "}},
        )
        assert resp_empty.status_code == 422

        resp_fmt = await client.post(
            "/api/integrations/action",
            json={"action": "connect_github_pat", "arguments": {"token": "not-a-pat"}},
        )
        assert resp_fmt.status_code == 422

        monkeypatch.setattr("core.auth.github_pat.complete_github_connection_db", fail_verify)
        resp_auth = await client.post(
            "/api/integrations/action",
            json={"action": "connect_github_pat", "arguments": {"token": "github_pat_bad"}},
        )
        assert resp_auth.status_code == 400
        assert resp_auth.json()["error_type"] == "auth_failed"


@pytest.mark.asyncio
async def test_save_gmail_client_secret_action_returns_ui_without_secret(monkeypatch) -> None:
    class FakeRegistry:
        async def execute(self, tool_name, _arguments, _context):
            assert tool_name == "gmail_setup_status"
            return ToolResult.success_result(
                {
                    "connector_id": "gmail",
                    "status": "client_secret_saved",
                    "client_secret_saved": True,
                    "ui": {
                        "kind": "connector_setup",
                        "connector_id": "gmail",
                        "status": "client_secret_saved",
                        "client_secret_saved": True,
                    },
                }
            )

    def fake_save(payload, *, source):
        assert source == "web_upload"
        assert payload["installed"]["client_secret"] == "actual-secret"
        return {
            "status": "client_secret_saved",
            "connector_id": "gmail",
            "client_secret_saved": True,
            "client_id_hint": "...client",
            "source": source,
            "next_step": "Start Google sign-in from the setup panel.",
        }

    monkeypatch.setattr(hexis_api, "_pool", object())
    monkeypatch.setattr(hexis_api, "create_default_registry", lambda _pool: FakeRegistry())
    monkeypatch.setattr(
        "core.auth.google_gmail.save_gmail_client_secret_payload",
        fake_save,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/integrations/action",
            json={
                "action": "save_gmail_client_secret",
                "arguments": {
                    "client_secret_json": {
                        "installed": {
                            "client_id": "client",
                            "client_secret": "actual-secret",
                        }
                    }
                },
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["output"]["ui"]["kind"] == "connector_setup"
    assert body["output"]["ui"]["status"] == "client_secret_saved"
    assert "actual-secret" not in response.text


@pytest.mark.asyncio
async def test_gmail_oauth_callback_completes_attempt_without_manual_paste(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_complete(_pool, *, authorization_response: str):
        calls.append(authorization_response)
        return SimpleNamespace(
            account_key="eric@example.com",
            display_name="eric@example.com",
            credential_ref="integration.gmail.default",
            granted_scopes=["https://www.googleapis.com/auth/gmail.readonly"],
            capabilities=["read", "search"],
        )

    monkeypatch.setattr(hexis_api, "_pool", object())
    monkeypatch.setattr("core.auth.google_gmail.complete_gmail_oauth", fake_complete)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:43817",
    ) as client:
        response = await client.get("/?code=oauth-code&state=state-value")

    assert response.status_code == 200
    assert "Gmail connected" in response.text
    assert "eric@example.com" in response.text
    assert calls and "code=oauth-code" in calls[0]


@pytest.mark.asyncio(loop_scope="session")
async def test_heartbeats_can_be_paused_and_resumed(db_pool, monkeypatch) -> None:
    """The dashboard's Pause/Resume: resuming needs an initialized agent."""
    monkeypatch.setattr(hexis_api, "_pool", db_pool)
    async with db_pool.acquire() as conn:
        before = await conn.fetchrow("SELECT is_paused, next_heartbeat_at FROM heartbeat_state WHERE id = 1")
        ready = await conn.fetchval("SELECT is_agent_configured() AND is_init_complete() AND NOT is_agent_terminated()")
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.post("/api/heartbeat/pause")).json() == {"heartbeat_paused": True}
            resumed = await client.post("/api/heartbeat/resume")
            if ready:
                assert resumed.json() == {"heartbeat_paused": False}
            else:
                assert resumed.status_code == 409
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE heartbeat_state SET is_paused = $1, next_heartbeat_at = $2 WHERE id = 1",
                before["is_paused"], before["next_heartbeat_at"],
            )
