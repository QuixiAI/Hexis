from __future__ import annotations

import json
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


def _j(value):
    return json.loads(value) if isinstance(value, str) else value


async def _stub_get_embedding(conn):
    await conn.execute(
        """
        CREATE OR REPLACE FUNCTION get_embedding(text_contents TEXT[])
        RETURNS vector[] AS $$
            SELECT COALESCE(
                array_agg((
                    ARRAY[1.0::float] ||
                    array_fill(0.0::float, ARRAY[embedding_dimension() - 1])
                )::vector),
                ARRAY[]::vector[]
            )
            FROM unnest(text_contents)
        $$ LANGUAGE sql;
        """
    )


async def test_portable_brain_core_paths_are_sql_only(db_pool):
    """A new host should be able to exercise core cognitive substrate by SQL.

    This deliberately avoids Python tool handlers, provider SDKs, and app chat
    state. It is not a full dump/restore test; it is the first portability
    contract for the restored DB surface.
    """
    marker = uuid4().hex
    session_id = str(uuid4())
    content_hash = f"portable-brain-{marker}"
    content = (
        f"Portable brain contract {marker}.\n"
        "The lighthouse clause must remain searchable and openable from SQL."
    )

    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _stub_get_embedding(conn)
            # Channel turns queue a reading job only when this is on (off by
            # default: live turns are remembered once, by the encoder).
            await conn.execute(
                "SELECT set_config('channel.source_artifacts_enqueue_inbound', 'true'::jsonb)"
            )

            await conn.execute(
                "SELECT set_config($1, $2::jsonb)",
                f"portable.test.{marker}",
                json.dumps({"mode": "db-owned"}),
            )
            assert _j(await conn.fetchval("SELECT get_config($1)", f"portable.test.{marker}")) == {
                "mode": "db-owned"
            }

            stored_doc = _j(await conn.fetchval(
                """
                SELECT upsert_source_document(
                    $1, 'document', $2, $3, '.md', $4, 11,
                    $5::jsonb, $6::jsonb
                )
                """,
                f"Portable Contract {marker}",
                content_hash,
                f"/portable/{marker}.md",
                content,
                json.dumps({"kind": "document", "ref": content_hash, "content_hash": content_hash}),
                json.dumps({"portable_contract": True}),
            ))
            rows = await conn.fetch(
                "SELECT * FROM search_source_documents($1, 5)",
                f"lighthouse {marker}",
            )
            opened = _j(await conn.fetchval(
                "SELECT open_source_document(NULL, $1)",
                content_hash,
            ))
            assert len(rows) == 1
            assert str(rows[0]["document_id"]) == stored_doc["document_id"]
            assert opened["content"] == content

            turn = _j(await conn.fetchval(
                """
                SELECT record_chat_turn_memory(
                    $1, $2, $3, NULL,
                    '{"importance": 0.96, "metadata": {"type": "conversation", "portable_contract": true}}'::jsonb
                )
                """,
                f"remember the lighthouse clause for {marker}",
                "noted",
                session_id,
            ))
            assert turn["raw"]["status"] == "stored"
            assert turn["direct_promoted"] is True
            assert turn["promoted_memory_id"] is not None
            source_identity = await conn.fetchval(
                "SELECT source_identity FROM subconscious_units WHERE id = $1::uuid",
                turn["raw_unit_id"],
            )
            assert source_identity.startswith(f"chat:{session_id}:0:")

            channel_content = f"Portable channel artifact {marker}: keep the fieldstone clause."
            channel_turn = _j(await conn.fetchval(
                "SELECT prepare_channel_turn($1::jsonb)",
                json.dumps({
                    "channel_type": "telegram",
                    "channel_id": f"portable-channel-{marker}",
                    "sender_id": f"portable-sender-{marker}",
                    "sender_name": "Portable Sender",
                    "content": channel_content,
                    "message_id": f"portable-message-{marker}",
                    "metadata": {"is_private": True, "portable_contract": True},
                }),
            ))
            channel_artifact = await conn.fetchrow(
                """
                SELECT csi.source_document_id::text AS source_document_id,
                       csi.ingestion_job_id::text AS ingestion_job_id,
                       csi.sensitivity,
                       d.content,
                       d.source_attribution
                FROM channel_source_items csi
                JOIN source_documents d ON d.id = csi.source_document_id
                WHERE csi.session_id = $1::uuid
                  AND csi.direction = 'inbound'
                """,
                channel_turn["session_id"],
            )
            opened_channel_artifact = _j(await conn.fetchval(
                "SELECT open_source_document($1::uuid)",
                channel_artifact["source_document_id"],
            ))
            channel_attr = _j(channel_artifact["source_attribution"])

            assert channel_turn["allowed"] is True
            assert channel_artifact["content"] == channel_content
            assert opened_channel_artifact["content"] == channel_content
            assert channel_artifact["ingestion_job_id"] is not None
            assert channel_artifact["sensitivity"] == "private"
            assert channel_attr["kind"] == "channel_message"
            assert channel_attr["platform_message_id"] == f"portable-message-{marker}"

            synced = _j(await conn.fetchval(
                "SELECT sync_tool_definitions($1::jsonb)",
                json.dumps([
                    {
                        "name": f"portable_send_{marker}",
                        "description": "Portable approval-gated tool",
                        "schema": {"type": "object", "properties": {"message": {"type": "string"}}},
                        "category": "messaging",
                        "energy_cost": 3,
                        "allowed_contexts": ["chat", "heartbeat"],
                        "requires_approval": True,
                        "supports_parallel": False,
                    }
                ]),
            ))
            chat_decision = _j(await conn.fetchval(
                "SELECT evaluate_tool_call($1, '{}'::jsonb, $2::jsonb)",
                f"portable_send_{marker}",
                json.dumps({"tool_context": "chat"}),
            ))
            heartbeat_denied = _j(await conn.fetchval(
                "SELECT evaluate_tool_call($1, '{}'::jsonb, $2::jsonb)",
                f"portable_send_{marker}",
                json.dumps({"tool_context": "heartbeat", "energy_available": 9}),
            ))
            await conn.execute("SELECT grant_tool_approval($1)", f"portable_send_{marker}")
            heartbeat_allowed = _j(await conn.fetchval(
                "SELECT evaluate_tool_call($1, '{}'::jsonb, $2::jsonb)",
                f"portable_send_{marker}",
                json.dumps({"tool_context": "heartbeat", "energy_available": 9}),
            ))
            assert synced["synced"] == 1
            assert chat_decision["allowed"] is True
            assert heartbeat_denied["error_type"] == "approval_required"
            assert heartbeat_allowed["allowed"] is True
            assert heartbeat_allowed["energy_cost"] == 3

            await conn.fetchval(
                "SELECT sync_tool_definitions($1::jsonb)",
                json.dumps([
                    {
                        "name": "slack_send",
                        "description": "Portable Slack send",
                        "schema": {"type": "object", "properties": {"channel": {"type": "string"}}},
                        "category": "messaging",
                        "energy_cost": 3,
                        "allowed_contexts": ["chat", "heartbeat"],
                        "requires_approval": True,
                        "supports_parallel": False,
                    }
                ]),
            )
            await conn.execute("SELECT grant_tool_approval('slack_send')")
            connector_action_denied = _j(await conn.fetchval(
                "SELECT evaluate_tool_call('slack_send', $1::jsonb, $2::jsonb)",
                json.dumps({"channel": "#portable", "message": "portable action"}),
                json.dumps({"tool_context": "heartbeat", "energy_available": 9}),
            ))
            action_policy = _j(await conn.fetchval(
                """
                SELECT grant_connector_action_policy(
                    'slack',
                    'send',
                    NULL,
                    '{"allowed_targets": ["#portable"]}'::jsonb,
                    TRUE,
                    FALSE,
                    ARRAY['heartbeat']::text[],
                    NULL,
                    $1,
                    'portable contract grant',
                    'user'
                )
                """,
                f"portable-session-{marker}",
            ))
            connector_action_allowed = _j(await conn.fetchval(
                "SELECT evaluate_tool_call('slack_send', $1::jsonb, $2::jsonb)",
                json.dumps({"channel": "#portable", "message": "portable action"}),
                json.dumps({"tool_context": "heartbeat", "energy_available": 9}),
            ))
            tool_execution_id = await conn.fetchval(
                "SELECT record_tool_execution($1::jsonb)",
                json.dumps({
                    "tool_name": "slack_send",
                    "arguments": {"channel": "#portable", "message": "portable action"},
                    "tool_context": "heartbeat",
                    "call_id": f"portable-action-{marker}",
                    "session_id": f"portable-session-{marker}",
                    "success": True,
                    "output": {"sent": True},
                    "energy_spent": 3,
                    "duration_seconds": 0.01,
                }),
            )
            action_audit = await conn.fetchrow(
                """
                SELECT policy_id::text, connector_id, action_kind, target, decision
                FROM connector_action_audit
                WHERE tool_execution_id = $1::uuid
                """,
                tool_execution_id,
            )

            assert connector_action_denied["allowed"] is False
            assert connector_action_denied["connector_action"]["connector_id"] == "slack"
            assert action_policy["allow_autonomous"] is True
            assert connector_action_allowed["allowed"] is True
            assert connector_action_allowed["connector_action"]["policy_id"] == action_policy["policy_id"]
            assert action_audit["policy_id"] == action_policy["policy_id"]
            assert action_audit["connector_id"] == "slack"
            assert action_audit["action_kind"] == "send"
            assert action_audit["target"] == "#portable"
            assert action_audit["decision"] == "allowed"

            connector_plan = _j(await conn.fetchval(
                "SELECT prepare_connection_attempt('gmail', $1::jsonb)",
                json.dumps(["spam", "respond"]),
            ))
            assert connector_plan["capabilities"] == ["spam_triage", "reply"]
            assert connector_plan["requested_scopes"] == [
                "https://www.googleapis.com/auth/userinfo.email",
                "https://www.googleapis.com/auth/gmail.modify",
                "https://www.googleapis.com/auth/gmail.send",
            ]
            assert connector_plan["scope_count"] == 3

            attempt = _j(await conn.fetchval(
                """
                SELECT start_connection_attempt(
                    'gmail',
                    '["read", "search"]'::jsonb,
                    $1::text[],
                    '{"portable_contract": true}'::jsonb,
                    'https://accounts.google.com/o/oauth2/v2/auth?portable=1',
                    'Open auth URL and paste redirect back.',
                    'cli',
                    $2,
                    CURRENT_TIMESTAMP + INTERVAL '10 minutes'
                )
                """,
                ["https://www.googleapis.com/auth/gmail.readonly"],
                f"portable-session-{marker}",
            ))
            completed = _j(await conn.fetchval(
                """
                SELECT complete_connection_attempt(
                    $1::uuid,
                    $2,
                    $2,
                    'integration.gmail.default',
                    $3::text[],
                    '["read", "search"]'::jsonb,
                    '{"auth_store": "filesystem", "portable_contract": true}'::jsonb
                )
                """,
                attempt["attempt_id"],
                f"portable-{marker}@example.com",
                ["https://www.googleapis.com/auth/gmail.readonly"],
            ))
            backfill = _j(await conn.fetchval(
                """
                SELECT enqueue_connector_backfill_job(
                    'gmail',
                    $1,
                    'messages',
                    '{"from": "portable-contract"}'::jsonb,
                    '{"portable_contract": true}'::jsonb
                )
                """,
                f"portable-{marker}@example.com",
            ))
            claimed_backfills = _j(await conn.fetchval(
                "SELECT claim_connector_backfill_jobs_for('gmail', 1)"
            ))
            completed_backfill = _j(await conn.fetchval(
                """
                SELECT complete_connector_backfill_job(
                    $1::uuid,
                    '{"items_seen": 1}'::jsonb,
                    '{"page_token": "portable-done"}'::jsonb,
                    CURRENT_TIMESTAMP
                )
                """,
                backfill["job_id"],
            ))
            raw_message = f"Portable connector raw artifact {marker}: keep the riverstone clause."
            source_item = _j(await conn.fetchval(
                """
                SELECT upsert_connector_source_item(
                    'gmail',
                    $1,
                    $2,
                    'Portable connector message',
                    $3,
                    'message',
                    'thread-portable',
                    CURRENT_TIMESTAMP,
                    ARRAY['INBOX']::text[],
                    '[{"email": "portable@example.com", "role": "from"}]'::jsonb,
                    '[]'::jsonb,
                    '{"portable_contract": true}'::jsonb,
                    'private',
                    TRUE
                )
                """,
                f"portable-{marker}@example.com",
                f"msg-{marker}",
                raw_message,
            ))
            opened_source_item = _j(await conn.fetchval(
                "SELECT open_source_document($1::uuid)",
                source_item["document_id"],
            ))
            revoked = _j(await conn.fetchval(
                "SELECT revoke_integration_connection('gmail', $1, 'portable contract cleanup')",
                f"portable-{marker}@example.com",
            ))
            status = _j(await conn.fetchval("SELECT integration_status('gmail')"))

            assert attempt["status"] == "pending_user"
            assert completed["status"] == "connected"
            assert completed["credential_ref"] == "integration.gmail.default"
            assert claimed_backfills[0]["id"] == backfill["job_id"]
            assert completed_backfill["status"] == "completed"
            assert source_item["provider_item_id"] == f"msg-{marker}"
            assert source_item["ingestion_job_id"] is not None
            assert opened_source_item["content"] == raw_message
            assert opened_source_item["source_attribution"]["connector_id"] == "gmail"
            assert opened_source_item["source_attribution"]["provider_item_id"] == f"msg-{marker}"
            assert revoked["revoked"] == 1
            assert any(item["id"] == "gmail" for item in status["connectors"])
        finally:
            await tr.rollback()
