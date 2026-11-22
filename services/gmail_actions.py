"""Gmail provider effect helpers.

Authorization policy is enforced by the DB-owned tool runtime before these
helpers run. This module only performs Gmail API side effects with the saved
Hexis OAuth credential.
"""

from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any

from core.auth.google_gmail import (
    refresh_default_credentials_if_needed,
)
from core.integration_reliability import (
    IntegrationHttpError,
    format_provider_error,
    request_json,
)

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"
SCOPE_SEND = "https://www.googleapis.com/auth/gmail.send"
SCOPE_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
SCOPE_MODIFY = "https://www.googleapis.com/auth/gmail.modify"
DRAFT_SCOPES = {SCOPE_COMPOSE, SCOPE_MODIFY, "https://mail.google.com/"}


class GmailActionError(RuntimeError):
    """Expected Gmail action failure with a user-actionable message."""


def _scope_set(credentials: dict[str, Any]) -> set[str]:
    return {str(scope) for scope in credentials.get("scopes") or []}


def _require_scope(credentials: dict[str, Any], scope: str, capability: str) -> None:
    if scope not in _scope_set(credentials):
        raise GmailActionError(
            f"Saved Gmail credentials do not include {capability}. "
            f"Reconnect Gmail with the {capability} capability before using this action."
        )


def _require_any_scope(credentials: dict[str, Any], scopes: set[str], capability: str) -> None:
    if not scopes.intersection(_scope_set(credentials)):
        raise GmailActionError(
            f"Saved Gmail credentials do not include {capability}. "
            f"Reconnect Gmail with the {capability} capability before using this action."
        )


def _check_account(credentials: dict[str, Any], account_key: str | None) -> str | None:
    saved = credentials.get("account_email")
    saved_email = saved.strip().lower() if isinstance(saved, str) and saved.strip() else None
    requested = account_key.strip().lower() if isinstance(account_key, str) and account_key.strip() else None
    if saved_email and requested and saved_email != requested:
        raise GmailActionError(
            f"Saved Gmail credentials are for {saved_email}, but this action requested {requested}."
        )
    return requested or saved_email


async def _gmail_request(
    credentials: dict[str, Any],
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    token = credentials.get("token")
    if not isinstance(token, str) or not token:
        raise GmailActionError("Saved Gmail credentials are missing an access token.")
    url = path if path.startswith("http") else f"{GMAIL_API_BASE}{path}"
    try:
        payload = await request_json(
            "gmail",
            method.upper(),
            url,
            headers={"Authorization": f"Bearer {token}"},
            json_body=json_body,
            timeout=30.0,
            attempts=3,
            max_delay=20.0,
            retry_unsafe_methods=False,
        )
    except IntegrationHttpError as exc:
        raise GmailActionError(format_provider_error("Gmail", exc)) from exc
    if not isinstance(payload, dict):
        raise GmailActionError("Gmail API returned an invalid payload.")
    return payload


def _split_addresses(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _raw_message(
    *,
    from_email: str | None,
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    bcc: str | None = None,
    reply_to: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> str:
    message = EmailMessage()
    if from_email:
        message["From"] = from_email
    message["To"] = to
    if cc:
        message["Cc"] = cc
    if bcc:
        message["Bcc"] = bcc
    if reply_to:
        message["Reply-To"] = reply_to
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = references
    message["Subject"] = subject
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")


async def send_gmail_message(
    *,
    account_key: str | None,
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    bcc: str | None = None,
    reply_to: str | None = None,
) -> dict[str, Any]:
    credentials = await refresh_default_credentials_if_needed()
    _require_scope(credentials, SCOPE_SEND, "send")
    account = _check_account(credentials, account_key)
    raw = _raw_message(
        from_email=account,
        to=to,
        subject=subject,
        body=body,
        cc=cc,
        bcc=bcc,
        reply_to=reply_to,
    )
    sent = await _gmail_request(credentials, "POST", "/users/me/messages/send", json_body={"raw": raw})
    return {
        "sent": True,
        "connector_id": "gmail",
        "account_key": account,
        "message_id": sent.get("id"),
        "thread_id": sent.get("threadId"),
        "to": _split_addresses(to),
        "cc": _split_addresses(cc),
        "subject": subject,
    }


async def reply_gmail_message(
    *,
    account_key: str | None,
    thread_id: str,
    to: str,
    subject: str,
    body: str,
    in_reply_to: str | None = None,
    references: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
) -> dict[str, Any]:
    credentials = await refresh_default_credentials_if_needed()
    _require_scope(credentials, SCOPE_SEND, "reply")
    account = _check_account(credentials, account_key)
    raw = _raw_message(
        from_email=account,
        to=to,
        subject=subject,
        body=body,
        cc=cc,
        bcc=bcc,
        in_reply_to=in_reply_to,
        references=references,
    )
    sent = await _gmail_request(
        credentials,
        "POST",
        "/users/me/messages/send",
        json_body={"raw": raw, "threadId": thread_id},
    )
    return {
        "sent": True,
        "connector_id": "gmail",
        "account_key": account,
        "message_id": sent.get("id"),
        "thread_id": sent.get("threadId") or thread_id,
        "to": _split_addresses(to),
        "cc": _split_addresses(cc),
        "subject": subject,
    }


async def create_gmail_draft(
    *,
    account_key: str | None,
    to: str,
    subject: str,
    body: str,
    thread_id: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
) -> dict[str, Any]:
    """Create a reversible Gmail draft (standalone or reply inside a thread) without sending."""
    credentials = await refresh_default_credentials_if_needed()
    _require_any_scope(credentials, DRAFT_SCOPES, "draft")
    account = _check_account(credentials, account_key)
    raw = _raw_message(
        from_email=account,
        to=to,
        subject=subject,
        body=body,
        cc=cc,
        bcc=bcc,
        in_reply_to=in_reply_to,
        references=references,
    )
    message_payload: dict[str, Any] = {"raw": raw}
    if thread_id and thread_id.strip():
        message_payload["threadId"] = thread_id.strip()
    created = await _gmail_request(
        credentials,
        "POST",
        "/users/me/drafts",
        json_body={"message": message_payload},
    )
    draft_message = created.get("message") if isinstance(created.get("message"), dict) else {}
    return {
        "drafted": True,
        "connector_id": "gmail",
        "account_key": account,
        "draft_id": created.get("id"),
        "message_id": draft_message.get("id"),
        "thread_id": draft_message.get("threadId") or (thread_id.strip() if thread_id else None),
        "to": _split_addresses(to),
        "cc": _split_addresses(cc),
        "subject": subject,
    }


async def modify_gmail_labels(
    *,
    account_key: str | None,
    message_id: str,
    add_label_ids: list[str] | None = None,
    remove_label_ids: list[str] | None = None,
) -> dict[str, Any]:
    credentials = await refresh_default_credentials_if_needed()
    _require_scope(credentials, SCOPE_MODIFY, "label")
    account = _check_account(credentials, account_key)
    payload = {
        "addLabelIds": [label for label in (add_label_ids or []) if str(label).strip()],
        "removeLabelIds": [label for label in (remove_label_ids or []) if str(label).strip()],
    }
    modified = await _gmail_request(
        credentials,
        "POST",
        f"/users/me/messages/{message_id}/modify",
        json_body=payload,
    )
    return {
        "modified": True,
        "connector_id": "gmail",
        "account_key": account,
        "message_id": modified.get("id") or message_id,
        "thread_id": modified.get("threadId"),
        "labels": modified.get("labelIds") or [],
        "add_label_ids": payload["addLabelIds"],
        "remove_label_ids": payload["removeLabelIds"],
    }


async def triage_gmail_spam(
    *,
    account_key: str | None,
    message_id: str,
    action: str,
) -> dict[str, Any]:
    normalized = action.strip().lower()
    if normalized == "mark_spam":
        return await modify_gmail_labels(
            account_key=account_key,
            message_id=message_id,
            add_label_ids=["SPAM"],
            remove_label_ids=["INBOX"],
        )
    if normalized == "mark_not_spam":
        return await modify_gmail_labels(
            account_key=account_key,
            message_id=message_id,
            add_label_ids=["INBOX"],
            remove_label_ids=["SPAM"],
        )
    if normalized == "archive":
        return await modify_gmail_labels(
            account_key=account_key,
            message_id=message_id,
            add_label_ids=[],
            remove_label_ids=["INBOX"],
        )
    raise GmailActionError("spam triage action must be mark_spam, mark_not_spam, or archive.")


async def delete_gmail_message(
    *,
    account_key: str | None,
    message_id: str,
    permanent: bool = False,
) -> dict[str, Any]:
    """Trash a Gmail message by default, or permanently delete it when explicitly requested."""
    if not message_id.strip():
        raise GmailActionError("message_id is required.")
    credentials = await refresh_default_credentials_if_needed()
    _require_scope(credentials, SCOPE_MODIFY, "delete")
    account = _check_account(credentials, account_key)
    normalized_id = message_id.strip()
    if permanent:
        await _gmail_request(credentials, "DELETE", f"/users/me/messages/{normalized_id}")
        return {
            "deleted": True,
            "permanent": True,
            "connector_id": "gmail",
            "account_key": account,
            "message_id": normalized_id,
        }

    trashed = await _gmail_request(credentials, "POST", f"/users/me/messages/{normalized_id}/trash")
    return {
        "deleted": True,
        "permanent": False,
        "connector_id": "gmail",
        "account_key": account,
        "message_id": trashed.get("id") or normalized_id,
        "thread_id": trashed.get("threadId"),
        "labels": trashed.get("labelIds") or [],
    }
