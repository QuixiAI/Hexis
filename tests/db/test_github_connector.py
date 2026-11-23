"""End-to-end DB + tool tests for the first-class GitHub Issues connector (#102)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.auth.github_pat import (
    GITHUB_CONNECTOR_ID,
    complete_github_connection_db,
    load_credentials,
    revoke_github_connection_db,
)
from core.tools import ToolContext, ToolExecutionContext, create_default_registry


@pytest.mark.asyncio(loop_scope="session")
async def test_github_issues_connector_resumable_flow_never_stores_secret_in_db(
    db_pool, monkeypatch, tmp_path
) -> None:
    # Apply migration 0267 idempotently in case the test DB hasn't been bounced yet.
    migration_sql = Path("db/migrations/0267_github_issues_connector.sql").read_text(
        encoding="utf-8"
    )
    async with db_pool.acquire() as conn:
        await conn.execute(migration_sql)
        connector_row = await conn.fetchrow(
            "SELECT id, display_name, category, auth_type, status FROM integration_connectors WHERE id = $1",
            GITHUB_CONNECTOR_ID,
        )
    assert connector_row is not None
    assert connector_row["display_name"] == "GitHub Issues"
    assert connector_row["auth_type"] == "api_key"
    assert connector_row["status"] == "available"

    # Isolate the local auth store to tmp_path so we never touch ~/.hexis/auth.
    monkeypatch.setenv("HEXIS_AUTH_DIR", str(tmp_path))
    fake_secret = "github_pat_test_secret_never_in_postgres_98765"

    async def fake_verify_pat(token: str) -> tuple[str | None, str | None]:
        assert token == fake_secret
        return "octocat", None

    monkeypatch.setattr("core.auth.github_pat.verify_pat", fake_verify_pat)

    registry = create_default_registry(db_pool)
    ctx = ToolExecutionContext(
        tool_context=ToolContext.CHAT,
        call_id="gh-flow-1",
        session_id="chat-session-gh-102",
        registry=registry,
    )

    try:
        # 1. Start guided setup with a pending issue draft (plus a stray secret key that must be stripped).
        start_res = await registry.execute(
            "start_github_setup",
            {
                "capabilities": ["search_issues", "read_issues", "create_issue"],
                "pending_action": {
                    "action": "create_issue",
                    "repo": "QuixiAI/Hexis",
                    "title": "Fix GitHub flow",
                    "body": "Repro details",
                    "labels": ["bug"],
                    "authorized": False,
                    "token": "should-never-be-persisted",
                },
                "source_channel": "chat",
            },
            ctx,
        )
        assert start_res.success, start_res.error
        assert start_res.output["status"] == "pending_setup"
        assert start_res.output["ui"]["kind"] == "connector_setup"
        assert start_res.output["ui"]["connector_id"] == GITHUB_CONNECTOR_ID
        assert "token" not in (start_res.output.get("pending_action") or {})
        assert start_res.output["pending_action"]["repo"] == "QuixiAI/Hexis"
        attempt_id = start_res.output["attempt_id"]

        # 2. Check status before auth completes: preserves pending_action.
        pre_status = await registry.execute("github_setup_status", {}, ctx)
        assert pre_status.success
        assert pre_status.output["credentials_saved"] is False
        assert pre_status.output["pending_action"]["title"] == "Fix GitHub flow"

        # 3. Complete setup out-of-band (web Connections / inline card / CLI):
        # verifies PAT, writes token ONLY to ~/.hexis/auth, records @octocat in Postgres,
        # and returns the preserved pending_action for immediate resume.
        completed = await complete_github_connection_db(
            db_pool,
            token=fake_secret,
            attempt_id=attempt_id,
            source_channel="web",
            source_session_id="chat-session-gh-102",
        )
        assert completed["connected"] is True
        assert completed["login"] == "octocat"
        assert completed["credential_ref"] == "token.github_pat"
        assert completed["pending_action"]["repo"] == "QuixiAI/Hexis"
        assert completed["pending_action"]["title"] == "Fix GitHub flow"
        assert fake_secret not in json.dumps(completed)

        # Verify local auth store has the token.
        stored = load_credentials()
        assert stored is not None
        assert stored.token == fake_secret
        assert stored.login == "octocat"

        # Verify Postgres has NO trace of the raw PAT anywhere in attempts or connections.
        async with db_pool.acquire() as conn:
            attempt_dump = await conn.fetchval(
                "SELECT row_to_json(connection_attempts)::text FROM connection_attempts WHERE id = $1::uuid",
                attempt_id,
            )
            conn_dump = await conn.fetchval(
                """
                SELECT row_to_json(integration_connections)::text
                FROM integration_connections
                WHERE connector_id = $1 AND account_key = 'octocat'
                """,
                GITHUB_CONNECTOR_ID,
            )
        assert attempt_dump and fake_secret not in attempt_dump
        assert conn_dump and fake_secret not in conn_dump
        assert "should-never-be-persisted" not in attempt_dump

        # 4. Status after auth completes: connected as @octocat with resumable pending_action.
        post_status = await registry.execute("github_setup_status", {}, ctx)
        assert post_status.success
        assert post_status.output["credentials_saved"] is True
        assert post_status.output["verified_login"] == "octocat"
        assert post_status.output["ui"]["status"] == "connected"
        assert post_status.output["pending_action"]["repo"] == "QuixiAI/Hexis"

        # 5. Revoking clears both local store and DB connection status.
        revoked = await revoke_github_connection_db(
            db_pool, account_key="octocat", reason="test cleanup"
        )
        assert revoked["revoked"]
        assert load_credentials() is None
        async with db_pool.acquire() as conn:
            status_after = await conn.fetchval(
                """
                SELECT status FROM integration_connections
                WHERE connector_id = $1 AND account_key = 'octocat'
                """,
                GITHUB_CONNECTOR_ID,
            )
        assert status_after == "revoked"
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM connection_attempts WHERE connector_id = $1 AND source_session_id = $2",
                GITHUB_CONNECTOR_ID,
                "chat-session-gh-102",
            )
            await conn.execute(
                "DELETE FROM integration_connections WHERE connector_id = $1 AND account_key = 'octocat'",
                GITHUB_CONNECTOR_ID,
            )
