"""Google Drive: the agent reads, and remembers, the folders it is given.

Sign-in goes through the shared Google sign-in (core.auth.google_gmail) with
read-only Drive access and its own token. The chosen folders live in config
(``integrations.google_drive.folders``). An import is a connector backfill job
the maintenance worker runs: it walks each folder, turns every file it can read into text,
and stores it as a connector source item, which queues it for memory. Files
that haven't changed since the last import are not downloaded again.

Nothing here writes to Drive.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from core.auth.google_gmail import (
    GmailOAuthError,
    credential_ref_for,
    load_default_credentials,
    refresh_default_credentials_if_needed,
)
from core.integration_reliability import (
    IntegrationHttpError,
    format_provider_error,
    request_bytes_response,
    request_json,
)

logger = logging.getLogger(__name__)

CONNECTOR_ID = "google_drive"
CREDENTIAL_REF = credential_ref_for(CONNECTOR_ID)
FOLDERS_CONFIG_KEY = "integrations.google_drive.folders"
CURSOR_KEY = "folders"
DRIVE_API = "https://www.googleapis.com/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"

# Google's own formats are exported; Sheets as .xlsx so every tab is read.
EXPORTS = {
    "application/vnd.google-apps.document": ("text/plain", ".txt"),
    "application/vnd.google-apps.presentation": ("text/plain", ".txt"),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
}
# Uploaded files are downloaded and read with the document readers. Media and
# code are left out: an import of a shared folder shouldn't transcribe video.
DOCUMENT_SUFFIXES = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".text", ".md", ".markdown",
    ".csv", ".tsv", ".rtf", ".epub", ".json",
}
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_FILES_PER_IMPORT = 5000
MAX_DEPTH = 20
PROGRESS_EVERY = 10

_FOLDER_ID = re.compile(r"^[A-Za-z0-9_-]{10,}$")


class DriveError(RuntimeError):
    """Something the user can act on: a bad link, no access, Drive API off."""


def folder_id_from(link: str) -> str:
    """A folder ID from a Drive folder link, an ``?id=`` link, or the bare ID."""
    text = (link or "").strip()
    match = re.search(r"/folders/([A-Za-z0-9_-]+)", text) or re.search(r"[?&]id=([A-Za-z0-9_-]+)", text)
    candidate = match.group(1) if match else text
    if not _FOLDER_ID.match(candidate):
        raise DriveError("invalid_link")
    return candidate


async def _drive_get(credentials: dict[str, Any], path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        data = await request_json(
            "google_drive", "GET", f"{DRIVE_API}{path}",
            headers={"Authorization": f"Bearer {credentials.get('token')}"},
            params=params, timeout=30.0, attempts=4, max_delay=20.0,
        )
    except IntegrationHttpError as exc:
        raise DriveError(_drive_error(exc)) from exc
    return data if isinstance(data, dict) else {}


def _drive_error(exc: IntegrationHttpError) -> str:
    body = str(exc.response_body or "")
    if "accessNotConfigured" in body or "has not been used in project" in body or "SERVICE_DISABLED" in body:
        return "drive_api_disabled"
    if exc.status_code == 404:
        return "not_found"
    if exc.status_code in (401, 403):
        return "no_access"
    return format_provider_error("Google Drive", exc)


async def _credentials() -> dict[str, Any]:
    return await refresh_default_credentials_if_needed(credential_ref=CREDENTIAL_REF)


async def resolve_folder(credentials: dict[str, Any], folder_id: str) -> dict[str, Any]:
    meta = await _drive_get(credentials, f"/files/{folder_id}", {
        "fields": "id,name,mimeType,webViewLink", "supportsAllDrives": "true",
    })
    if meta.get("mimeType") != FOLDER_MIME:
        raise DriveError("not_a_folder")
    return {"id": meta["id"], "name": meta.get("name") or meta["id"], "url": meta.get("webViewLink")}


async def list_folder_files(credentials: dict[str, Any], folder_id: str, *, limit: int = MAX_FILES_PER_IMPORT):
    """Every file under the folder, with its folder path, breadth first."""
    queue: list[tuple[str, list[str], int]] = [(folder_id, [], 0)]
    found = 0
    while queue and found < limit:
        current, path, depth = queue.pop(0)
        page_token = None
        while found < limit:
            params = {
                "q": f"'{current}' in parents and trashed = false",
                "fields": "nextPageToken, files(id,name,mimeType,modifiedTime,size,webViewLink)",
                "pageSize": 200,
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if page_token:
                params["pageToken"] = page_token
            page = await _drive_get(credentials, "/files", params)
            for item in page.get("files") or []:
                if item.get("mimeType") == FOLDER_MIME:
                    if depth + 1 <= MAX_DEPTH:
                        queue.append((item["id"], [*path, item.get("name") or ""], depth + 1))
                    continue
                found += 1
                yield {**item, "folder_path": path}
                if found >= limit:
                    return
            page_token = page.get("nextPageToken")
            if not page_token:
                break


def readable(item: dict[str, Any]) -> bool:
    if item.get("mimeType") in EXPORTS:
        return True
    try:
        size = int(item.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    return Path(str(item.get("name") or "")).suffix.lower() in DOCUMENT_SUFFIXES and size <= MAX_FILE_BYTES


async def _download(credentials: dict[str, Any], item: dict[str, Any]) -> tuple[bytes, str]:
    headers = {"Authorization": f"Bearer {credentials.get('token')}"}
    export = EXPORTS.get(item.get("mimeType") or "")
    if export:
        url, params, suffix = f"{DRIVE_API}/files/{item['id']}/export", {"mimeType": export[0]}, export[1]
    else:
        url, params = f"{DRIVE_API}/files/{item['id']}", {"alt": "media", "supportsAllDrives": "true"}
        suffix = Path(str(item.get("name") or "")).suffix.lower()
    try:
        response = await request_bytes_response(
            "google_drive", "GET", url, headers=headers, params=params,
            max_bytes=MAX_FILE_BYTES, timeout=120.0, attempts=3, max_delay=20.0, follow_redirects=True,
        )
    except IntegrationHttpError as exc:
        raise DriveError(_drive_error(exc)) from exc
    return response.content, suffix


def _extract_text(content: bytes, suffix: str, name: str = "file") -> str:
    from services.ingest.readers import get_reader

    # Named after the Drive file: readers put the file name in the text.
    stem = re.sub(r"[^\w .()-]+", "_", Path(name.replace("/", "_")).stem).strip()[:120] or "file"
    with tempfile.TemporaryDirectory(prefix="hexis-drive-") as tmp:
        path = Path(tmp) / f"{stem}{suffix}"
        path.write_bytes(content)
        return get_reader(path).read_result(path).text or ""


async def file_text(credentials: dict[str, Any], item: dict[str, Any]) -> str:
    content, suffix = await _download(credentials, item)
    if suffix in {".txt", ".text", ".md", ".markdown", ".csv", ".tsv", ".json"}:
        return content.decode("utf-8", errors="replace")
    return await asyncio.to_thread(_extract_text, content, suffix, str(item.get("name") or "file"))


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


# --- Settings and status (called by the API) --------------------------------


async def connection(pool: Any) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT account_key, display_name, connected_at
            FROM integration_connections
            WHERE connector_id = $1 AND status = 'connected'
            ORDER BY connected_at DESC NULLS LAST
            LIMIT 1
            """,
            CONNECTOR_ID,
        )
    return dict(row) if row else None


async def saved_folders(pool: Any) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        raw = await conn.fetchval("SELECT get_config($1)", FOLDERS_CONFIG_KEY)
    value = _json(raw)
    return [f for f in value if isinstance(f, dict) and f.get("id")] if isinstance(value, list) else []


async def _save_folders(pool: Any, folders: list[dict[str, Any]]) -> None:
    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config($1, $2::jsonb)", FOLDERS_CONFIG_KEY, json.dumps(folders))


async def add_folder(pool: Any, link: str) -> list[dict[str, Any]]:
    folder_id = folder_id_from(link)
    if load_default_credentials(CREDENTIAL_REF) is None:
        raise DriveError("not_connected")
    folder = await resolve_folder(await _credentials(), folder_id)
    folders = [f for f in await saved_folders(pool) if f["id"] != folder["id"]]
    folders.append(folder)
    await _save_folders(pool, folders)
    return folders


async def remove_folder(pool: Any, folder_id: str) -> list[dict[str, Any]]:
    folders = [f for f in await saved_folders(pool) if f["id"] != folder_id]
    await _save_folders(pool, folders)
    return folders


async def start_import(pool: Any) -> dict[str, Any]:
    linked = await connection(pool)
    if not linked:
        raise DriveError("not_connected")
    folders = await saved_folders(pool)
    if not folders:
        raise DriveError("no_folders")
    async with pool.acquire() as conn:
        raw = await conn.fetchval(
            "SELECT enqueue_connector_backfill_job($1, $2, $3, $4::jsonb, '{}'::jsonb)",
            CONNECTOR_ID, linked["account_key"], CURSOR_KEY,
            json.dumps({"folders": [f["id"] for f in folders]}),
        )
    return _json(raw) or {}


async def status(pool: Any) -> dict[str, Any]:
    linked = await connection(pool)
    async with pool.acquire() as conn:
        job = await conn.fetchrow(
            """
            SELECT status, COALESCE(result, progress) AS progress, error, updated_at
            FROM connector_backfill_jobs
            WHERE connector_id = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            CONNECTOR_ID,
        )
        items = await conn.fetchval(
            "SELECT count(*) FROM connector_source_items WHERE connector_id = $1", CONNECTOR_ID,
        )
    return {
        "connected": bool(linked),
        "account": (linked or {}).get("display_name") or (linked or {}).get("account_key"),
        "folders": await saved_folders(pool),
        "import": (
            {
                "status": job["status"],
                "progress": _json(job["progress"]) or {},
                "error": job["error"],
                "updated_at": job["updated_at"].isoformat() if job["updated_at"] else None,
            }
            if job
            else None
        ),
        "files_remembered": int(items or 0),
    }


async def disconnect(pool: Any) -> None:
    from core.auth.google_gmail import delete_default_credentials

    linked = await connection(pool)
    async with pool.acquire() as conn:
        await conn.fetchval(
            "SELECT revoke_integration_connection($1, $2, $3)",
            CONNECTOR_ID, (linked or {}).get("account_key"), "signed out from the Connections page",
        )
    delete_default_credentials(CREDENTIAL_REF)


# --- The import job (run by the maintenance worker) -------------------------------------


async def _job_call(pool: Any, sql: str, *args: Any) -> dict[str, Any]:
    async with pool.acquire() as conn:
        raw = await conn.fetchval(sql, *args)
    value = _json(raw)
    return value if isinstance(value, dict) else {}


async def _fail(pool: Any, job_id: str, error: str) -> dict[str, Any]:
    return await _job_call(pool, "SELECT fail_connector_backfill_job($1::uuid, $2)", job_id, error)


async def _store(pool: Any, job: dict[str, Any], item: dict[str, Any], folder_id: str, text: str) -> None:
    title = str(item.get("name") or item["id"])
    path = "/".join([*item.get("folder_path", []), title])
    async with pool.acquire() as conn:
        await conn.fetchval(
            """
            SELECT upsert_connector_source_item(
                $1, $2, $3, $4, $5, 'document', $6, $7, ARRAY[]::text[],
                '[]'::jsonb, '[]'::jsonb, $8::jsonb, 'private', TRUE
            )
            """,
            CONNECTOR_ID,
            str(job.get("account_key")),
            item["id"],
            title,
            text,
            folder_id,
            _timestamp(item.get("modifiedTime")),
            json.dumps({
                "file_type": (EXPORTS.get(item.get("mimeType") or "", ("", ".txt"))[1]
                              if item.get("mimeType") in EXPORTS
                              else Path(title).suffix.lower()).lstrip(".") or "txt",
                "mime_type": item.get("mimeType"),
                "drive_url": item.get("webViewLink"),
                "drive_path": path,
                "drive_folder_id": folder_id,
                "modified_time": item.get("modifiedTime"),
                # Imported on purpose: history, not news (connector_cognition).
                "acquisition": "import",
            }),
        )


async def process_drive_import_job(pool: Any, job: dict[str, Any]) -> dict[str, Any]:
    job = _json(job) or {}
    job_id = str(job.get("id") or "")
    try:
        credentials = await _credentials()
        account = str(credentials.get("account_email") or "").lower()
        if account and str(job.get("account_key") or "").lower() not in ("", account):
            raise DriveError(f"Signed in to Drive as {account}, but the import is for {job.get('account_key')}.")
        requested = _json(job.get("requested_range")) or {}
        folder_ids = [f for f in requested.get("folders") or [] if isinstance(f, str)]
        async with pool.acquire() as conn:
            raw = await conn.fetchval(
                "SELECT cursor_value FROM connector_sync_cursors WHERE connection_id = $1::uuid AND cursor_key = $2",
                str(job.get("connection_id")), CURSOR_KEY,
            )
        seen: dict[str, str] = dict((_json(raw) or {}).get("seen") or {})

        counts = {"folders": len(folder_ids), "files_seen": 0, "files_read": 0, "unchanged": 0, "skipped": 0, "failed": 0}
        newest: datetime | None = None
        for folder_id in folder_ids:
            async for item in list_folder_files(credentials, folder_id):
                counts["files_seen"] += 1
                modified = str(item.get("modifiedTime") or "")
                if not readable(item):
                    counts["skipped"] += 1
                elif seen.get(item["id"]) == modified:
                    counts["unchanged"] += 1
                else:
                    try:
                        text = await file_text(credentials, item)
                        if text.strip():
                            await _store(pool, job, item, folder_id, text)
                            counts["files_read"] += 1
                        else:
                            counts["skipped"] += 1
                        seen[item["id"]] = modified
                    except Exception as exc:  # one bad file shouldn't stop the folder
                        counts["failed"] += 1
                        logger.warning("Drive file %s not read: %s", item.get("id"), exc)
                stamp = _timestamp(modified)
                if stamp and (newest is None or stamp > newest):
                    newest = stamp
                if counts["files_seen"] % PROGRESS_EVERY == 0:
                    progress = await _job_call(
                        pool, "SELECT update_connector_backfill_progress($1::uuid, $2::jsonb, $3::jsonb, $4)",
                        job_id, json.dumps(counts), json.dumps({"seen": seen}), newest,
                    )
                    if progress.get("cancel_requested"):
                        return await _fail(pool, job_id, "cancelled by request")
                    if progress.get("pause_requested"):
                        return await _fail(pool, job_id, "paused by request")
                    credentials = await _credentials()  # long imports outlive a token
        return await _job_call(
            pool, "SELECT complete_connector_backfill_job($1::uuid, $2::jsonb, $3::jsonb, $4)",
            job_id, json.dumps(counts), json.dumps({"seen": seen}), newest,
        )
    except (DriveError, GmailOAuthError) as exc:
        logger.warning("Drive import %s failed: %s", job_id, exc)
        return await _fail(pool, job_id, str(exc))
    except Exception as exc:
        logger.exception("Drive import %s failed unexpectedly", job_id)
        return await _fail(pool, job_id, str(exc))


async def run_drive_import_step(pool: Any, *, limit: int | None = None) -> int:
    """Claim and run due Drive imports; returns how many were handled."""
    if load_default_credentials(CREDENTIAL_REF) is None:
        return 0
    async with pool.acquire() as conn:
        raw = await conn.fetchval("SELECT claim_connector_backfill_jobs_for($1, $2::int)", CONNECTOR_ID, limit)
    jobs = _json(raw) or []
    for job in jobs if isinstance(jobs, list) else []:
        await process_drive_import_job(pool, job)
    return len(jobs) if isinstance(jobs, list) else 0
