"""Google Drive: its own sign-in through the shared Google callback, and folder imports."""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest

from core.auth.google_gmail import (
    GMAIL_CLIENT_SECRET_REF,
    GMAIL_DEFAULT_CREDENTIAL_REF,
    complete_gmail_oauth,
    start_gmail_oauth,
)
from core.auth.store import load_auth, save_auth
from core.auth.utils import now_ms
from services import google_drive
from tests.utils import get_test_identifier

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture(autouse=True)
def isolated_auth(tmp_path, monkeypatch):
    import core.auth.store as auth_store

    monkeypatch.setattr(auth_store, "AUTH_DIR", tmp_path / "auth")
    for name in ("HEXIS_GMAIL_OAUTH_REDIRECT_URI", "GOOGLE_GMAIL_OAUTH_REDIRECT_URI", "HEXIS_API_URL", "HEXIS_API_BASE_URL"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("link,folder", [
    ("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMn?usp=sharing", "1AbCdEfGhIjKlMn"),
    ("https://drive.google.com/drive/u/0/folders/1AbCdEfGhIjKlMn", "1AbCdEfGhIjKlMn"),
    ("https://drive.google.com/open?id=1AbCdEfGhIjKlMn", "1AbCdEfGhIjKlMn"),
    (" 1AbCdEfGhIjKlMn ", "1AbCdEfGhIjKlMn"),
])
async def test_folder_links_in_the_forms_people_paste(link, folder):
    assert google_drive.folder_id_from(link) == folder


async def test_a_link_that_is_not_a_folder_is_refused():
    with pytest.raises(google_drive.DriveError, match="invalid_link"):
        google_drive.folder_id_from("https://example.com/not a folder")


async def test_drive_signs_in_separately_with_read_only_access(db_pool, monkeypatch):
    import core.auth.google_gmail as google_gmail

    save_auth(GMAIL_CLIENT_SECRET_REF, {"installed": {
        "client_id": "google-test-client", "client_secret": "google-test-secret",
        "redirect_uris": ["http://127.0.0.1"],
    }})
    marker = get_test_identifier("drive-signin")
    email = f"ana-{marker}@example.com"

    async def fake_exchange(**kwargs):
        return {"access_token": "drive-token", "refresh_token": "drive-refresh", "expires_in": 3600,
                "scope": "https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/drive.readonly"}

    async def fake_email(token):
        return email

    monkeypatch.setattr(google_gmail, "_exchange_code", fake_exchange)
    monkeypatch.setattr(google_gmail, "_fetch_account_email", fake_email)
    try:
        started = await start_gmail_oauth(db_pool, connector_id="google_drive", source_session_id=marker)
        query = parse_qs(urlparse(started.attempt_payload["authorization_url"]).query)
        assert query["scope"] == [
            "https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/drive.readonly"
        ]
        state = query["state"][0]
        # Google comes back to the one callback; the state says it was Drive.
        completed = await complete_gmail_oauth(
            db_pool, authorization_response=f"http://127.0.0.1/?code=c&state={state}",
        )
        assert completed.connector_id == "google_drive"
        assert load_auth(google_drive.CREDENTIAL_REF)["refresh_token"] == "drive-refresh"
        assert load_auth(GMAIL_DEFAULT_CREDENTIAL_REF) is None  # Gmail untouched
        assert (await google_drive.connection(db_pool))["account_key"] == email
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM integration_connections WHERE account_key = $1", email)
            await conn.execute("DELETE FROM connection_attempts WHERE source_session_id = $1", marker)


async def test_folders_are_walked_with_their_paths(monkeypatch):
    tree = {
        "root": [{"id": "sub", "name": "Policies", "mimeType": google_drive.FOLDER_MIME},
                 {"id": "a", "name": "plan.docx", "mimeType": "application/octet-stream"}],
        "sub": [{"id": "b", "name": "Leave", "mimeType": "application/vnd.google-apps.document"}],
    }

    async def fake_get(credentials, path, params=None):
        parent = params["q"].split("'")[1]
        return {"files": tree.get(parent, [])}

    monkeypatch.setattr(google_drive, "_drive_get", fake_get)
    files = [f async for f in google_drive.list_folder_files({}, "root")]
    assert [(f["id"], f["folder_path"]) for f in files] == [("a", []), ("b", ["Policies"])]


async def test_an_import_reads_new_files_and_skips_unchanged_and_unreadable_ones(db_pool, monkeypatch):
    marker = get_test_identifier("drive-import")
    email = f"ana-{marker}@example.com"
    save_auth(google_drive.CREDENTIAL_REF, {
        "token": "t", "refresh_token": "r", "client_id": "c", "client_secret": "s",
        "expires_ms": now_ms() + 3_600_000, "account_email": email,
    })
    files = [
        {"id": f"{marker}-old", "name": "old.pdf", "mimeType": "application/pdf", "size": "10", "modifiedTime": "2026-01-01T00:00:00Z"},
        {"id": f"{marker}-doc", "name": "Leave policy", "mimeType": "application/vnd.google-apps.document",
         "modifiedTime": "2026-02-01T00:00:00Z", "webViewLink": "https://docs.google.com/d", "folder_path": ["HR"]},
        {"id": f"{marker}-video", "name": "all-hands.mp4", "mimeType": "video/mp4", "size": "10", "modifiedTime": "2026-02-01T00:00:00Z"},
        {"id": f"{marker}-bad", "name": "broken.docx", "mimeType": "application/octet-stream", "size": "10", "modifiedTime": "2026-02-01T00:00:00Z"},
    ]

    async def fake_list(credentials, folder_id, **kwargs):
        for item in files:
            yield {"folder_path": [], **item}

    read = []

    async def fake_text(credentials, item):
        read.append(item["name"])
        if item["name"] == "broken.docx":
            raise ValueError("corrupt")
        return "Everyone gets twenty days of paid leave."

    monkeypatch.setattr(google_drive, "list_folder_files", fake_list)
    monkeypatch.setattr(google_drive, "file_text", fake_text)
    async with db_pool.acquire() as conn:
        attempt = await conn.fetchval(
            "SELECT start_connection_attempt('google_drive', '[\"read\"]'::jsonb, ARRAY[]::text[], '{}'::jsonb, NULL, NULL, 'web', $1)",
            marker,
        )
        attempt = json.loads(attempt) if isinstance(attempt, str) else attempt
        await conn.fetchval(
            "SELECT complete_connection_attempt($1::uuid, $2, $2, $3, ARRAY[]::text[], '[]'::jsonb, '{}'::jsonb)",
            attempt["attempt_id"], email, google_drive.CREDENTIAL_REF,
        )
    try:
        await google_drive._save_folders(db_pool, [{"id": "folder1", "name": "HR"}])
        queued = await google_drive.start_import(db_pool)
        async with db_pool.acquire() as conn:
            conn_id = await conn.fetchval(
                "SELECT connection_id::text FROM connector_backfill_jobs WHERE id = $1::uuid", queued["job_id"])
            await conn.execute(
                "UPDATE connector_sync_cursors SET cursor_value = $2::jsonb WHERE connection_id = $1::uuid AND cursor_key = 'folders'",
                conn_id, json.dumps({"seen": {f"{marker}-old": "2026-01-01T00:00:00Z"}}),
            )
            job = await conn.fetchval("SELECT to_jsonb(j) FROM connector_backfill_jobs j WHERE id = $1::uuid", queued["job_id"])
        await google_drive.process_drive_import_job(db_pool, job)

        assert read == ["Leave policy", "broken.docx"]
        state = await google_drive.status(db_pool)
        assert state["import"]["status"] == "completed"
        assert state["import"]["progress"] == {
            "folders": 1, "files_seen": 4, "files_read": 1, "unchanged": 1, "skipped": 1, "failed": 1,
        }
        async with db_pool.acquire() as conn:
            item = await conn.fetchrow(
                """
                SELECT d.title, i.raw_metadata AS metadata, i.ingestion_job_id
                FROM connector_source_items i JOIN source_documents d ON d.id = i.source_document_id
                WHERE i.connector_id = 'google_drive' AND i.provider_item_id = $1
                """,
                f"{marker}-doc",
            )
            cursor = await conn.fetchval(
                "SELECT cursor_value FROM connector_sync_cursors WHERE connection_id = $1::uuid AND cursor_key = 'folders'", conn_id)
        assert item["title"] == "Leave policy"
        metadata = json.loads(item["metadata"]) if isinstance(item["metadata"], str) else item["metadata"]
        assert metadata["drive_path"] == "HR/Leave policy"
        assert item["ingestion_job_id"] is not None  # queued to be remembered
        # The broken file is retried next time; the read one is not.
        cursor = json.loads(cursor) if isinstance(cursor, str) else cursor
        assert set(cursor["seen"]) == {f"{marker}-old", f"{marker}-doc"}
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM config WHERE key = $1", google_drive.FOLDERS_CONFIG_KEY)
            await conn.execute("DELETE FROM integration_connections WHERE account_key = $1", email)
            await conn.execute("DELETE FROM connection_attempts WHERE source_session_id = $1", marker)
