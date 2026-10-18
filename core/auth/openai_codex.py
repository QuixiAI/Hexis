"""OpenAI Codex (ChatGPT subscription) OAuth flow.

Moved from ``core/openai_codex_oauth.py``; now uses shared auth utils.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.auth.utils import _b64url_decode, create_state, generate_pkce  # noqa: F401 – re-exported
from core.integration_reliability import (
    IntegrationHttpError,
    format_provider_error,
    request_json,
)

# ---------------------------------------------------------------------------
# Constants (mirrored from OpenClaw/pi-ai)
# ---------------------------------------------------------------------------

OPENAI_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_CODEX_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
OPENAI_CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_CODEX_REDIRECT_URI = "http://localhost:1455/auth/callback"
OPENAI_CODEX_MODELS_URL = "https://chatgpt.com/backend-api/codex/models?client_version=1.0.0"
OPENAI_CODEX_SCOPE = "openid profile email offline_access"
OPENAI_CODEX_ORIGINATOR = "pi"

OPENAI_AUTH_JWT_CLAIM_PATH = "https://api.openai.com/auth"

# Device-code login: the user types a short code at OpenAI, so no browser
# callback has to reach this machine (works in Docker and over SSH).
OPENAI_CODEX_DEVICE_USERCODE_URL = "https://auth.openai.com/api/accounts/deviceauth/usercode"
OPENAI_CODEX_DEVICE_TOKEN_URL = "https://auth.openai.com/api/accounts/deviceauth/token"
OPENAI_CODEX_DEVICE_VERIFICATION_URL = "https://auth.openai.com/codex/device"
OPENAI_CODEX_DEVICE_REDIRECT_URI = "https://auth.openai.com/deviceauth/callback"
OPENAI_CODEX_DEVICE_EXPIRES_SECONDS = 900

OPENAI_CODEX_OAUTH_CONFIG_KEY = "oauth.openai_codex"

_OPENAI_CODEX_OAUTH_LOCK_KEY = __import__("zlib").crc32(
    OPENAI_CODEX_OAUTH_CONFIG_KEY.encode("utf-8")
)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OpenAICodexCredentials:
    access: str
    refresh: str
    expires_ms: int
    account_id: str


# ---------------------------------------------------------------------------
# URL / JWT helpers
# ---------------------------------------------------------------------------

def build_authorize_url(
    *,
    challenge: str,
    state: str,
    redirect_uri: str = OPENAI_CODEX_REDIRECT_URI,
    originator: str = OPENAI_CODEX_ORIGINATOR,
) -> str:
    from urllib.parse import urlencode

    params = {
        "response_type": "code",
        "client_id": OPENAI_CODEX_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": OPENAI_CODEX_SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": originator,
    }
    return f"{OPENAI_CODEX_AUTHORIZE_URL}?{urlencode(params)}"


def parse_authorization_input(value: str) -> tuple[str | None, str | None]:
    """Accept full redirect URL, ``code#state``, querystring, or raw code."""
    v = (value or "").strip()
    if not v:
        return None, None

    try:
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(v)
        if parsed.scheme and parsed.netloc:
            qs = parse_qs(parsed.query)
            code = (qs.get("code") or [None])[0]
            state = (qs.get("state") or [None])[0]
            return code, state
    except Exception:
        pass

    if "#" in v:
        code, st = v.split("#", 1)
        return code or None, st or None

    if "code=" in v:
        try:
            from urllib.parse import parse_qs

            qs = parse_qs(v)
            code = (qs.get("code") or [None])[0]
            state = (qs.get("state") or [None])[0]
            return code, state
        except Exception:
            pass

    return v, None


def decode_jwt_payload(token: str) -> dict[str, Any] | None:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = _b64url_decode(parts[1])
        return json.loads(payload.decode("utf-8"))
    except Exception:
        return None


def extract_account_id(access_token: str) -> str | None:
    payload = decode_jwt_payload(access_token)
    auth = payload.get(OPENAI_AUTH_JWT_CLAIM_PATH) if isinstance(payload, dict) else None
    account_id = auth.get("chatgpt_account_id") if isinstance(auth, dict) else None
    return account_id if isinstance(account_id, str) and account_id else None


# ---------------------------------------------------------------------------
# Token exchange / refresh
# ---------------------------------------------------------------------------

async def exchange_authorization_code(
    *,
    code: str,
    verifier: str,
    redirect_uri: str = OPENAI_CODEX_REDIRECT_URI,
) -> OpenAICodexCredentials:
    try:
        data = await request_json(
            "openai_codex_oauth",
            "POST",
            OPENAI_CODEX_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "authorization_code",
                "client_id": OPENAI_CODEX_CLIENT_ID,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
            },
            timeout=30.0,
            attempts=3,
            max_delay=10.0,
            retry_unsafe_methods=True,
        )
    except IntegrationHttpError as exc:
        raise RuntimeError(format_provider_error("OpenAI Codex token exchange", exc)) from exc
    access = data.get("access_token")
    refresh = data.get("refresh_token")
    expires_in = data.get("expires_in")
    if not isinstance(access, str) or not isinstance(refresh, str) or not isinstance(expires_in, (int, float)):
        raise RuntimeError("OpenAI Codex token exchange failed: missing fields in response.")

    account_id = extract_account_id(access)
    if not account_id:
        raise RuntimeError("OpenAI Codex token exchange failed: could not extract account id from token.")

    now_ms = int(time.time() * 1000)
    return OpenAICodexCredentials(
        access=access,
        refresh=refresh,
        expires_ms=now_ms + int(expires_in * 1000),
        account_id=account_id,
    )


@dataclass(frozen=True)
class DeviceCode:
    device_auth_id: str
    user_code: str
    interval: int


async def request_device_code() -> DeviceCode:
    """Ask OpenAI for a one-time code the user enters at the verification page."""
    try:
        data = await request_json(
            "openai_codex_oauth",
            "POST",
            OPENAI_CODEX_DEVICE_USERCODE_URL,
            json_body={"client_id": OPENAI_CODEX_CLIENT_ID},
            timeout=30.0,
            attempts=3,
            max_delay=10.0,
            retry_unsafe_methods=True,
        )
    except IntegrationHttpError as exc:
        raise RuntimeError(format_provider_error("OpenAI Codex device code", exc)) from exc
    device_auth_id = data.get("device_auth_id") if isinstance(data, dict) else None
    user_code = (data.get("user_code") or data.get("usercode")) if isinstance(data, dict) else None
    if not isinstance(device_auth_id, str) or not isinstance(user_code, str) or not device_auth_id or not user_code:
        raise RuntimeError("OpenAI Codex device code response was missing fields.")
    try:
        interval = int(data.get("interval") or 5)
    except (TypeError, ValueError):
        interval = 5
    return DeviceCode(device_auth_id, user_code, max(3, interval))


async def poll_device_code(device: DeviceCode) -> OpenAICodexCredentials | None:
    """One poll: ``None`` while the user has not approved yet, credentials once they have."""
    try:
        data = await request_json(
            "openai_codex_oauth",
            "POST",
            OPENAI_CODEX_DEVICE_TOKEN_URL,
            json_body={"device_auth_id": device.device_auth_id, "user_code": device.user_code},
            timeout=30.0,
            attempts=1,
        )
    except IntegrationHttpError as exc:
        # OpenAI answers 403/404 until the code has been entered and approved.
        if exc.status_code in (403, 404):
            return None
        raise RuntimeError(format_provider_error("OpenAI Codex device authorization", exc)) from exc
    code = data.get("authorization_code") if isinstance(data, dict) else None
    verifier = data.get("code_verifier") if isinstance(data, dict) else None
    if not isinstance(code, str) or not isinstance(verifier, str) or not code or not verifier:
        raise RuntimeError("OpenAI Codex device authorization response was missing fields.")
    return await exchange_authorization_code(
        code=code, verifier=verifier, redirect_uri=OPENAI_CODEX_DEVICE_REDIRECT_URI,
    )


async def refresh_openai_codex_token(refresh_token: str) -> OpenAICodexCredentials:
    try:
        data = await request_json(
            "openai_codex_oauth",
            "POST",
            OPENAI_CODEX_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": OPENAI_CODEX_CLIENT_ID,
            },
            timeout=30.0,
            attempts=3,
            max_delay=10.0,
            retry_unsafe_methods=True,
        )
    except IntegrationHttpError as exc:
        raise RuntimeError(format_provider_error("OpenAI Codex token refresh", exc)) from exc
    access = data.get("access_token")
    refresh = data.get("refresh_token")
    expires_in = data.get("expires_in")
    if not isinstance(access, str) or not isinstance(refresh, str) or not isinstance(expires_in, (int, float)):
        raise RuntimeError("OpenAI Codex token refresh failed: missing fields in response.")

    account_id = extract_account_id(access)
    if not account_id:
        raise RuntimeError("OpenAI Codex token refresh failed: could not extract account id from token.")

    now_ms = int(time.time() * 1000)
    return OpenAICodexCredentials(
        access=access,
        refresh=refresh,
        expires_ms=now_ms + int(expires_in * 1000),
        account_id=account_id,
    )


async def list_openai_codex_models(
    creds: OpenAICodexCredentials | None = None,
) -> list[str]:
    """List picker-visible models from the authenticated Codex workspace."""
    if creds is None:
        creds = await ensure_fresh_openai_codex_credentials(skew_seconds=0)
    try:
        data = await request_json(
            "openai_codex",
            "GET",
            OPENAI_CODEX_MODELS_URL,
            headers={
                "Authorization": f"Bearer {creds.access}",
                "ChatGPT-Account-ID": creds.account_id,
                "Accept": "application/json",
            },
            timeout=30.0,
            attempts=3,
            max_delay=10.0,
        )
    except IntegrationHttpError as exc:
        raise RuntimeError(format_provider_error("OpenAI Codex model discovery", exc)) from exc
    rows = data.get("models") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("OpenAI Codex model discovery response was missing models.")

    models: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("visibility") or "list").lower() != "list":
            continue
        if row.get("show_in_picker") is False or row.get("supported_in_api") is False:
            continue
        model = row.get("slug") or row.get("id")
        if isinstance(model, str) and model and model not in seen:
            seen.add(model)
            models.append(model)
    if not models:
        raise RuntimeError("OpenAI Codex model discovery returned no usable models.")
    return models


# ---------------------------------------------------------------------------
# Persistence (filesystem – survives DB resets)
# ---------------------------------------------------------------------------

def credentials_to_dict(creds: OpenAICodexCredentials) -> dict[str, Any]:
    return {
        "access": creds.access,
        "refresh": creds.refresh,
        "expires_ms": int(creds.expires_ms),
        "account_id": creds.account_id,
    }


def credentials_from_value(value: Any) -> OpenAICodexCredentials | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return None
    if not isinstance(value, dict):
        return None

    access = value.get("access")
    refresh = value.get("refresh")
    expires_ms = value.get("expires_ms") or value.get("expires")
    account_id = value.get("account_id") or value.get("accountId")

    if not isinstance(access, str) or not isinstance(refresh, str):
        return None
    if not isinstance(expires_ms, (int, float)):
        return None
    if not isinstance(account_id, str) or not account_id:
        account_id = extract_account_id(access) or ""
    if not account_id:
        return None

    return OpenAICodexCredentials(
        access=access,
        refresh=refresh,
        expires_ms=int(expires_ms),
        account_id=account_id,
    )


def load_openai_codex_credentials() -> OpenAICodexCredentials | None:
    from core.auth.store import load_auth
    return credentials_from_value(load_auth(OPENAI_CODEX_OAUTH_CONFIG_KEY))


# LiteLLM's chatgpt/ provider makes the calls. It reads its tokens from its own
# file and, finding none or an expired one, refreshes it or starts an
# interactive login of its own, neither of which is safe here (no lock across
# the API and workers; nobody to see a login code). So this module stays the
# owner: every save and refresh also writes LiteLLM's file, and calls refresh
# here first, with a wider margin than LiteLLM's 60 seconds.

def litellm_chatgpt_dir() -> Path:
    from core.auth import store

    return Path(os.environ.get("CHATGPT_TOKEN_DIR") or store.AUTH_DIR / "litellm-chatgpt")


def _write_litellm_tokens(creds: OpenAICodexCredentials) -> None:
    import tempfile

    directory = litellm_chatgpt_dir()
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    data = {
        "access_token": creds.access,
        "refresh_token": creds.refresh,
        "account_id": creds.account_id,
        "expires_at": int(creds.expires_ms // 1000),
    }
    target = directory / "auth.json"
    try:
        if json.loads(target.read_text()) == data:
            return  # rewriting an unchanged file on every call let parallel readers miss it
    except (OSError, ValueError):
        pass
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save_openai_codex_credentials(creds: OpenAICodexCredentials) -> None:
    from core.auth.store import save_auth
    save_auth(OPENAI_CODEX_OAUTH_CONFIG_KEY, credentials_to_dict(creds))
    _write_litellm_tokens(creds)


def delete_openai_codex_credentials() -> None:
    from core.auth.store import delete_auth
    delete_auth(OPENAI_CODEX_OAUTH_CONFIG_KEY)
    (litellm_chatgpt_dir() / "auth.json").unlink(missing_ok=True)


async def _refresh_under_lock(*, skew_seconds: int = 300) -> OpenAICodexCredentials:
    """Return valid credentials, refreshing under file lock if needed."""
    from core.auth.store import auth_lock

    with auth_lock(OPENAI_CODEX_OAUTH_CONFIG_KEY):
        creds = load_openai_codex_credentials()
        if not creds:
            raise RuntimeError(
                "OpenAI Codex OAuth is not configured. Run: `hexis auth openai-codex login`"
            )

        now_ms = int(time.time() * 1000)
        if creds.expires_ms > now_ms + skew_seconds * 1000:
            # Keep LiteLLM's copy current even if it was never written (an
            # install from before LiteLLM) or was changed underneath us.
            _write_litellm_tokens(creds)
            return creds

        refreshed = await refresh_openai_codex_token(creds.refresh)
        save_openai_codex_credentials(refreshed)
        return refreshed


async def ensure_fresh_openai_codex_credentials(
    *,
    skew_seconds: int = 300,
) -> OpenAICodexCredentials:
    """Serialize refreshes across the API and workers without blocking their event loops.

    The file lock is blocking, so it is taken on a worker thread with its own
    event loop for the refresh request.
    """
    import asyncio

    return await asyncio.to_thread(
        lambda: asyncio.run(_refresh_under_lock(skew_seconds=skew_seconds))
    )
