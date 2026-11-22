from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


def _j(value):
    return json.loads(value) if isinstance(value, str) else value


async def _sync_tool(conn, name: str, *, requires_approval: bool = True):
    return _j(await conn.fetchval(
        """
        SELECT sync_tool_definitions($1::jsonb)
        """,
        json.dumps([
            {
                "name": name,
                "description": f"{name} test tool",
                "schema": {"type": "object", "properties": {}},
                "category": "messaging",
                "energy_cost": 3,
                "allowed_contexts": ["chat", "heartbeat"],
                "requires_approval": requires_approval,
                "supports_parallel": False,
            }
        ]),
    ))


async def test_connector_actions_allow_chat_but_deny_autonomous_without_policy(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _sync_tool(conn, "slack_send")
            await conn.execute("SELECT grant_tool_approval('slack_send')")

            chat = _j(await conn.fetchval(
                "SELECT evaluate_tool_call('slack_send', $1::jsonb, $2::jsonb)",
                json.dumps({"channel": "#ops", "message": "Heads up"}),
                json.dumps({"tool_context": "chat"}),
            ))
            heartbeat = _j(await conn.fetchval(
                "SELECT evaluate_tool_call('slack_send', $1::jsonb, $2::jsonb)",
                json.dumps({"channel": "#ops", "message": "Heads up"}),
                json.dumps({"tool_context": "heartbeat", "energy_available": 20}),
            ))
        finally:
            await tr.rollback()

    assert chat["allowed"] is True
    assert chat["connector_action"]["authorization_kind"] == "interactive_chat_approval"
    assert heartbeat["allowed"] is False
    assert heartbeat["error_type"] == "approval_required"
    assert "slack/send" in heartbeat["reason"]


async def test_connector_action_policy_allows_matching_autonomous_target_and_limits(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _sync_tool(conn, "slack_send")
            await conn.execute("SELECT grant_tool_approval('slack_send')")
            policy = _j(await conn.fetchval(
                """
                SELECT grant_connector_action_policy(
                    'slack',
                    'send',
                    NULL,
                    '{"allowed_targets": ["#ops"], "max_per_day": 1}'::jsonb,
                    TRUE,
                    FALSE,
                    ARRAY['heartbeat']::text[],
                    NULL,
                    'test-session',
                    'Only operational alerts to #ops',
                    'user'
                )
                """
            ))
            allowed = _j(await conn.fetchval(
                "SELECT evaluate_tool_call('slack_send', $1::jsonb, $2::jsonb)",
                json.dumps({"channel": "#ops", "message": "Build failed"}),
                json.dumps({"tool_context": "heartbeat", "energy_available": 20}),
            ))
            denied_target = _j(await conn.fetchval(
                "SELECT evaluate_tool_call('slack_send', $1::jsonb, $2::jsonb)",
                json.dumps({"channel": "#random", "message": "Build failed"}),
                json.dumps({"tool_context": "heartbeat", "energy_available": 20}),
            ))
            await conn.fetchval(
                """
                SELECT record_tool_execution($1::jsonb)
                """,
                json.dumps({
                    "tool_name": "slack_send",
                    "arguments": {"channel": "#ops", "message": "Build failed"},
                    "tool_context": "heartbeat",
                    "call_id": "connector-action-limit",
                    "session_id": "test-session",
                    "success": True,
                    "output": {"sent": True},
                    "energy_spent": 3,
                    "duration_seconds": 0.01,
                }),
            )
            denied_limit = _j(await conn.fetchval(
                "SELECT evaluate_tool_call('slack_send', $1::jsonb, $2::jsonb)",
                json.dumps({"channel": "#ops", "message": "Build failed again"}),
                json.dumps({"tool_context": "heartbeat", "energy_available": 20}),
            ))
            audit = await conn.fetchrow(
                """
                SELECT policy_id::text, connector_id, action_kind, target, decision
                FROM connector_action_audit
                WHERE tool_name = 'slack_send'
                  AND context->>'call_id' = 'connector-action-limit'
                """
            )
        finally:
            await tr.rollback()

    assert allowed["allowed"] is True
    assert allowed["connector_action"]["policy_id"] == policy["policy_id"]
    assert denied_target["allowed"] is False
    assert denied_target["connector_action"]["target"] == "#random"
    assert denied_limit["allowed"] is False
    assert audit["policy_id"] == policy["policy_id"]
    assert audit["connector_id"] == "slack"
    assert audit["action_kind"] == "send"
    assert audit["target"] == "#ops"
    assert audit["decision"] == "allowed"


async def test_conditional_gmail_mark_read_action_only_when_argument_true(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute("SELECT set_config('integrations.gmail.heartbeat_digest_enabled', 'true'::jsonb)")
            await _sync_tool(conn, "email_read", requires_approval=False)
            plain_read = _j(await conn.fetchval(
                "SELECT evaluate_tool_call('email_read', $1::jsonb, $2::jsonb)",
                json.dumps({"message_id": "msg-1", "mark_read": False}),
                json.dumps({"tool_context": "heartbeat", "energy_available": 20}),
            ))
            mark_read = _j(await conn.fetchval(
                "SELECT evaluate_tool_call('email_read', $1::jsonb, $2::jsonb)",
                json.dumps({"message_id": "msg-1", "mark_read": True}),
                json.dumps({"tool_context": "heartbeat", "energy_available": 20}),
            ))
        finally:
            await tr.rollback()

    assert plain_read["allowed"] is True
    assert plain_read["connector_action"]["action_required"] is False
    assert mark_read["allowed"] is False
    assert mark_read["connector_action"]["connector_id"] == "gmail"
    assert mark_read["connector_action"]["action_kind"] == "mark_read"


async def test_gmail_heartbeat_and_memory_policies_enforced_mechanically(db_pool):
    """#100: heartbeat inbox reads require integrations.gmail.heartbeat_digest_enabled,
    and ingest_emails requires integrations.gmail.memory_policy='remember'."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _sync_tool(conn, "email_list", requires_approval=False)
            await _sync_tool(conn, "email_read", requires_approval=False)
            await _sync_tool(conn, "email_search", requires_approval=False)
            await _sync_tool(conn, "ingest_emails", requires_approval=False)

            await conn.execute("SELECT set_config('integrations.gmail.heartbeat_digest_enabled', 'false'::jsonb)")
            await conn.execute("SELECT set_config('integrations.gmail.memory_policy', '\"forget\"'::jsonb)")

            # Heartbeat inbox reads are denied when heartbeat_digest_enabled is false.
            hb_list = _j(await conn.fetchval(
                "SELECT evaluate_tool_call('email_list', '{}'::jsonb, '{\"tool_context\": \"heartbeat\", \"energy_available\": 20}'::jsonb)"
            ))
            hb_search = _j(await conn.fetchval(
                "SELECT evaluate_tool_call('email_search', '{\"query\": \"invoice\"}'::jsonb, '{\"tool_context\": \"heartbeat\", \"energy_available\": 20}'::jsonb)"
            ))
            assert hb_list["allowed"] is False
            assert hb_list["error_type"] == "context_denied"
            assert "heartbeat_digest_enabled" in hb_list["reason"]
            assert hb_search["allowed"] is False

            # Live chat reads remain allowed even when heartbeat_digest_enabled is false.
            chat_list = _j(await conn.fetchval(
                "SELECT evaluate_tool_call('email_list', '{}'::jsonb, '{\"tool_context\": \"chat\"}'::jsonb)"
            ))
            assert chat_list["allowed"] is True

            # ingest_emails is denied whenever memory_policy <> 'remember' (even in chat).
            chat_ingest_forget = _j(await conn.fetchval(
                "SELECT evaluate_tool_call('ingest_emails', '{}'::jsonb, '{\"tool_context\": \"chat\"}'::jsonb)"
            ))
            assert chat_ingest_forget["allowed"] is False
            assert "memory_policy" in chat_ingest_forget["reason"]

            # Enabling both allows heartbeat ingest_emails.
            await conn.execute("SELECT set_config('integrations.gmail.heartbeat_digest_enabled', 'true'::jsonb)")
            await conn.execute("SELECT set_config('integrations.gmail.memory_policy', '\"remember\"'::jsonb)")
            hb_ingest_ok = _j(await conn.fetchval(
                "SELECT evaluate_tool_call('ingest_emails', '{}'::jsonb, '{\"tool_context\": \"heartbeat\", \"energy_available\": 20}'::jsonb)"
            ))
            assert hb_ingest_ok["allowed"] is True
        finally:
            await tr.rollback()


async def test_gmail_draft_policy_gating_and_audit_via_migration_0266(db_pool):
    """#100: migration 0266 adds the gmail/draft capability & tool map so
    autonomous gmail_draft requires a matching standing policy and logs audit."""
    from pathlib import Path

    migration_sql = (
        Path(__file__).resolve().parents[2]
        / "db"
        / "migrations"
        / "0266_gmail_draft_and_policy_controls.sql"
    ).read_text(encoding="utf-8")

    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(migration_sql)
            await _sync_tool(conn, "gmail_draft", requires_approval=True)
            await conn.execute("SELECT grant_tool_approval('gmail_draft')")

            manifest = _j(await conn.fetchval(
                "SELECT capability_manifest FROM integration_connectors WHERE id = 'gmail'"
            ))
            assert "draft" in manifest
            assert "https://www.googleapis.com/auth/gmail.compose" in manifest["draft"]["scopes"]

            # Denied in heartbeat without a standing gmail/draft policy.
            denied = _j(await conn.fetchval(
                "SELECT evaluate_tool_call('gmail_draft', $1::jsonb, $2::jsonb)",
                json.dumps({"to": "recruiter@example.com", "subject": "Re: Role", "body": "No thanks"}),
                json.dumps({"tool_context": "heartbeat", "energy_available": 20}),
            ))
            assert denied["allowed"] is False
            assert denied["connector_action"]["connector_id"] == "gmail"
            assert denied["connector_action"]["action_kind"] == "draft"

            # Grant a narrow standing policy for drafts to recruiter@example.com.
            policy = _j(await conn.fetchval(
                """
                SELECT grant_connector_action_policy(
                    'gmail', 'draft', NULL,
                    '{"allowed_targets": ["recruiter@example.com"], "max_per_day": 5}'::jsonb,
                    TRUE, FALSE, ARRAY['heartbeat']::text[], NULL,
                    'test-session', 'Allow drafting polite declines to recruiter@example.com', 'user'
                )
                """
            ))
            allowed = _j(await conn.fetchval(
                "SELECT evaluate_tool_call('gmail_draft', $1::jsonb, $2::jsonb)",
                json.dumps({"to": "recruiter@example.com", "subject": "Re: Role", "body": "No thanks"}),
                json.dumps({"tool_context": "heartbeat", "energy_available": 20}),
            ))
            wrong_target = _j(await conn.fetchval(
                "SELECT evaluate_tool_call('gmail_draft', $1::jsonb, $2::jsonb)",
                json.dumps({"to": "ceo@example.com", "subject": "Hi", "body": "Hey"}),
                json.dumps({"tool_context": "heartbeat", "energy_available": 20}),
            ))
            sneaky_cc = _j(await conn.fetchval(
                "SELECT evaluate_tool_call('gmail_draft', $1::jsonb, $2::jsonb)",
                json.dumps({
                    "to": "recruiter@example.com",
                    "cc": "outside@evil.com",
                    "subject": "Re: Role",
                    "body": "No thanks",
                }),
                json.dumps({"tool_context": "heartbeat", "energy_available": 20}),
            ))
            assert allowed["allowed"] is True
            assert allowed["connector_action"]["policy_id"] == policy["policy_id"]
            assert wrong_target["allowed"] is False
            assert sneaky_cc["allowed"] is False
        finally:
            await tr.rollback()


async def test_integration_status_payload_includes_action_policies_and_audit(db_pool):
    """#100: /api/integrations/status SQL queries for action_policies and
    connector_action_audit must match the live schema cleanly."""
    from apps.hexis_api import _integration_status_payload

    payload = await _integration_status_payload(db_pool, None)
    assert isinstance(payload.get("action_policies"), list)
    assert isinstance(payload.get("action_audit"), list)


async def test_connector_action_policy_can_be_revoked(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            policy = _j(await conn.fetchval(
                """
                SELECT grant_connector_action_policy(
                    'telegram', 'send', NULL, '{"allowed_targets": ["123"]}'::jsonb,
                    TRUE, FALSE, ARRAY['heartbeat']::text[]
                )
                """
            ))
            listed = _j(await conn.fetchval("SELECT list_connector_action_policies('telegram')"))
            revoked = _j(await conn.fetchval(
                "SELECT revoke_connector_action_policy($1::uuid, 'test revoke')",
                policy["policy_id"],
            ))
            active_after = _j(await conn.fetchval("SELECT list_connector_action_policies('telegram')"))
        finally:
            await tr.rollback()

    assert listed[0]["policy_id"] == policy["policy_id"]
    assert revoked["status"] == "revoked"
    assert active_after == []


async def test_signal_send_is_connector_policy_managed(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _sync_tool(conn, "signal_send")
            await conn.execute("SELECT grant_tool_approval('signal_send')")
            denied = _j(await conn.fetchval(
                "SELECT evaluate_tool_call('signal_send', $1::jsonb, $2::jsonb)",
                json.dumps({"recipient": "+15551234567", "message": "Emergency update"}),
                json.dumps({"tool_context": "heartbeat", "energy_available": 20}),
            ))
            policy = _j(await conn.fetchval(
                """
                SELECT grant_connector_action_policy(
                    'signal', 'send', NULL, '{"allowed_targets": ["+15551234567"]}'::jsonb,
                    TRUE, FALSE, ARRAY['heartbeat']::text[],
                    NULL, 'test-session', 'Allow emergency Signal alerts', 'user'
                )
                """
            ))
            allowed = _j(await conn.fetchval(
                "SELECT evaluate_tool_call('signal_send', $1::jsonb, $2::jsonb)",
                json.dumps({"recipient": "+15551234567", "message": "Emergency update"}),
                json.dumps({"tool_context": "heartbeat", "energy_available": 20}),
            ))
        finally:
            await tr.rollback()

    assert denied["allowed"] is False
    assert denied["connector_action"]["connector_id"] == "signal"
    assert denied["connector_action"]["action_kind"] == "send"
    assert allowed["allowed"] is True
    assert allowed["connector_action"]["policy_id"] == policy["policy_id"]
