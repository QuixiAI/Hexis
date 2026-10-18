"""Gmail connector backfill adapter.

Postgres owns the durable backfill lifecycle. This module only does provider
I/O and message-to-source-document conversion.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from core.auth.google_gmail import (
    GmailOAuthError,
    GOOGLE_GMAIL_PROFILE_URL,
    load_default_credentials,
    refresh_default_credentials_if_needed,
)
from core.integration_reliability import (
    IntegrationHttpError,
    format_provider_error,
    request_json,
)

logger = logging.getLogger(__name__)

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"
GMAIL_CONNECTOR_ID = "gmail"


class GmailBackfillError(RuntimeError):
    """Expected, DB-recorded Gmail backfill failure."""


class GmailPermissionError(GmailBackfillError):
    """Google didn't grant mail access; retrying can't help, only signing in again."""


GMAIL_READ_SCOPES = {
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://mail.google.com/",
}
NO_MAIL_PERMISSION = (
    "Google didn't give Hexis permission to read this mailbox. Sign in to Gmail again "
    "(Connections → Gmail), and on Google's screen make sure the box to read your email is ticked."
)


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=str)


def _coerce_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _headers(payload: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in payload.get("headers") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if isinstance(name, str) and isinstance(value, str):
            result.setdefault(name.lower(), value)
    return result


def _header(headers: dict[str, str], name: str, default: str = "") -> str:
    return headers.get(name.lower(), default)


def _decode_body_data(data: Any) -> str:
    if not isinstance(data, str) or not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")
    except (ValueError, UnicodeError):
        return ""


# Shared with every other document read into memory.
from services.ingest.textclean import clean_email_text, strip_html as _strip_html  # noqa: E402


def _walk_payload_parts(payload: dict[str, Any]):
    yield payload
    for part in payload.get("parts") or []:
        if isinstance(part, dict):
            yield from _walk_payload_parts(part)


def extract_message_body(payload: dict[str, Any]) -> str:
    """Extract all inline text from a Gmail message payload."""
    plain_parts: list[str] = []
    html_parts: list[str] = []
    for part in _walk_payload_parts(payload):
        mime_type = str(part.get("mimeType") or "")
        body = part.get("body") if isinstance(part.get("body"), dict) else {}
        data = _decode_body_data(body.get("data"))
        if not data:
            continue
        if mime_type == "text/plain":
            plain_parts.append(clean_email_text(data))
        elif mime_type == "text/html":
            html_parts.append(clean_email_text(_strip_html(data)))

    if plain_parts:
        return "\n\n".join(part.strip() for part in plain_parts if part.strip())
    if html_parts:
        return "\n\n".join(part.strip() for part in html_parts if part.strip())
    return ""


def extract_message_attachments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    for part in _walk_payload_parts(payload):
        filename = part.get("filename")
        if not isinstance(filename, str) or not filename:
            continue
        body = part.get("body") if isinstance(part.get("body"), dict) else {}
        attachments.append(
            {
                "filename": filename,
                "mime_type": part.get("mimeType") or "",
                "size": body.get("size") or 0,
                "attachment_id": body.get("attachmentId"),
            }
        )
    return attachments


def _participants(headers: dict[str, str]) -> list[dict[str, str]]:
    participants: list[dict[str, str]] = []
    for role, name in (("from", "From"), ("to", "To"), ("cc", "Cc"), ("bcc", "Bcc"), ("reply_to", "Reply-To")):
        value = _header(headers, name)
        if value:
            participants.append({"role": role, "value": value})
    return participants


def _message_timestamp(message: dict[str, Any]) -> datetime | None:
    raw = message.get("internalDate")
    try:
        millis = int(raw)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc)


def gmail_message_to_source_item(message: dict[str, Any]) -> dict[str, Any]:
    """Convert Gmail's full-message JSON to the DB source-item contract."""
    payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
    headers = _headers(payload)
    message_id = str(message.get("id") or "").strip()
    if not message_id:
        raise GmailBackfillError("Gmail message payload is missing id.")

    subject = _header(headers, "Subject", "(No subject)")
    body = extract_message_body(payload)
    labels = [str(label) for label in (message.get("labelIds") or []) if str(label).strip()]
    attachments = extract_message_attachments(payload)
    date_header = _header(headers, "Date")
    snippet = clean_email_text(str(message.get("snippet") or ""))

    content_lines = [
        f"Subject: {subject}",
        f"From: {_header(headers, 'From')}",
        f"To: {_header(headers, 'To')}",
    ]
    if _header(headers, "Cc"):
        content_lines.append(f"Cc: {_header(headers, 'Cc')}")
    if date_header:
        content_lines.append(f"Date: {date_header}")
    if labels:
        content_lines.append(f"Labels: {', '.join(labels)}")
    if snippet:
        content_lines.extend(["", "Snippet:", snippet])
    content_lines.extend(["", "Body:", body or "(No extractable text body)"])

    raw_headers = [
        {"name": item.get("name"), "value": item.get("value")}
        for item in payload.get("headers") or []
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    ]
    return {
        "provider_item_id": message_id,
        "title": subject,
        "content": "\n".join(content_lines),
        "item_kind": "message",
        "provider_thread_id": message.get("threadId"),
        "item_timestamp": _message_timestamp(message),
        "labels": labels,
        "participants": _participants(headers),
        "attachments": attachments,
        "metadata": {
            "gmail_history_id": message.get("historyId"),
            "gmail_size_estimate": message.get("sizeEstimate"),
            "gmail_internal_date_ms": message.get("internalDate"),
            "gmail_payload_mime_type": payload.get("mimeType"),
            "gmail_snippet": snippet,
            "gmail_headers": raw_headers,
        },
    }


async def _gmail_get(
    credentials: dict[str, Any],
    path: str,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    token = credentials.get("token")
    if not isinstance(token, str) or not token:
        raise GmailBackfillError("Saved Gmail credentials are missing an access token.")
    url = path if path.startswith("http") else f"{GMAIL_API_BASE}{path}"
    try:
        payload = await request_json(
            "gmail",
            "GET",
            url,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=30.0,
            attempts=4,
            max_delay=20.0,
        )
    except IntegrationHttpError as exc:
        if exc.status_code == 403 and "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in (exc.response_body or ""):
            raise GmailPermissionError(NO_MAIL_PERMISSION) from exc
        raise GmailBackfillError(format_provider_error("Gmail", exc)) from exc
    if not isinstance(payload, dict):
        raise GmailBackfillError("Gmail API returned an invalid payload.")
    return payload


async def _connected_account_email(credentials: dict[str, Any]) -> str | None:
    cached = credentials.get("account_email")
    if isinstance(cached, str) and cached.strip():
        return cached.strip().lower()
    try:
        payload = await _gmail_get(credentials, GOOGLE_GMAIL_PROFILE_URL)
    except Exception:
        return None
    email = payload.get("emailAddress")
    return email.strip().lower() if isinstance(email, str) and email.strip() else None


async def _load_cursor_value(pool: Any, job: dict[str, Any]) -> dict[str, Any]:
    async with pool.acquire() as conn:
        raw = await conn.fetchval(
            """
            SELECT cursor_value
            FROM connector_sync_cursors
            WHERE connection_id = $1::uuid
              AND cursor_key = $2
            """,
            str(job.get("connection_id")),
            str(job.get("cursor_key") or "messages"),
        )
    value = _json(raw) or {}
    return value if isinstance(value, dict) else {}


async def _upsert_source_item(pool: Any, job: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    async with pool.acquire() as conn:
        raw = await conn.fetchval(
            """
            SELECT upsert_connector_source_item(
                'gmail',
                $1,
                $2,
                $3,
                $4,
                $5,
                $6,
                $7,
                $8::text[],
                $9::jsonb,
                $10::jsonb,
                $11::jsonb,
                'private',
                TRUE
            )
            """,
            str(job.get("account_key")),
            source["provider_item_id"],
            source["title"],
            source["content"],
            source["item_kind"],
            source.get("provider_thread_id"),
            source.get("item_timestamp"),
            source["labels"],
            _json_dumps(source["participants"]),
            _json_dumps(source["attachments"]),
            _json_dumps(source["metadata"]),
        )
    return _json(raw) or {}


async def _update_progress(
    pool: Any,
    job_id: str,
    progress: dict[str, Any],
    cursor_value: dict[str, Any] | None,
    high_watermark: datetime | None,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        raw = await conn.fetchval(
            """
            SELECT update_connector_backfill_progress(
                $1::uuid,
                $2::jsonb,
                $3::jsonb,
                $4
            )
            """,
            job_id,
            _json_dumps(progress),
            _json_dumps(cursor_value) if cursor_value is not None else None,
            high_watermark,
        )
    return _json(raw) or {}


async def _complete_job(
    pool: Any,
    job_id: str,
    result: dict[str, Any],
    cursor_value: dict[str, Any] | None,
    high_watermark: datetime | None,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        raw = await conn.fetchval(
            """
            SELECT complete_connector_backfill_job(
                $1::uuid,
                $2::jsonb,
                $3::jsonb,
                $4
            )
            """,
            job_id,
            _json_dumps(result),
            _json_dumps(cursor_value) if cursor_value is not None else None,
            high_watermark,
        )
    return _json(raw) or {}


async def _fail_job(pool: Any, job_id: str, error: str, *, final: bool = False) -> dict[str, Any]:
    async with pool.acquire() as conn:
        if final:  # no retries left, so it is marked failed now instead of spinning
            await conn.execute(
                "UPDATE connector_backfill_jobs SET attempts = GREATEST(attempts, max_attempts) WHERE id = $1::uuid",
                job_id,
            )
        raw = await conn.fetchval("SELECT fail_connector_backfill_job($1::uuid, $2)", job_id, error[:2000])
    return _json(raw) or {}


# What Gmail itself files as not worth reading: spam, trash and the
# Promotions, Social and Forums tabs. Asked of the search, so they're never fetched.
_SKIP_QUERY = "-in:spam -in:trash -category:promotions -category:social -category:forums"
_SKIP_LABELS = {"SPAM", "TRASH", "CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"}


def worth_reading(message: dict[str, Any]) -> bool:
    """Whether an email is worth turning into memories.

    Gmail's own sorting decides: mail it marks important, mail the person
    starred or sent, and Primary mail are read; spam, promotions, social and
    forum mail are not, nor are automated updates and mailing-list mail
    (alerts, receipts, newsletters) unless Gmail marks them important.
    """
    labels = {str(label) for label in (message.get("labelIds") or [])}
    if labels & {"SPAM", "TRASH"}:
        return False
    if labels & {"STARRED", "SENT"}:
        return True
    if labels & _SKIP_LABELS:
        return False
    if "IMPORTANT" in labels:
        return True
    if "CATEGORY_UPDATES" in labels:
        return False
    payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
    headers = {str(h.get("name") or "").lower(): str(h.get("value") or "").lower()
               for h in payload.get("headers") or [] if isinstance(h, dict)}
    if "list-unsubscribe" in headers or "list-id" in headers:
        return False
    if headers.get("precedence") in {"bulk", "list", "junk"}:
        return False
    return True


def _request_options(job: dict[str, Any], cursor_value: dict[str, Any]) -> dict[str, Any]:
    requested = _json(job.get("requested_range")) or {}
    if not isinstance(requested, dict):
        requested = {}

    labels = requested.get("label_ids") or requested.get("labels") or []
    if isinstance(labels, str):
        labels = [part.strip() for part in labels.split(",") if part.strip()]
    elif not isinstance(labels, list):
        labels = []

    query = str(requested.get("query") or "").strip() or None
    skip_unimportant = bool(requested.get("skip_unimportant", True))
    search = " ".join(part for part in (query, _SKIP_QUERY if skip_unimportant else None) if part) or None
    # A page token only continues the search that produced it.
    same_search = cursor_value.get("search", cursor_value.get("query")) == search
    return {
        "query": query,
        "search": search,
        "skip_unimportant": skip_unimportant,
        "label_ids": [str(label).strip() for label in labels if str(label).strip()],
        "include_spam_trash": bool(requested.get("include_spam_trash", False)),
        "max_messages": _coerce_int(requested.get("max_messages"), 100, minimum=1, maximum=500),
        "page_size": _coerce_int(requested.get("page_size"), 100, minimum=1, maximum=100),
        "page_token": (
            str(requested.get("page_token") or "").strip()
            or (str(cursor_value.get("page_token") or "").strip() if same_search else "")
            or None
        ),
    }


async def process_gmail_backfill_job(pool: Any, job: dict[str, Any]) -> dict[str, Any]:
    """Process one claimed Gmail connector backfill job."""
    job = _json(job) or {}
    job_id = str(job.get("id") or "")
    if not job_id:
        raise GmailBackfillError("Claimed connector backfill job is missing id.")
    if job.get("connector_id") != GMAIL_CONNECTOR_ID:
        return await _fail_job(pool, job_id, f"Unsupported connector for Gmail worker: {job.get('connector_id')}")

    try:
        credentials = await refresh_default_credentials_if_needed()
        granted = credentials.get("scopes")
        if isinstance(granted, list) and granted and not GMAIL_READ_SCOPES.intersection(granted):
            raise GmailPermissionError(NO_MAIL_PERMISSION)
        account_email = await _connected_account_email(credentials)
        account_key = str(job.get("account_key") or "").strip().lower()
        if account_email and account_key and account_email != account_key:
            raise GmailBackfillError(
                f"Saved Gmail credentials are for {account_email}, but the queued job is for {account_key}."
            )

        cursor_value = await _load_cursor_value(pool, job)
        options = _request_options(job, cursor_value)
        max_messages = int(options["max_messages"])
        page_size = min(int(options["page_size"]), max_messages)
        page_token = options["page_token"]

        pages = 0
        items_seen = 0
        items_stored = 0
        items_skipped = 0
        high_watermark: datetime | None = None
        last_message_id: str | None = None
        last_history_id: Any = cursor_value.get("history_id")

        while items_seen < max_messages:
            params: dict[str, Any] = {
                "maxResults": min(page_size, max_messages - items_seen),
                "includeSpamTrash": options["include_spam_trash"],
            }
            if options["search"]:
                params["q"] = options["search"]
            if page_token:
                params["pageToken"] = page_token
            label_ids = options["label_ids"]
            if label_ids:
                params["labelIds"] = label_ids

            listed = await _gmail_get(credentials, "/users/me/messages", params=params)
            pages += 1
            stubs = listed.get("messages") or []
            if not isinstance(stubs, list):
                stubs = []

            for stub in stubs:
                if items_seen >= max_messages:
                    break
                if not isinstance(stub, dict) or not isinstance(stub.get("id"), str):
                    continue
                message = await _gmail_get(
                    credentials,
                    f"/users/me/messages/{stub['id']}",
                    params={"format": "full"},
                )
                items_seen += 1
                if options["skip_unimportant"] and not worth_reading(message):
                    items_skipped += 1
                    continue
                source = gmail_message_to_source_item(message)
                # Imported mail is history, not news: no alerts (connector_cognition).
                source["metadata"]["acquisition"] = "import"
                await _upsert_source_item(pool, job, source)
                items_stored += 1
                last_message_id = source["provider_item_id"]
                last_history_id = source["metadata"].get("gmail_history_id") or last_history_id
                timestamp = source.get("item_timestamp")
                if isinstance(timestamp, datetime) and (
                    high_watermark is None or timestamp > high_watermark
                ):
                    high_watermark = timestamp

            next_page_token = listed.get("nextPageToken")
            page_token = next_page_token if isinstance(next_page_token, str) and next_page_token else None
            next_cursor = {
                "page_token": page_token,
                "history_id": last_history_id,
                "query": options["query"],
                "search": options["search"],
                "label_ids": label_ids,
                "last_message_id": last_message_id,
            }
            progress = await _update_progress(
                pool,
                job_id,
                {
                    "pages": pages,
                    "items_seen": items_seen,
                    "items_stored": items_stored,
                    "items_skipped": items_skipped,
                    "truncated": bool(page_token),
                },
                next_cursor,
                high_watermark,
            )
            if progress.get("cancel_requested"):
                return await _fail_job(pool, job_id, "cancelled by request")
            if progress.get("pause_requested"):
                return await _fail_job(pool, job_id, "paused by request")
            if not page_token:
                break

        final_cursor = {
            "page_token": page_token,
            "history_id": last_history_id,
            "query": options["query"],
            "search": options["search"],
            "label_ids": options["label_ids"],
            "last_message_id": last_message_id,
        }
        return await _complete_job(
            pool,
            job_id,
            {
                "pages": pages,
                "items_seen": items_seen,
                "items_stored": items_stored,
                "items_skipped": items_skipped,
                "truncated": bool(page_token),
                "next_page_token": page_token,
            },
            final_cursor,
            high_watermark,
        )
    except GmailPermissionError as exc:
        logger.warning("Gmail backfill job %s can't read mail: %s", job_id, exc)
        async with pool.acquire() as conn:  # brings "Start Google sign-in" back to the Connections page
            await conn.execute(
                "UPDATE integration_connections SET status = 'needs_reauth', updated_at = CURRENT_TIMESTAMP "
                "WHERE id = $1::uuid AND status = 'connected'",
                str(job.get("connection_id") or ""),
            )
        return await _fail_job(pool, job_id, str(exc), final=True)
    except (GmailBackfillError, GmailOAuthError) as exc:
        logger.warning("Gmail backfill job %s failed: %s", job_id, exc)
        return await _fail_job(pool, job_id, str(exc))
    except Exception as exc:
        logger.exception("Gmail backfill job %s failed unexpectedly", job_id)
        return await _fail_job(pool, job_id, str(exc))


async def run_gmail_backfill_step(pool: Any, *, limit: int | None = None) -> int:
    """Claim and process due Gmail backfill jobs; returns jobs handled."""
    if load_default_credentials() is None:
        return 0
    async with pool.acquire() as conn:
        raw = await conn.fetchval(
            "SELECT claim_connector_backfill_jobs_for('gmail', $1::int)",
            limit,
        )
    jobs = _json(raw) or []
    if not isinstance(jobs, list):
        jobs = []
    for job in jobs:
        await process_gmail_backfill_job(pool, job)
    return len(jobs)
