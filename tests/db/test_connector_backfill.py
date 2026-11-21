from __future__ import annotations

import json

import pytest

from tests.utils import get_test_identifier

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


def _j(value):
    return json.loads(value) if isinstance(value, str) else value


async def _connected_gmail(conn, marker: str) -> str:
    account = f"backfill-{marker}@example.com"
    attempt = _j(await conn.fetchval(
        """
        SELECT start_connection_attempt(
            'gmail',
            '["read", "search", "ingest"]'::jsonb,
            ARRAY[]::text[],
            '{}'::jsonb,
            NULL,
            NULL,
            'test',
            $1,
            CURRENT_TIMESTAMP + INTERVAL '10 minutes'
        )
        """,
        marker,
    ))
    await conn.fetchval(
        """
        SELECT complete_connection_attempt(
            $1::uuid,
            $2,
            $2,
            'integration.gmail.default',
            $3::text[],
            '[]'::jsonb,
            '{"test": true}'::jsonb
        )
        """,
        attempt["attempt_id"],
        account,
        [
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/gmail.readonly",
        ],
    )
    return account


async def test_connector_backfill_job_cursor_lifecycle(db_pool):
    marker = get_test_identifier("connector-backfill")

    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            account = await _connected_gmail(conn, marker)
            job = _j(await conn.fetchval(
                """
                SELECT enqueue_connector_backfill_job(
                    'gmail', $1, 'messages',
                    '{"from": "now-30d"}'::jsonb,
                    '{"reason": "test"}'::jsonb
                )
                """,
                account,
            ))
            duplicate = _j(await conn.fetchval(
                "SELECT enqueue_connector_backfill_job('gmail', $1, 'messages')",
                account,
            ))
            assert duplicate["existing"] is True
            assert duplicate["job_id"] == job["job_id"]

            claimed = _j(await conn.fetchval("SELECT claim_connector_backfill_jobs_for('gmail', 5)"))
            assert [item["id"] for item in claimed] == [job["job_id"]]

            progress = _j(await conn.fetchval(
                """
                SELECT update_connector_backfill_progress(
                    $1::uuid,
                    '{"pages": 1, "items_seen": 2}'::jsonb,
                    '{"page_token": "abc"}'::jsonb,
                    CURRENT_TIMESTAMP
                )
                """,
                job["job_id"],
            ))
            assert progress["status"] == "in_progress"
            assert progress["cancel_requested"] is False

            completed = _j(await conn.fetchval(
                """
                SELECT complete_connector_backfill_job(
                    $1::uuid,
                    '{"items_seen": 2}'::jsonb,
                    '{"page_token": "done"}'::jsonb,
                    CURRENT_TIMESTAMP
                )
                """,
                job["job_id"],
            ))
            status = _j(await conn.fetchval(
                "SELECT get_connector_backfill_status('gmail', $1)",
                account,
            ))
        finally:
            await tr.rollback()

    assert completed["status"] == "completed"
    assert status["cursors"][0]["cursor_value"] == {"page_token": "done"}
    assert status["jobs"][0]["status"] == "completed"


async def test_enqueue_gmail_backfill_does_not_create_synthetic_ambient_responsibility(db_pool):
    """#110: ongoing Gmail sync is handled directly by incremental history.list
    sync on connector_sync_cursors rather than a synthetic system ambient
    responsibility that spams outbox notifications."""
    marker = get_test_identifier("connector-ambient")

    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            account = await _connected_gmail(conn, marker)
            await conn.fetchval(
                "SELECT enqueue_connector_backfill_job('gmail', $1, 'messages')",
                account,
            )

            rows = await conn.fetch(
                """
                SELECT id FROM ambient_responsibilities
                WHERE created_by = 'system'
                  AND title = 'Ongoing Gmail sync'
                  AND sources @> jsonb_build_array(
                      jsonb_build_object('connector_id', 'gmail', 'account_key', $1::text)
                  )
                """,
                account.lower(),
            )
            assert rows == []
        finally:
            await tr.rollback()


async def test_claim_due_gmail_incremental_syncs_respects_memory_policy_and_backfill_state(db_pool):
    """#110: incremental Gmail sync respects integrations.gmail.memory_policy
    ('forget' never syncs, 'remember' auto-syncs connected accounts, 'ask' syncs
    opted-in cursors) and never races active or truncated backfills."""
    marker = get_test_identifier("gmail-inc-claim")

    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            # Isolate from any pre-existing gmail connections in the test DB.
            await conn.execute(
                "UPDATE integration_connections SET status = 'revoked' WHERE connector_id = 'gmail'"
            )
            account = await _connected_gmail(conn, marker)

            # 1. memory_policy='ask' without a cursor -> not claimed.
            await conn.execute("SELECT set_config('integrations.gmail.memory_policy', '\"ask\"'::jsonb)")
            assert _j(await conn.fetchval("SELECT claim_due_gmail_incremental_syncs(5)")) == []

            # 2. memory_policy='forget' even with a cursor -> never claimed.
            await conn.fetchval(
                "SELECT advance_connector_cursor('gmail', $1, 'messages', '{\"history_id\": \"100\"}'::jsonb, CURRENT_TIMESTAMP)",
                account,
            )
            await conn.execute(
                "UPDATE connector_sync_cursors SET last_completed_at = CURRENT_TIMESTAMP - INTERVAL '1 hour' WHERE account_key = $1",
                account,
            )
            await conn.execute("SELECT set_config('integrations.gmail.memory_policy', '\"forget\"'::jsonb)")
            assert _j(await conn.fetchval("SELECT claim_due_gmail_incremental_syncs(5)")) == []

            # 3. memory_policy='ask' with an opted-in completed cursor -> claimed once, then backs off.
            await conn.execute("SELECT set_config('integrations.gmail.memory_policy', '\"ask\"'::jsonb)")
            claimed = _j(await conn.fetchval("SELECT claim_due_gmail_incremental_syncs(5)"))
            assert len(claimed) == 1
            assert claimed[0]["account_key"] == account
            assert claimed[0]["cursor_value"]["history_id"] == "100"
            # Immediately calling again backs off because last_started_at was stamped.
            assert _j(await conn.fetchval("SELECT claim_due_gmail_incremental_syncs(5)")) == []

            # 4. Leftover page_token (unfinished paginated backfill) is not claimed for incremental sync.
            await conn.execute(
                """
                UPDATE connector_sync_cursors
                SET cursor_value = '{"history_id": "100", "page_token": "page-2"}'::jsonb,
                    last_started_at = CURRENT_TIMESTAMP - INTERVAL '1 hour',
                    last_completed_at = CURRENT_TIMESTAMP - INTERVAL '1 hour'
                WHERE account_key = $1
                """,
                account,
            )
            assert _j(await conn.fetchval("SELECT claim_due_gmail_incremental_syncs(5)")) == []

            # 5. memory_policy='remember' auto-claims a newly connected Gmail account with no cursor yet.
            account2 = await _connected_gmail(conn, f"{marker}-remember")
            await conn.execute("SELECT set_config('integrations.gmail.memory_policy', '\"remember\"'::jsonb)")
            claimed_new = _j(await conn.fetchval("SELECT claim_due_gmail_incremental_syncs(5)"))
            assert [c["account_key"] for c in claimed_new] == [account2]
        finally:
            await tr.rollback()


async def test_migration_0265_retires_synthetic_sync_and_recovers_stalled_cursor(db_pool):
    """#110: migration 0265 disables system 'Ongoing Gmail sync' rows (without
    touching user monitors), recovers pre-0246 stalled cursors with leftover
    page_token, and reconnect clears errored cursors."""
    from pathlib import Path

    migration_sql = (
        Path(__file__).resolve().parents[2]
        / "db"
        / "migrations"
        / "0265_gmail_incremental_sync.sql"
    ).read_text(encoding="utf-8")
    marker = get_test_identifier("mig-0265")

    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            account = await _connected_gmail(conn, marker)
            # Seed a synthetic system responsibility (as 0246 created) AND a user monitor.
            sys_resp = _j(await conn.fetchval(
                "SELECT create_ambient_responsibility($1::jsonb)",
                json.dumps({
                    "title": "Ongoing Gmail sync",
                    "user_intent": "Keep ingesting new Gmail messages",
                    "kind": "monitor",
                    "created_by": "system",
                    "sources": [{"connector_id": "gmail", "account_key": account}],
                }),
            ))
            user_resp = _j(await conn.fetchval(
                "SELECT create_ambient_responsibility($1::jsonb)",
                json.dumps({
                    "title": f"Watch Alice emails {marker}",
                    "user_intent": "Tell me when Alice emails",
                    "kind": "monitor",
                    "created_by": "user",
                    "sources": [{"connector_id": "gmail", "account_key": account, "from": "alice@example.com"}],
                }),
            ))

            # Seed a stalled cursor (leftover page_token, no pending/in_progress job).
            await conn.fetchval(
                """
                SELECT advance_connector_cursor(
                    'gmail', $1, 'messages',
                    '{"page_token": "stuck-page-2", "history_id": "200"}'::jsonb,
                    CURRENT_TIMESTAMP - INTERVAL '2 days'
                )
                """,
                account,
            )
            before_status = _j(await conn.fetchval("SELECT get_connector_backfill_status('gmail', $1)", account))
            assert before_status["cursors"][0]["stalled"] is True
            assert before_status["cursors"][0]["sync_age_seconds"] is not None

            # Run migration 0265.
            await conn.execute(migration_sql)

            sys_status = await conn.fetchval(
                "SELECT status FROM ambient_responsibilities WHERE id = $1::uuid",
                sys_resp["output"]["responsibility_id"],
            )
            user_status = await conn.fetchval(
                "SELECT status FROM ambient_responsibilities WHERE id = $1::uuid",
                user_resp["output"]["responsibility_id"],
            )
            after_status = _j(await conn.fetchval("SELECT get_connector_backfill_status('gmail', $1)", account))

            assert sys_status == "disabled"
            assert user_status == "active"
            assert after_status["cursors"][0]["stalled"] is False
            assert any(j["status"] == "pending" for j in after_status["jobs"])

            # Errored cursor recovers to active when Gmail reconnects.
            conn_id = after_status["cursors"][0]["connection_id"]
            await conn.fetchval(
                "SELECT record_gmail_incremental_sync_error($1::uuid, 'messages', 'token revoked', TRUE)",
                conn_id,
            )
            err_cursor = await conn.fetchrow(
                "SELECT status, last_error FROM connector_sync_cursors WHERE connection_id = $1::uuid",
                conn_id,
            )
            assert err_cursor["status"] == "error"
            assert err_cursor["last_error"] == "token revoked"

            # Reconnect via complete_connection_attempt.
            attempt = _j(await conn.fetchval(
                """
                SELECT start_connection_attempt(
                    'gmail', '["read", "search"]'::jsonb, ARRAY[]::text[], '{}'::jsonb,
                    NULL, NULL, 'test', $1, CURRENT_TIMESTAMP + INTERVAL '10 minutes'
                )
                """,
                f"{marker}-reconnect",
            ))
            await conn.fetchval(
                """
                SELECT complete_connection_attempt(
                    $1::uuid, $2, $2, 'integration.gmail.default',
                    ARRAY['https://www.googleapis.com/auth/gmail.readonly']::text[],
                    '["read", "search"]'::jsonb, '{"test": true}'::jsonb
                )
                """,
                attempt["attempt_id"],
                account,
            )
            recovered_cursor = await conn.fetchrow(
                "SELECT status, last_error, last_started_at FROM connector_sync_cursors WHERE connection_id = $1::uuid",
                conn_id,
            )
            assert recovered_cursor["status"] == "active"
            assert recovered_cursor["last_error"] is None
            assert recovered_cursor["last_started_at"] is None
        finally:
            await tr.rollback()


async def test_completed_truncated_job_reenqueues_successor(db_pool):
    """#110: a capped backfill that hit max_messages and still had a
    page_token left previously completed and just sat there forever.
    Completing with result.truncated=true must queue a successor so the
    next maintenance tick continues from the advanced cursor."""
    marker = get_test_identifier("connector-truncated")

    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            account = await _connected_gmail(conn, marker)
            job = _j(await conn.fetchval(
                "SELECT enqueue_connector_backfill_job('gmail', $1, 'messages')",
                account,
            ))
            await conn.fetchval("SELECT claim_connector_backfill_jobs_for('gmail', 5)")

            completed = _j(await conn.fetchval(
                """
                SELECT complete_connector_backfill_job(
                    $1::uuid,
                    '{"items_seen": 100, "truncated": true, "next_page_token": "page-2"}'::jsonb,
                    '{"page_token": "page-2"}'::jsonb,
                    CURRENT_TIMESTAMP
                )
                """,
                job["job_id"],
            ))
            assert completed["status"] == "completed"

            status = _j(await conn.fetchval(
                "SELECT get_connector_backfill_status('gmail', $1)", account
            ))
            statuses = {j["job_id"]: j["status"] for j in status["jobs"]}
            assert statuses[job["job_id"]] == "completed"
            successors = [
                jid for jid, st in statuses.items()
                if jid != job["job_id"] and st == "pending"
            ]
            assert len(successors) == 1, statuses
            assert status["cursors"][0]["cursor_value"] == {"page_token": "page-2"}
        finally:
            await tr.rollback()


async def test_completed_non_truncated_job_has_no_successor(db_pool):
    marker = get_test_identifier("connector-finished")

    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            account = await _connected_gmail(conn, marker)
            job = _j(await conn.fetchval(
                "SELECT enqueue_connector_backfill_job('gmail', $1, 'messages')",
                account,
            ))
            await conn.fetchval("SELECT claim_connector_backfill_jobs_for('gmail', 5)")
            await conn.fetchval(
                """
                SELECT complete_connector_backfill_job(
                    $1::uuid,
                    '{"items_seen": 3, "truncated": false}'::jsonb,
                    '{"page_token": null}'::jsonb,
                    CURRENT_TIMESTAMP
                )
                """,
                job["job_id"],
            )

            status = _j(await conn.fetchval(
                "SELECT get_connector_backfill_status('gmail', $1)", account
            ))
            assert len(status["jobs"]) == 1
        finally:
            await tr.rollback()


async def test_connector_backfill_pause_resume_and_cancel(db_pool):
    marker = get_test_identifier("connector-pause")

    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            account = await _connected_gmail(conn, marker)
            job = _j(await conn.fetchval(
                "SELECT enqueue_connector_backfill_job('gmail', $1, 'messages')",
                account,
            ))
            paused = _j(await conn.fetchval(
                "SELECT pause_connector_backfill_job($1::uuid, 'operator pause')",
                job["job_id"],
            ))
            claimed_while_paused = _j(await conn.fetchval("SELECT claim_connector_backfill_jobs(5)"))
            resumed = _j(await conn.fetchval(
                "SELECT resume_connector_backfill_job($1::uuid)",
                job["job_id"],
            ))
            cancelled = _j(await conn.fetchval(
                "SELECT cancel_connector_backfill_job($1::uuid, 'operator cancel')",
                job["job_id"],
            ))
        finally:
            await tr.rollback()

    assert paused["status"] == "paused"
    assert claimed_while_paused == []
    assert resumed["status"] == "pending"
    assert cancelled["status"] == "cancelled"


async def test_connector_source_item_preserves_raw_artifact_and_ingestion_receipt(db_pool):
    marker = get_test_identifier("connector-source")
    content = (
        f"Subject: Source receipt {marker}\n\n"
        "This email contains the heliotrope connector backfill clause."
    )

    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            account = await _connected_gmail(conn, marker)
            item = _j(await conn.fetchval(
                """
                SELECT upsert_connector_source_item(
                    'gmail',
                    $1,
                    $2,
                    'Source receipt',
                    $3,
                    'message',
                    'thread-1',
                    CURRENT_TIMESTAMP,
                    ARRAY['INBOX']::text[],
                    '[{"email": "eric@example.com", "role": "to"}]'::jsonb,
                    '[{"filename": "note.txt", "size": 12}]'::jsonb,
                    '{"gmail_history_id": "42"}'::jsonb,
                    'private',
                    TRUE
                )
                """,
                account,
                f"msg-{marker}",
                content,
            ))
            opened = _j(await conn.fetchval(
                "SELECT open_source_document($1::uuid)",
                item["document_id"],
            ))
            second = _j(await conn.fetchval(
                """
                SELECT upsert_connector_source_item(
                    'gmail', $1, $2, 'Source receipt', $3,
                    'message', 'thread-1', CURRENT_TIMESTAMP,
                    ARRAY['INBOX']::text[], '[]'::jsonb, '[]'::jsonb,
                    '{"seen_again": true}'::jsonb, 'private', TRUE
                )
                """,
                account,
                f"msg-{marker}",
                content,
            ))
            row = await conn.fetchrow(
                """
                SELECT provider_item_id, source_document_id, ingestion_job_id, sensitivity, raw_metadata
                FROM connector_source_items
                WHERE id = $1::uuid
                """,
                item["source_item_id"],
            )
            job = _j(await conn.fetchval(
                "SELECT get_ingestion_job($1::uuid)",
                item["ingestion_job_id"],
            ))
        finally:
            await tr.rollback()

    assert opened["content"] == content
    assert opened["source_attribution"]["connector_id"] == "gmail"
    assert opened["source_attribution"]["provider_item_id"] == f"msg-{marker}"
    assert opened["source_attribution"]["sensitivity"] == "private"
    assert second["source_item_id"] == item["source_item_id"]
    assert second["ingestion_job_id"] == item["ingestion_job_id"]
    assert row["provider_item_id"] == f"msg-{marker}"
    assert row["sensitivity"] == "private"
    assert _j(row["raw_metadata"])["seen_again"] is True
    assert job["payload"]["provider_item_id"] == f"msg-{marker}"
    assert job["payload"]["acquisition"] == "connector"
    assert "content" not in job
