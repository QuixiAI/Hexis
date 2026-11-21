from __future__ import annotations

import base64
import json

import pytest

from core.auth.google_gmail import GMAIL_DEFAULT_CREDENTIAL_REF
from core.auth.store import save_auth
from core.auth.utils import now_ms
from services import gmail_backfill
from tests.utils import get_test_identifier

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


def _j(value):
    return json.loads(value) if isinstance(value, str) else value


def _b64(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _message(message_id: str, *, subject: str, body: str, internal_ms: str = "1710000000000"):
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "historyId": f"history-{message_id}",
        "internalDate": internal_ms,
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": f"Snippet for {subject}",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": "Alice <alice@example.com>"},
                {"name": "To", "value": "Eric <eric@example.com>"},
                {"name": "Date", "value": "Sat, 9 Mar 2024 12:00:00 +0000"},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64(body)}},
                {
                    "filename": "note.txt",
                    "mimeType": "text/plain",
                    "body": {"attachmentId": f"att-{message_id}", "size": 42},
                },
            ],
        },
    }


async def _connected_gmail(pool, marker: str, account: str) -> None:
    async with pool.acquire() as conn:
        attempt = _j(await conn.fetchval(
            """
            SELECT start_connection_attempt(
                'gmail',
                '["read", "search"]'::jsonb,
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
                '["read", "search"]'::jsonb,
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


async def test_gmail_message_parser_preserves_full_body_and_metadata():
    source = gmail_backfill.gmail_message_to_source_item(
        _message(
            "msg-parser",
            subject="Parser contract",
            body="The complete searchable clause lives here.",
        )
    )

    assert source["provider_item_id"] == "msg-parser"
    assert source["title"] == "Parser contract"
    assert "The complete searchable clause lives here." in source["content"]
    assert source["labels"] == ["INBOX", "UNREAD"]
    assert source["participants"][0]["role"] == "from"
    assert source["attachments"][0]["filename"] == "note.txt"
    assert source["metadata"]["gmail_history_id"] == "history-msg-parser"


async def test_run_gmail_backfill_step_stores_source_documents_and_ingestion_jobs(
    db_pool, monkeypatch, tmp_path
):
    import core.auth.store as auth_store

    monkeypatch.setattr(auth_store, "AUTH_DIR", tmp_path / "auth")
    marker = get_test_identifier("gmail-backfill")
    account = f"eric-{marker}@example.com"
    save_auth(
        GMAIL_DEFAULT_CREDENTIAL_REF,
        {
            "type": "authorized_user",
            "token": "access-token",
            "refresh_token": "refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client",
            "client_secret": "secret",
            "expires_ms": now_ms() + 3_600_000,
            "account_email": account,
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        },
    )
    await _connected_gmail(db_pool, marker, account)

    async with db_pool.acquire() as conn:
        job = _j(await conn.fetchval(
            """
            SELECT enqueue_connector_backfill_job(
                'gmail',
                $1,
                'messages',
                '{"label_ids": ["INBOX"], "max_messages": 2, "page_size": 2}'::jsonb,
                '{"test": true}'::jsonb
            )
            """,
            account,
        ))

    async def fake_gmail_get(credentials, path, *, params=None):
        if path == "/users/me/messages":
            assert params["labelIds"] == ["INBOX"]
            assert params["maxResults"] == 2
            return {"messages": [{"id": "msg-1"}, {"id": "msg-2"}], "nextPageToken": "next-page"}
        if path == "/users/me/messages/msg-1":
            return _message("msg-1", subject="First", body="First full email body.")
        if path == "/users/me/messages/msg-2":
            return _message("msg-2", subject="Second", body="Second full email body.")
        raise AssertionError(f"Unexpected Gmail path: {path}")

    monkeypatch.setattr(gmail_backfill, "_gmail_get", fake_gmail_get)
    handled = await gmail_backfill.run_gmail_backfill_step(db_pool, limit=1)

    async with db_pool.acquire() as conn:
        status = _j(await conn.fetchval("SELECT get_connector_backfill_status('gmail', $1)", account))
        items = await conn.fetch(
            """
            SELECT provider_item_id, source_document_id, ingestion_job_id, raw_metadata
            FROM connector_source_items
            WHERE account_key = $1
            ORDER BY provider_item_id
            """,
            account,
        )
        opened = _j(await conn.fetchval(
            "SELECT open_source_document($1::uuid)",
            items[0]["source_document_id"],
        ))

    assert handled == 1
    jobs_by_id = {j["job_id"]: j for j in status["jobs"]}
    original = jobs_by_id[job["job_id"]]
    assert original["status"] == "completed"
    assert original["result"]["items_stored"] == 2
    assert original["result"]["truncated"] is True
    assert status["cursors"][0]["cursor_value"]["page_token"] == "next-page"
    # #110: a truncated completion now self-continues -- a pending successor
    # job is queued so the next maintenance tick picks up at "next-page"
    # instead of a capped backfill silently completing forever.
    successors = [j for jid, j in jobs_by_id.items() if jid != job["job_id"]]
    assert len(successors) == 1
    assert successors[0]["status"] == "pending"
    assert [row["provider_item_id"] for row in items] == ["msg-1", "msg-2"]
    assert items[0]["ingestion_job_id"] is not None
    assert _j(items[0]["raw_metadata"])["gmail_history_id"] == "history-msg-1"
    assert "First full email body." in opened["content"]
    assert opened["source_attribution"]["connector_id"] == "gmail"


async def test_a_token_without_mail_access_fails_at_once_and_says_why(db_pool, monkeypatch, tmp_path):
    """Google can sign someone in without the "read your email" permission;
    the import then fails now with that reason instead of retrying in silence."""
    import core.auth.store as auth_store

    monkeypatch.setattr(auth_store, "AUTH_DIR", tmp_path / "auth")
    marker = get_test_identifier("gmail-noscope")
    account = f"eric-{marker}@example.com"
    save_auth(
        GMAIL_DEFAULT_CREDENTIAL_REF,
        {
            "type": "authorized_user",
            "token": "access-token",
            "refresh_token": "refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client",
            "client_secret": "secret",
            "expires_ms": now_ms() + 3_600_000,
            "account_email": account,
            "scopes": ["https://www.googleapis.com/auth/userinfo.email", "openid"],
        },
    )
    await _connected_gmail(db_pool, marker, account)
    async with db_pool.acquire() as conn:
        job = _j(await conn.fetchval(
            "SELECT enqueue_connector_backfill_job('gmail', $1, 'messages', '{}'::jsonb, '{\"test\": true}'::jsonb)",
            account,
        ))

    async def no_gmail_calls(*args, **kwargs):
        raise AssertionError("Gmail should not be called without mail access")

    monkeypatch.setattr(gmail_backfill, "_gmail_get", no_gmail_calls)
    # Every queued job for the account is tried; each fails at the scope check.
    await gmail_backfill.run_gmail_backfill_step(db_pool, limit=5)

    async with db_pool.acquire() as conn:
        status = _j(await conn.fetchval("SELECT get_connector_backfill_status('gmail', $1)", account))
        connection = await conn.fetchval(
            "SELECT status FROM integration_connections WHERE connector_id = 'gmail' AND account_key = $1", account)
    failed = next(j for j in status["jobs"] if j["job_id"] == job["job_id"])
    assert failed["status"] == "failed", failed
    assert "permission to read this mailbox" in failed["error"]
    assert connection == "needs_reauth"  # so the Gmail page offers sign-in again


async def test_only_mail_worth_reading_is_kept():
    """Gmail's own sorting decides; spam, promotions and automated mail are not read."""
    def labelled(*labels, headers=()):
        message = _message("m", subject="s", body="b")
        message["labelIds"] = list(labels)
        message["payload"]["headers"] += [{"name": n, "value": v} for n, v in headers]
        return message

    worth = gmail_backfill.worth_reading
    assert worth(labelled("INBOX", "CATEGORY_PERSONAL"))
    assert worth(labelled("INBOX", "CATEGORY_UPDATES", "IMPORTANT"))
    assert worth(labelled("SENT"))
    assert worth(labelled("INBOX", "CATEGORY_PROMOTIONS", "STARRED"))
    assert not worth(labelled("SPAM"))
    assert not worth(labelled("INBOX", "CATEGORY_PROMOTIONS", "IMPORTANT"))
    assert not worth(labelled("INBOX", "CATEGORY_SOCIAL"))
    assert not worth(labelled("INBOX", "CATEGORY_UPDATES"))
    assert not worth(labelled("INBOX", headers=[("List-Unsubscribe", "<mailto:x@example.com>")]))
    assert worth(labelled("INBOX", "IMPORTANT", headers=[("List-Id", "team.example.com")]))


async def test_email_text_is_cleaned_of_markup_padding_and_tracking_links():
    """HTML in the plain-text part, preheader padding, entities and tracking links are removed."""
    raw = (
        '<!DOCTYPE html><html><head><style>body { margin: 0; }</style></head><body>'
        '<!--[if mso]><xml><o:PixelsPerInch>96</o:PixelsPerInch></xml><![endif]-->'
        'MRVL a bullish opportunity.&#847; &zwnj;͏‌  ͏'
        '<p>Hi Eric,</p><p>Your statement   is ready.</p>'
        '<a href="https://click.e.example.com/?qs=abcdefabcdefabcdefabcdefabcdefabcdefabcdef1234">View</a>'
        '<br>Sam&#39;s Club</body></html>'
    )
    text = gmail_backfill.clean_email_text(raw)
    assert "margin" not in text and "PixelsPerInch" not in text and "<" not in text
    assert "͏" not in text and "&#" not in text and "&zwnj;" not in text
    assert "Your statement is ready." in text and "Sam's Club" in text
    assert "click.e.example.com" not in text  # an HTML link keeps only its text
    assert "Hi Eric,\n" in text  # paragraphs survive

    plain = "Track it: https://click.e.example.com/?qs=abcdefabcdefabcdefabcdefabcdefabcdefabcdef1234 today"
    assert gmail_backfill.clean_email_text(plain) == "Track it: [link: click.e.example.com] today"
    assert gmail_backfill.clean_email_text("See https://github.com/a/b") == "See https://github.com/a/b"

    assert gmail_backfill.clean_email_text("Thanks!\n\n> On Monday you wrote:\n> old text") == "Thanks!"


async def test_backfill_preserves_highest_numeric_history_id(db_pool, monkeypatch, tmp_path):
    """#110: Gmail /users/me/messages returns newest-first; paging through older
    messages must keep the highest numeric historyId instead of walking it backwards."""
    import core.auth.store as auth_store

    monkeypatch.setattr(auth_store, "AUTH_DIR", tmp_path / "auth")
    marker = get_test_identifier("gmail-max-hist")
    account = f"eric-{marker}@example.com"
    save_auth(
        GMAIL_DEFAULT_CREDENTIAL_REF,
        {
            "type": "authorized_user",
            "token": "access-token",
            "refresh_token": "refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client",
            "client_secret": "secret",
            "expires_ms": now_ms() + 3_600_000,
            "account_email": account,
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        },
    )
    await _connected_gmail(db_pool, marker, account)
    async with db_pool.acquire() as conn:
        await conn.fetchval(
            "SELECT enqueue_connector_backfill_job('gmail', $1, 'messages', '{\"max_messages\": 2}'::jsonb)",
            account,
        )

    newest = _message("msg-new", subject="Newest", body="Newest body", internal_ms="1720000000000")
    newest["historyId"] = "5000"
    older = _message("msg-old", subject="Older", body="Older body", internal_ms="1710000000000")
    older["historyId"] = "4000"

    async def fake_gmail_get(credentials, path, *, params=None):
        if path == "/users/me/messages":
            return {"messages": [{"id": "msg-new"}, {"id": "msg-old"}]}
        if path == "/users/me/messages/msg-new":
            return newest
        if path == "/users/me/messages/msg-old":
            return older
        raise AssertionError(f"Unexpected path: {path}")

    monkeypatch.setattr(gmail_backfill, "_gmail_get", fake_gmail_get)
    await gmail_backfill.run_gmail_backfill_step(db_pool, limit=5)

    async with db_pool.acquire() as conn:
        status = _j(await conn.fetchval("SELECT get_connector_backfill_status('gmail', $1)", account))
    assert status["cursors"][0]["cursor_value"]["history_id"] == "5000"


async def test_run_gmail_incremental_sync_seeds_baseline_and_syncs_history_list(
    db_pool, monkeypatch, tmp_path
):
    """#110: incremental sync seeds history_id from /users/me/profile when absent,
    then consumes /users/me/history (messageAdded), filters non-worth-reading mail,
    skips deleted messages (404), and advances the cursor."""
    import core.auth.store as auth_store

    monkeypatch.setattr(auth_store, "AUTH_DIR", tmp_path / "auth")
    marker = get_test_identifier("gmail-inc-sync")
    account = f"eric-{marker}@example.com"
    save_auth(
        GMAIL_DEFAULT_CREDENTIAL_REF,
        {
            "type": "authorized_user",
            "token": "access-token",
            "refresh_token": "refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client",
            "client_secret": "secret",
            "expires_ms": now_ms() + 3_600_000,
            "account_email": account,
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        },
    )

    async with db_pool.acquire() as conn:
        prev_policy = await conn.fetchval("SELECT value FROM config WHERE key = 'integrations.gmail.memory_policy'")
        await conn.execute(
            "UPDATE integration_connections SET status = 'revoked' WHERE connector_id = 'gmail'"
        )
        await conn.execute("SELECT set_config('integrations.gmail.memory_policy', '\"remember\"'::jsonb)")
    await _connected_gmail(db_pool, marker, account)

    try:
        calls: list[tuple[str, dict | None]] = []

        async def seed_gmail_get(credentials, path, *, params=None):
            calls.append((path, params))
            if path == gmail_backfill.GOOGLE_GMAIL_PROFILE_URL:
                return {"emailAddress": account, "historyId": "9000"}
            raise AssertionError(f"Unexpected seed path: {path}")

        monkeypatch.setattr(gmail_backfill, "_gmail_get", seed_gmail_get)
        seed_result = await gmail_backfill.run_gmail_incremental_sync_step(db_pool, limit=5)
        assert seed_result["seeded"] == 1
        assert seed_result["synced_accounts"] == 1

        # Make the cursor due again so the second pass runs history.list.
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE connector_sync_cursors
                SET last_started_at = CURRENT_TIMESTAMP - INTERVAL '1 hour',
                    last_completed_at = CURRENT_TIMESTAMP - INTERVAL '1 hour'
                WHERE account_key = $1
                """,
                account,
            )

        good_msg = _message("msg-live-1", subject="Important note", body="Live incoming email.", internal_ms="1725000000000")
        good_msg["historyId"] = "9010"
        promo_msg = _message("msg-promo", subject="Big Sale", body="50% off!", internal_ms="1725000001000")
        promo_msg["labelIds"] = ["INBOX", "CATEGORY_PROMOTIONS"]
        promo_msg["historyId"] = "9011"

        async def history_gmail_get(credentials, path, *, params=None):
            calls.append((path, params))
            if path == "/users/me/history":
                assert params["startHistoryId"] == "9000"
                assert params["historyTypes"] == "messageAdded"
                return {
                    "historyId": "9020",
                    "history": [
                        {
                            "id": "9010",
                            "messagesAdded": [
                                {"message": {"id": "msg-live-1"}},
                                {"message": {"id": "msg-deleted"}},
                            ],
                        },
                        {
                            "id": "9011",
                            "messagesAdded": [
                                {"message": {"id": "msg-promo"}},
                                {"message": {"id": "msg-live-1"}},  # duplicate ID
                            ],
                        },
                    ],
                }
            if path == "/users/me/messages/msg-live-1":
                return good_msg
            if path == "/users/me/messages/msg-promo":
                return promo_msg
            if path == "/users/me/messages/msg-deleted":
                raise gmail_backfill.GmailNotFoundError("Message not found")
            raise AssertionError(f"Unexpected history path: {path}")

        monkeypatch.setattr(gmail_backfill, "_gmail_get", history_gmail_get)
        sync_result = await gmail_backfill.run_gmail_incremental_sync_step(db_pool, limit=5)

        assert sync_result["synced_accounts"] == 1
        assert sync_result["items_stored"] == 1
        assert sync_result["items_skipped"] == 1
        assert [p for p, _ in calls if p in {gmail_backfill.GOOGLE_GMAIL_PROFILE_URL, "/users/me/history"}] == [
            gmail_backfill.GOOGLE_GMAIL_PROFILE_URL,
            "/users/me/history",
        ]

        async with db_pool.acquire() as conn:
            status = _j(await conn.fetchval("SELECT get_connector_backfill_status('gmail', $1)", account))
            items = await conn.fetch(
                "SELECT provider_item_id, raw_metadata FROM connector_source_items WHERE account_key = $1",
                account,
            )
        assert status["cursors"][0]["cursor_value"]["history_id"] == "9020"
        assert [row["provider_item_id"] for row in items] == ["msg-live-1"]
        assert _j(items[0]["raw_metadata"])["acquisition"] == "incremental_sync"
    finally:
        async with db_pool.acquire() as conn:
            if prev_policy is None:
                await conn.execute("DELETE FROM config WHERE key = 'integrations.gmail.memory_policy'")
            else:
                await conn.execute("SELECT set_config('integrations.gmail.memory_policy', $1::jsonb)", prev_policy)
            await conn.execute("DELETE FROM integration_connections WHERE account_key = $1", account)
            await conn.execute("DELETE FROM connection_attempts WHERE source_session_id = $1", marker)


async def test_run_gmail_incremental_sync_recovers_from_expired_history_id_404(
    db_pool, monkeypatch, tmp_path
):
    """#110: if startHistoryId expired (Gmail 404), incremental sync refreshes
    the baseline from /users/me/profile and performs a bounded catch-up tagged
    as import so it doesn't flood live alerts."""
    import core.auth.store as auth_store

    monkeypatch.setattr(auth_store, "AUTH_DIR", tmp_path / "auth")
    marker = get_test_identifier("gmail-hist-404")
    account = f"eric-{marker}@example.com"
    save_auth(
        GMAIL_DEFAULT_CREDENTIAL_REF,
        {
            "type": "authorized_user",
            "token": "access-token",
            "refresh_token": "refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client",
            "client_secret": "secret",
            "expires_ms": now_ms() + 3_600_000,
            "account_email": account,
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        },
    )

    async with db_pool.acquire() as conn:
        prev_policy = await conn.fetchval("SELECT value FROM config WHERE key = 'integrations.gmail.memory_policy'")
        await conn.execute(
            "UPDATE integration_connections SET status = 'revoked' WHERE connector_id = 'gmail'"
        )
        await conn.execute("SELECT set_config('integrations.gmail.memory_policy', '\"remember\"'::jsonb)")
    await _connected_gmail(db_pool, marker, account)
    try:
        async with db_pool.acquire() as conn:
            await conn.fetchval(
                "SELECT advance_connector_cursor('gmail', $1, 'messages', '{\"history_id\": \"100\"}'::jsonb, CURRENT_TIMESTAMP - INTERVAL '10 days')",
                account,
            )
            await conn.execute(
                """
                UPDATE connector_sync_cursors
                SET last_started_at = CURRENT_TIMESTAMP - INTERVAL '1 hour',
                    last_completed_at = CURRENT_TIMESTAMP - INTERVAL '1 hour'
                WHERE account_key = $1
                """,
                account,
            )

        catchup_msg = _message("msg-catchup", subject="Catchup", body="Recent mail during outage.", internal_ms="1726000000000")
        catchup_msg["historyId"] = "9950"

        async def fake_gmail_get(credentials, path, *, params=None):
            if path == "/users/me/history":
                raise gmail_backfill.GmailNotFoundError("404 historyId too old")
            if path == gmail_backfill.GOOGLE_GMAIL_PROFILE_URL:
                return {"emailAddress": account, "historyId": "9999"}
            if path == "/users/me/messages":
                assert "after:" in (params or {}).get("q", "")
                return {"messages": [{"id": "msg-catchup"}]}
            if path == "/users/me/messages/msg-catchup":
                return catchup_msg
            raise AssertionError(f"Unexpected path: {path}")

        monkeypatch.setattr(gmail_backfill, "_gmail_get", fake_gmail_get)
        result = await gmail_backfill.run_gmail_incremental_sync_step(db_pool, limit=5)

        assert result["resynced"] == 1
        assert result["items_stored"] == 1
        async with db_pool.acquire() as conn:
            status = _j(await conn.fetchval("SELECT get_connector_backfill_status('gmail', $1)", account))
            item = await conn.fetchrow(
                "SELECT provider_item_id, raw_metadata FROM connector_source_items WHERE account_key = $1",
                account,
            )
        assert status["cursors"][0]["cursor_value"]["history_id"] == "9999"
        assert item["provider_item_id"] == "msg-catchup"
        assert _j(item["raw_metadata"])["acquisition"] == "import"
    finally:
        async with db_pool.acquire() as conn:
            if prev_policy is None:
                await conn.execute("DELETE FROM config WHERE key = 'integrations.gmail.memory_policy'")
            else:
                await conn.execute("SELECT set_config('integrations.gmail.memory_policy', $1::jsonb)", prev_policy)
            await conn.execute("DELETE FROM integration_connections WHERE account_key = $1", account)
            await conn.execute("DELETE FROM connection_attempts WHERE source_session_id = $1", marker)


async def test_missing_credentials_fails_loud_for_backfill_and_incremental_sync(
    db_pool, monkeypatch, tmp_path
):
    """#110 / Experience Bar #8: missing credentials must not silently return 0
    when Gmail backfill jobs or incremental sync cursors are actually due."""
    import core.auth.store as auth_store

    monkeypatch.setattr(auth_store, "AUTH_DIR", tmp_path / "empty-auth")
    marker = get_test_identifier("gmail-nocreds")
    account = f"eric-{marker}@example.com"

    async with db_pool.acquire() as conn:
        prev_policy = await conn.fetchval("SELECT value FROM config WHERE key = 'integrations.gmail.memory_policy'")
        await conn.execute(
            "UPDATE integration_connections SET status = 'revoked' WHERE connector_id = 'gmail'"
        )
        await conn.execute("SELECT set_config('integrations.gmail.memory_policy', '\"remember\"'::jsonb)")
    await _connected_gmail(db_pool, marker, account)

    try:
        # 1. Incremental sync due with missing credentials -> fails loud + marks needs_reauth.
        inc_result = await gmail_backfill.run_gmail_incremental_sync_step(db_pool, limit=5)
        assert inc_result["failed"] is True
        assert "credentials are missing" in inc_result["error"]

        async with db_pool.acquire() as conn:
            conn_status = await conn.fetchval(
                "SELECT status FROM integration_connections WHERE connector_id = 'gmail' AND account_key = $1",
                account,
            )
            assert conn_status == "needs_reauth"

            # 2. Re-mark connected and queue a backfill job -> run_gmail_backfill_step fails it loud.
            await conn.execute(
                "UPDATE integration_connections SET status = 'connected' WHERE connector_id = 'gmail' AND account_key = $1",
                account,
            )
            job = _j(await conn.fetchval(
                "SELECT enqueue_connector_backfill_job('gmail', $1, 'messages')",
                account,
            ))

        handled = await gmail_backfill.run_gmail_backfill_step(db_pool, limit=5)
        assert handled == 1

        async with db_pool.acquire() as conn:
            status = _j(await conn.fetchval("SELECT get_connector_backfill_status('gmail', $1)", account))
        failed_job = next(j for j in status["jobs"] if j["job_id"] == job["job_id"])
        assert failed_job["status"] == "failed"
        assert "credentials are missing" in failed_job["error"]
    finally:
        async with db_pool.acquire() as conn:
            if prev_policy is None:
                await conn.execute("DELETE FROM config WHERE key = 'integrations.gmail.memory_policy'")
            else:
                await conn.execute("SELECT set_config('integrations.gmail.memory_policy', $1::jsonb)", prev_policy)
            await conn.execute("DELETE FROM integration_connections WHERE account_key = $1", account)
            await conn.execute("DELETE FROM connection_attempts WHERE source_session_id = $1", marker)
