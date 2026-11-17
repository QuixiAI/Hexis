"""Twitter/X provider adapter.

Postgres owns connector manifests, jobs, cursors, policy, and audit. This
module performs provider I/O and converts X API resources into the connector
source-item contract.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from core.auth.twitter_x import (
    TwitterXOAuthError,
    load_default_credentials,
    refresh_default_credentials_if_needed,
)
from core.integration_reliability import (
    IntegrationHttpError,
    format_provider_error,
    request_json_response,
)

logger = logging.getLogger(__name__)

TWITTER_X_API_BASE = "https://api.x.com/2"
TWITTER_X_CONNECTOR_ID = "twitter_x"

SCOPE_TWEET_READ = "tweet.read"
SCOPE_TWEET_WRITE = "tweet.write"
SCOPE_USERS_READ = "users.read"
SCOPE_DM_READ = "dm.read"
SCOPE_DM_WRITE = "dm.write"


class TwitterXProviderError(RuntimeError):
    """Expected Twitter/X provider failure with a user-actionable message."""


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


def _scope_set(credentials: dict[str, Any]) -> set[str]:
    return {str(scope) for scope in credentials.get("scopes") or []}


def _require_scopes(credentials: dict[str, Any], scopes: list[str], capability: str) -> None:
    have = _scope_set(credentials)
    missing = [scope for scope in scopes if scope not in have]
    if missing:
        raise TwitterXProviderError(
            f"Saved Twitter/X credentials do not include {capability}: missing {', '.join(missing)}. "
            f"Reconnect Twitter/X with the {capability} capability before using this action."
        )


def _saved_account(credentials: dict[str, Any]) -> str | None:
    account = credentials.get("account_key")
    if isinstance(account, str) and account.strip():
        return account.strip()
    user_id = credentials.get("user_id")
    if isinstance(user_id, str) and user_id.strip():
        return f"x:{user_id.strip()}"
    return None


def _check_account(credentials: dict[str, Any], account_key: str | None) -> str | None:
    saved = _saved_account(credentials)
    requested = account_key.strip() if isinstance(account_key, str) and account_key.strip() else None
    username = credentials.get("username")
    username_key = f"@{username}".lower() if isinstance(username, str) and username.strip() else None
    if saved and requested and requested != saved and requested.lower() != username_key:
        raise TwitterXProviderError(
            f"Saved Twitter/X credentials are for {saved}, but this action requested {requested}."
        )
    return requested or saved


def _rate_limit_metadata(headers: dict[str, str]) -> dict[str, Any]:
    return {
        key: headers.get(key)
        for key in ("x-rate-limit-limit", "x-rate-limit-remaining", "x-rate-limit-reset")
        if headers.get(key) is not None
    }


async def twitter_x_request(
    credentials: dict[str, Any],
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    token = credentials.get("token")
    if not isinstance(token, str) or not token:
        raise TwitterXProviderError("Saved Twitter/X credentials are missing an access token.")
    url = path if path.startswith("http") else f"{TWITTER_X_API_BASE}{path}"
    try:
        response = await request_json_response(
            "twitter_x",
            method.upper(),
            url,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            json_body=json_body,
            timeout=30.0,
            attempts=4,
            max_delay=30.0,
            retry_unsafe_methods=False,
        )
    except IntegrationHttpError as exc:
        raise TwitterXProviderError(format_provider_error("Twitter/X", exc)) from exc
    payload = response.json_data
    if not isinstance(payload, dict):
        raise TwitterXProviderError("Twitter/X API returned an invalid payload.")
    payload["_rate_limit"] = _rate_limit_metadata(response.headers)
    return payload


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def tweet_to_source_item(tweet: dict[str, Any], *, account_key: str, stream: str) -> dict[str, Any] | None:
    tweet_id = str(tweet.get("id") or "").strip()
    text = str(tweet.get("text") or "").strip()
    if not tweet_id and not text:
        return None
    author_id = str(tweet.get("author_id") or account_key).strip()
    conversation_id = str(tweet.get("conversation_id") or "").strip() or None
    created_at = _parse_timestamp(tweet.get("created_at"))
    referenced = tweet.get("referenced_tweets") if isinstance(tweet.get("referenced_tweets"), list) else []
    metrics = tweet.get("public_metrics") if isinstance(tweet.get("public_metrics"), dict) else {}
    labels = ["twitter_x", "post", stream]
    if referenced:
        labels.append("referenced")

    content_lines = [
        "Twitter/X post",
        f"Tweet id: {tweet_id}",
        f"Stream: {stream}",
        f"Author id: {author_id}",
    ]
    if conversation_id:
        content_lines.append(f"Conversation id: {conversation_id}")
    if created_at:
        content_lines.append(f"Created at: {created_at.isoformat()}")
    content_lines.extend(["", "Post:", text or "(No text body)"])

    return {
        "provider_item_id": f"tweet:{tweet_id}",
        "title": f"Twitter/X post {tweet_id}",
        "content": "\n".join(content_lines),
        "item_kind": "post",
        "provider_thread_id": conversation_id,
        "item_timestamp": created_at,
        "labels": labels,
        "participants": [{"role": "author", "id": author_id}],
        "attachments": [],
        "sensitivity": "shared",
        "metadata": {
            "twitter_x_tweet_id": tweet_id,
            "twitter_x_stream": stream,
            "twitter_x_raw_tweet": tweet,
            "public_metrics": metrics,
            "account_key": account_key,
        },
    }


def dm_event_to_source_item(event: dict[str, Any], *, account_key: str, stream: str = "dms") -> dict[str, Any] | None:
    event_id = str(event.get("id") or "").strip()
    text = str(event.get("text") or "").strip()
    if not event_id and not text:
        return None
    conversation_id = str(
        event.get("dm_conversation_id")
        or event.get("dm_conversation_id_str")
        or event.get("conversation_id")
        or "dm_events"
    )
    sender = str(event.get("sender_id") or event.get("senderId") or "unknown")
    created_at = _parse_timestamp(event.get("created_at") or event.get("createdAt"))
    event_type = str(event.get("event_type") or event.get("type") or "MessageCreate")

    content_lines = [
        f"Twitter/X DM conversation: {conversation_id}",
        f"Event id: {event_id}",
        f"Event type: {event_type}",
        f"Sender id: {sender}",
    ]
    if created_at:
        content_lines.append(f"Created at: {created_at.isoformat()}")
    content_lines.extend(["", "Message:", text or "(No text body)"])

    return {
        "provider_item_id": f"dm:{conversation_id}:{event_id}",
        "title": f"Twitter/X DM in {conversation_id}",
        "content": "\n".join(content_lines),
        "item_kind": "message",
        "provider_thread_id": conversation_id,
        "item_timestamp": created_at,
        "labels": ["twitter_x", "dm", stream],
        "participants": [{"role": "sender", "id": sender}],
        "attachments": [],
        "sensitivity": "private",
        "metadata": {
            "twitter_x_conversation_id": conversation_id,
            "twitter_x_event_id": event_id,
            "twitter_x_event_type": event_type,
            "twitter_x_raw_dm_event": event,
            "account_key": account_key,
        },
    }


def _requested_range(job: dict[str, Any]) -> dict[str, Any]:
    requested = _json(job.get("requested_range")) or {}
    return requested if isinstance(requested, dict) else {}


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
                'twitter_x',
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
                $12,
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
            _json_dumps({**source["metadata"], "acquisition": "import"}),  # history, not news: no alerts
            source.get("sensitivity", "shared"),
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


async def _fail_job(pool: Any, job_id: str, error: str) -> dict[str, Any]:
    async with pool.acquire() as conn:
        raw = await conn.fetchval("SELECT fail_connector_backfill_job($1::uuid, $2)", job_id, error[:2000])
    return _json(raw) or {}


def _request_options(job: dict[str, Any], cursor_value: dict[str, Any], credentials: dict[str, Any]) -> dict[str, Any]:
    requested = _requested_range(job)
    stream = str(requested.get("stream") or requested.get("source") or cursor_value.get("stream") or "timeline")
    stream = stream.strip().lower().replace("-", "_")
    if stream in {"tweets", "user_tweets", "posts"}:
        stream = "timeline"
    if stream in {"mention", "mentions"}:
        stream = "mentions"
    if stream in {"dm", "dms", "dm_events", "direct_messages"}:
        stream = "dms"
    if stream not in {"timeline", "mentions", "search", "dms"}:
        raise TwitterXProviderError("Twitter/X backfill stream must be timeline, mentions, search, or dms.")

    user_id = str(requested.get("user_id") or credentials.get("user_id") or "").strip()
    if stream in {"timeline", "mentions"} and not user_id:
        raise TwitterXProviderError("Twitter/X live backfill needs the connected account user id. Reconnect Twitter/X.")

    return {
        "stream": stream,
        "user_id": user_id,
        "query": str(requested.get("query") or cursor_value.get("query") or "").strip(),
        "max_messages": _coerce_int(requested.get("max_messages"), 100, minimum=1, maximum=5000),
        "page_size": _coerce_int(requested.get("page_size"), 100, minimum=10, maximum=100),
        "pagination_token": (
            str(requested.get("pagination_token") or requested.get("next_token") or "").strip()
            or str(cursor_value.get("pagination_token") or "").strip()
            or None
        ),
    }


def _endpoint_for_options(options: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    stream = options["stream"]
    page_size = options["page_size"]
    pagination_token = options["pagination_token"]
    if stream == "timeline":
        path = f"/users/{options['user_id']}/tweets"
        params: dict[str, Any] = {
            "max_results": page_size,
            "tweet.fields": "id,text,created_at,author_id,conversation_id,referenced_tweets,public_metrics",
        }
    elif stream == "mentions":
        path = f"/users/{options['user_id']}/mentions"
        params = {
            "max_results": page_size,
            "tweet.fields": "id,text,created_at,author_id,conversation_id,referenced_tweets,public_metrics",
        }
    elif stream == "search":
        if not options["query"]:
            raise TwitterXProviderError("Twitter/X search backfill requires requested_range.query.")
        path = "/tweets/search/recent"
        params = {
            "query": options["query"],
            "max_results": page_size,
            "tweet.fields": "id,text,created_at,author_id,conversation_id,referenced_tweets,public_metrics",
        }
    else:
        path = "/dm_events"
        params = {
            "max_results": page_size,
            "dm_event.fields": "id,text,event_type,created_at,dm_conversation_id,sender_id",
        }
    if pagination_token:
        params["pagination_token"] = pagination_token
    return path, params, stream


async def process_twitter_x_backfill_job(pool: Any, job: dict[str, Any]) -> dict[str, Any]:
    job = _json(job) or {}
    job_id = str(job.get("id") or "")
    if not job_id:
        raise TwitterXProviderError("Claimed connector backfill job is missing id.")
    if job.get("connector_id") != TWITTER_X_CONNECTOR_ID:
        return await _fail_job(pool, job_id, f"Unsupported connector for Twitter/X worker: {job.get('connector_id')}")

    try:
        credentials = await refresh_default_credentials_if_needed()
        _check_account(credentials, str(job.get("account_key") or ""))
        cursor_value = await _load_cursor_value(pool, job)
        options = _request_options(job, cursor_value, credentials)
        read_scopes = [SCOPE_TWEET_READ, SCOPE_USERS_READ]
        if options["stream"] == "dms":
            read_scopes = [SCOPE_DM_READ, SCOPE_TWEET_READ, SCOPE_USERS_READ]
        _require_scopes(credentials, read_scopes, f"{options['stream']} read")

        max_messages = int(options["max_messages"])
        pages = 0
        items_seen = 0
        items_stored = 0
        high_watermark: datetime | None = None
        next_token = options["pagination_token"]
        last_item_id: str | None = cursor_value.get("last_item_id")
        rate_limit: dict[str, Any] = {}

        while items_seen < max_messages:
            options["pagination_token"] = next_token
            path, params, stream = _endpoint_for_options(options)
            payload = await twitter_x_request(credentials, "GET", path, params=params)
            pages += 1
            rate_limit = payload.get("_rate_limit") if isinstance(payload.get("_rate_limit"), dict) else {}
            data = payload.get("data") or []
            if isinstance(data, dict):
                data = [data]
            if not isinstance(data, list):
                data = []

            for item in data:
                if items_seen >= max_messages or not isinstance(item, dict):
                    break
                if stream == "dms":
                    source = dm_event_to_source_item(item, account_key=str(job.get("account_key")), stream=stream)
                else:
                    source = tweet_to_source_item(item, account_key=str(job.get("account_key")), stream=stream)
                if not source:
                    continue
                await _upsert_source_item(pool, job, source)
                items_seen += 1
                items_stored += 1
                last_item_id = source["provider_item_id"]
                timestamp = source.get("item_timestamp")
                if isinstance(timestamp, datetime) and (
                    high_watermark is None or timestamp > high_watermark
                ):
                    high_watermark = timestamp

            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
            next_token = str(meta.get("next_token") or "").strip() or None
            next_cursor = {
                "stream": stream,
                "query": options["query"],
                "pagination_token": next_token,
                "last_item_id": last_item_id,
                "rate_limit": rate_limit,
            }
            progress = await _update_progress(
                pool,
                job_id,
                {
                    "pages": pages,
                    "items_seen": items_seen,
                    "items_stored": items_stored,
                    "truncated": bool(next_token),
                    "rate_limit": rate_limit,
                },
                next_cursor,
                high_watermark,
            )
            if progress.get("cancel_requested"):
                return await _fail_job(pool, job_id, "cancelled by request")
            if progress.get("pause_requested"):
                return await _fail_job(pool, job_id, "paused by request")
            if not next_token:
                break

        final_cursor = {
            "stream": options["stream"],
            "query": options["query"],
            "pagination_token": next_token,
            "last_item_id": last_item_id,
            "rate_limit": rate_limit,
        }
        return await _complete_job(
            pool,
            job_id,
            {
                "pages": pages,
                "items_seen": items_seen,
                "items_stored": items_stored,
                "truncated": bool(next_token),
                "next_token": next_token,
                "rate_limit": rate_limit,
            },
            final_cursor,
            high_watermark,
        )
    except (TwitterXProviderError, TwitterXOAuthError) as exc:
        logger.warning("Twitter/X backfill job %s failed: %s", job_id, exc)
        return await _fail_job(pool, job_id, str(exc))
    except Exception as exc:
        logger.exception("Twitter/X backfill job %s failed unexpectedly", job_id)
        return await _fail_job(pool, job_id, str(exc))


async def post_twitter_x(
    *,
    account_key: str | None,
    text: str,
) -> dict[str, Any]:
    credentials = await refresh_default_credentials_if_needed()
    _require_scopes(credentials, [SCOPE_TWEET_READ, SCOPE_TWEET_WRITE, SCOPE_USERS_READ], "post")
    account = _check_account(credentials, account_key)
    payload = await twitter_x_request(credentials, "POST", "/tweets", json_body={"text": text})
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return {
        "sent": True,
        "connector_id": TWITTER_X_CONNECTOR_ID,
        "account_key": account,
        "tweet_id": data.get("id"),
        "text": data.get("text") or text,
        "rate_limit": payload.get("_rate_limit") or {},
    }


async def reply_twitter_x(
    *,
    account_key: str | None,
    reply_to_tweet_id: str,
    text: str,
) -> dict[str, Any]:
    credentials = await refresh_default_credentials_if_needed()
    _require_scopes(credentials, [SCOPE_TWEET_READ, SCOPE_TWEET_WRITE, SCOPE_USERS_READ], "reply")
    account = _check_account(credentials, account_key)
    payload = await twitter_x_request(
        credentials,
        "POST",
        "/tweets",
        json_body={"text": text, "reply": {"in_reply_to_tweet_id": reply_to_tweet_id}},
    )
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return {
        "sent": True,
        "connector_id": TWITTER_X_CONNECTOR_ID,
        "account_key": account,
        "tweet_id": data.get("id"),
        "reply_to_tweet_id": reply_to_tweet_id,
        "text": data.get("text") or text,
        "rate_limit": payload.get("_rate_limit") or {},
    }


async def send_twitter_x_dm(
    *,
    account_key: str | None,
    participant_id: str,
    text: str,
) -> dict[str, Any]:
    credentials = await refresh_default_credentials_if_needed()
    _require_scopes(credentials, [SCOPE_DM_WRITE, SCOPE_TWEET_READ, SCOPE_USERS_READ], "dm_send")
    account = _check_account(credentials, account_key)
    payload = await twitter_x_request(
        credentials,
        "POST",
        f"/dm_conversations/with/{participant_id}/messages",
        json_body={"text": text},
    )
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return {
        "sent": True,
        "connector_id": TWITTER_X_CONNECTOR_ID,
        "account_key": account,
        "dm_event_id": data.get("dm_event_id") or data.get("id"),
        "participant_id": participant_id,
        "text": text,
        "rate_limit": payload.get("_rate_limit") or {},
    }


async def run_twitter_x_backfill_step(pool: Any, *, limit: int | None = None) -> int:
    if load_default_credentials() is None:
        return 0
    async with pool.acquire() as conn:
        raw = await conn.fetchval(
            "SELECT claim_connector_backfill_jobs_for('twitter_x', $1::int)",
            limit,
        )
    jobs = _json(raw) or []
    if not isinstance(jobs, list):
        jobs = []
    for job in jobs:
        await process_twitter_x_backfill_job(pool, job)
    return len(jobs)
