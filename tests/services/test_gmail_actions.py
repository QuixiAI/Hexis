from __future__ import annotations

import base64

import pytest

from core.auth.google_gmail import GMAIL_DEFAULT_CREDENTIAL_REF
from core.auth.store import save_auth
from core.auth.utils import now_ms
from services import gmail_actions

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


def _decode_raw(raw: str) -> str:
    padded = raw + "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")


def _save_credentials(account: str, scopes: list[str]) -> None:
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
            "scopes": scopes,
        },
    )


async def test_send_gmail_message_posts_rfc822_raw(monkeypatch, tmp_path):
    import core.auth.store as auth_store

    monkeypatch.setattr(auth_store, "AUTH_DIR", tmp_path / "auth")
    _save_credentials("eric@example.com", [gmail_actions.SCOPE_SEND])

    async def fake_request(credentials, method, path, *, json_body=None):
        assert method == "POST"
        assert path == "/users/me/messages/send"
        decoded = _decode_raw(json_body["raw"])
        assert "From: eric@example.com" in decoded
        assert "To: alice@example.com" in decoded
        assert "Subject: Hello" in decoded
        assert "Body text" in decoded
        return {"id": "sent-1", "threadId": "thread-1"}

    monkeypatch.setattr(gmail_actions, "_gmail_request", fake_request)

    result = await gmail_actions.send_gmail_message(
        account_key="eric@example.com",
        to="alice@example.com",
        subject="Hello",
        body="Body text",
    )

    assert result["sent"] is True
    assert result["message_id"] == "sent-1"
    assert result["thread_id"] == "thread-1"


async def test_reply_gmail_message_sends_inside_thread(monkeypatch, tmp_path):
    import core.auth.store as auth_store

    monkeypatch.setattr(auth_store, "AUTH_DIR", tmp_path / "auth")
    _save_credentials("eric@example.com", [gmail_actions.SCOPE_SEND])

    async def fake_request(credentials, method, path, *, json_body=None):
        assert path == "/users/me/messages/send"
        assert json_body["threadId"] == "thread-1"
        decoded = _decode_raw(json_body["raw"])
        assert "In-Reply-To: <original@example.com>" in decoded
        return {"id": "reply-1", "threadId": "thread-1"}

    monkeypatch.setattr(gmail_actions, "_gmail_request", fake_request)

    result = await gmail_actions.reply_gmail_message(
        account_key="eric@example.com",
        thread_id="thread-1",
        to="alice@example.com",
        subject="Re: Hello",
        body="No, thank you.",
        in_reply_to="<original@example.com>",
    )

    assert result["message_id"] == "reply-1"
    assert result["thread_id"] == "thread-1"


async def test_modify_and_spam_triage_require_modify_scope(monkeypatch, tmp_path):
    import core.auth.store as auth_store

    monkeypatch.setattr(auth_store, "AUTH_DIR", tmp_path / "auth")
    _save_credentials("eric@example.com", [gmail_actions.SCOPE_MODIFY])
    calls = []

    async def fake_request(credentials, method, path, *, json_body=None):
        calls.append((method, path, json_body))
        return {"id": "msg-1", "threadId": "thread-1", "labelIds": json_body["addLabelIds"]}

    monkeypatch.setattr(gmail_actions, "_gmail_request", fake_request)

    labeled = await gmail_actions.modify_gmail_labels(
        account_key="eric@example.com",
        message_id="msg-1",
        add_label_ids=["IMPORTANT"],
        remove_label_ids=["UNREAD"],
    )
    spam = await gmail_actions.triage_gmail_spam(
        account_key="eric@example.com",
        message_id="msg-1",
        action="mark_spam",
    )

    assert labeled["add_label_ids"] == ["IMPORTANT"]
    assert spam["add_label_ids"] == ["SPAM"]
    assert calls[0][1] == "/users/me/messages/msg-1/modify"
    assert calls[1][2] == {"addLabelIds": ["SPAM"], "removeLabelIds": ["INBOX"]}


async def test_delete_gmail_message_trashes_by_default(monkeypatch, tmp_path):
    import core.auth.store as auth_store

    monkeypatch.setattr(auth_store, "AUTH_DIR", tmp_path / "auth")
    _save_credentials("eric@example.com", [gmail_actions.SCOPE_MODIFY])

    async def fake_request(credentials, method, path, *, json_body=None):
        assert method == "POST"
        assert path == "/users/me/messages/msg-1/trash"
        assert json_body is None
        return {"id": "msg-1", "threadId": "thread-1", "labelIds": ["TRASH"]}

    monkeypatch.setattr(gmail_actions, "_gmail_request", fake_request)

    result = await gmail_actions.delete_gmail_message(
        account_key="eric@example.com",
        message_id="msg-1",
    )

    assert result["deleted"] is True
    assert result["permanent"] is False
    assert result["labels"] == ["TRASH"]


async def test_delete_gmail_message_can_permanently_delete(monkeypatch, tmp_path):
    import core.auth.store as auth_store

    monkeypatch.setattr(auth_store, "AUTH_DIR", tmp_path / "auth")
    _save_credentials("eric@example.com", [gmail_actions.SCOPE_MODIFY])

    async def fake_request(credentials, method, path, *, json_body=None):
        assert method == "DELETE"
        assert path == "/users/me/messages/msg-1"
        assert json_body is None
        return {}

    monkeypatch.setattr(gmail_actions, "_gmail_request", fake_request)

    result = await gmail_actions.delete_gmail_message(
        account_key="eric@example.com",
        message_id="msg-1",
        permanent=True,
    )

    assert result["deleted"] is True
    assert result["permanent"] is True


async def test_create_gmail_draft_posts_rfc822_draft_with_compose_or_modify_scope(monkeypatch, tmp_path):
    import core.auth.store as auth_store

    monkeypatch.setattr(auth_store, "AUTH_DIR", tmp_path / "auth")
    _save_credentials("eric@example.com", [gmail_actions.SCOPE_COMPOSE])

    async def fake_request(credentials, method, path, *, json_body=None):
        assert method == "POST"
        assert path == "/users/me/drafts"
        assert json_body["message"]["threadId"] == "thread-42"
        decoded = _decode_raw(json_body["message"]["raw"])
        assert "From: eric@example.com" in decoded
        assert "To: recruiter@example.com" in decoded
        assert "Subject: Re: Opportunity" in decoded
        assert "In-Reply-To: <orig-42@example.com>" in decoded
        assert "No, thank you." in decoded
        return {"id": "draft-1", "message": {"id": "msg-draft-1", "threadId": "thread-42"}}

    monkeypatch.setattr(gmail_actions, "_gmail_request", fake_request)

    result = await gmail_actions.create_gmail_draft(
        account_key="eric@example.com",
        to="recruiter@example.com",
        subject="Re: Opportunity",
        body="No, thank you.",
        thread_id="thread-42",
        in_reply_to="<orig-42@example.com>",
    )

    assert result["drafted"] is True
    assert result["draft_id"] == "draft-1"
    assert result["message_id"] == "msg-draft-1"
    assert result["thread_id"] == "thread-42"

    # gmail.send alone does not grant /users/me/drafts; must fail with clear scope error.
    _save_credentials("eric@example.com", [gmail_actions.SCOPE_SEND])
    with pytest.raises(gmail_actions.GmailActionError, match="do not include draft"):
        await gmail_actions.create_gmail_draft(
            account_key="eric@example.com",
            to="recruiter@example.com",
            subject="Re: Opportunity",
            body="No, thank you.",
        )


async def test_gmail_draft_tool_is_registered_for_heartbeat_and_chat():
    from core.tools.base import ToolContext
    from core.tools.registry import create_default_registry

    registry = create_default_registry(pool=None)
    handler = registry.get("gmail_draft")
    assert handler is not None
    assert handler.spec.requires_approval is True
    assert ToolContext.HEARTBEAT in handler.spec.allowed_contexts
    assert ToolContext.CHAT in handler.spec.allowed_contexts
