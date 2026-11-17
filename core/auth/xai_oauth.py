"""xAI (Grok) OAuth: device-code sign-in with a SuperGrok / X account.

Standard RFC 8628 device flow against xAI's shared OAuth client (the one the
Grok CLI and OpenClaw use). The access token is used as an API key against
https://api.x.ai/v1. Endpoints come from xAI's OpenID discovery document and
must stay on x.ai.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from core.auth.utils import _b64url_decode
from core.integration_reliability import IntegrationHttpError, format_provider_error, request_json

XAI_OAUTH_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
XAI_OAUTH_SCOPE = "openid profile email offline_access grok-cli:access api:access"
XAI_OAUTH_DISCOVERY_URL = "https://auth.x.ai/.well-known/openid-configuration"
XAI_DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
XAI_DEVICE_EXPIRES_SECONDS = 300

XAI_OAUTH_CONFIG_KEY = "oauth.xai"


@dataclass(frozen=True)
class XaiCredentials:
    access: str
    refresh: str
    expires_ms: int
    token_endpoint: str


@dataclass(frozen=True)
class XaiDeviceCode:
    device_code: str
    user_code: str
    verification_url: str
    interval: int
    expires_in: int
    token_endpoint: str


def _trusted(url: Any, label: str) -> str:
    parsed = urlparse(url if isinstance(url, str) else "")
    host = parsed.hostname or ""
    if parsed.scheme == "https" and (host == "x.ai" or host.endswith(".x.ai")):
        return url
    raise RuntimeError(f"xAI OAuth discovery returned an untrusted {label}.")


def _expires_ms(data: dict[str, Any], access: str) -> int:
    expires_in = data.get("expires_in")
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        return int(time.time() * 1000 + expires_in * 1000)
    try:
        exp = json.loads(_b64url_decode(access.split(".")[1])).get("exp")
        if isinstance(exp, (int, float)):
            return int(exp * 1000)
    except Exception:
        pass
    return int(time.time() * 1000 + 3600 * 1000)


async def _discover() -> dict[str, str]:
    try:
        doc = await request_json("xai_oauth", "GET", XAI_OAUTH_DISCOVERY_URL, timeout=30.0, attempts=3, max_delay=10.0)
    except IntegrationHttpError as exc:
        raise RuntimeError(format_provider_error("xAI OAuth discovery", exc)) from exc
    if not isinstance(doc, dict):
        raise RuntimeError("xAI OAuth discovery returned no document.")
    return {
        "device": _trusted(doc.get("device_authorization_endpoint"), "device authorization endpoint"),
        "token": _trusted(doc.get("token_endpoint"), "token endpoint"),
    }


async def request_device_code() -> XaiDeviceCode:
    """Ask xAI for a one-time code the user enters at the verification page."""
    endpoints = await _discover()
    try:
        data = await request_json(
            "xai_oauth", "POST", endpoints["device"],
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            data={"client_id": XAI_OAUTH_CLIENT_ID, "scope": XAI_OAUTH_SCOPE},
            timeout=30.0, attempts=3, max_delay=10.0, retry_unsafe_methods=True,
        )
    except IntegrationHttpError as exc:
        raise RuntimeError(format_provider_error("xAI device code", exc)) from exc
    device_code = data.get("device_code") if isinstance(data, dict) else None
    user_code = data.get("user_code") if isinstance(data, dict) else None
    verification = (data.get("verification_uri_complete") or data.get("verification_uri")) if isinstance(data, dict) else None
    if not isinstance(device_code, str) or not isinstance(user_code, str) or not device_code or not user_code:
        raise RuntimeError("xAI device code response was missing fields.")
    try:
        interval = int(data.get("interval") or 5)
        expires_in = int(data.get("expires_in") or XAI_DEVICE_EXPIRES_SECONDS)
    except (TypeError, ValueError):
        interval, expires_in = 5, XAI_DEVICE_EXPIRES_SECONDS
    return XaiDeviceCode(
        device_code=device_code,
        user_code=user_code,
        verification_url=_trusted(verification, "verification URL"),
        interval=max(1, interval),
        expires_in=max(60, expires_in),
        token_endpoint=endpoints["token"],
    )


class SlowDown(Exception):
    """xAI asked to poll less often."""


class SignInEnded(RuntimeError):
    """The user denied the sign-in or the code expired; polling again cannot help."""


async def poll_device_code(device: XaiDeviceCode) -> XaiCredentials | None:
    """One poll: ``None`` while the user has not approved yet, credentials once they have."""
    try:
        data = await request_json(
            "xai_oauth", "POST", device.token_endpoint,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            data={"grant_type": XAI_DEVICE_GRANT_TYPE, "client_id": XAI_OAUTH_CLIENT_ID, "device_code": device.device_code},
            timeout=30.0, attempts=1,
        )
    except IntegrationHttpError as exc:
        try:
            error = json.loads(exc.response_body or "{}").get("error")
        except Exception:
            error = None
        if error == "authorization_pending":
            return None
        if error == "slow_down":
            raise SlowDown() from exc
        if error in ("access_denied", "authorization_denied"):
            raise SignInEnded("xAI sign-in was denied.") from exc
        if error == "expired_token":
            raise SignInEnded("The xAI login code expired.") from exc
        raise RuntimeError(format_provider_error("xAI device authorization", exc)) from exc
    return _credentials(data, device.token_endpoint, require_refresh=True)


def _credentials(data: Any, token_endpoint: str, *, require_refresh: bool, previous_refresh: str = "") -> XaiCredentials:
    access = data.get("access_token") if isinstance(data, dict) else None
    refresh = (data.get("refresh_token") if isinstance(data, dict) else None) or previous_refresh
    if not isinstance(access, str) or not access:
        raise RuntimeError("xAI OAuth response was missing the access token.")
    if require_refresh and not refresh:
        raise RuntimeError("xAI OAuth response was missing the refresh token.")
    return XaiCredentials(access, refresh or "", _expires_ms(data, access), token_endpoint)


async def refresh_xai_token(creds: XaiCredentials) -> XaiCredentials:
    token_endpoint = _trusted(creds.token_endpoint, "token endpoint") if creds.token_endpoint else (await _discover())["token"]
    try:
        data = await request_json(
            "xai_oauth", "POST", token_endpoint,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            data={"grant_type": "refresh_token", "client_id": XAI_OAUTH_CLIENT_ID, "refresh_token": creds.refresh},
            timeout=30.0, attempts=1,
        )
    except IntegrationHttpError as exc:
        raise RuntimeError(format_provider_error("xAI OAuth refresh", exc)) from exc
    return _credentials(data, token_endpoint, require_refresh=False, previous_refresh=creds.refresh)


# ---------------------------------------------------------------------------
# Persistence (filesystem auth store, shared by the API and workers)
# ---------------------------------------------------------------------------

def load_xai_credentials() -> XaiCredentials | None:
    from core.auth.store import load_auth

    value = load_auth(XAI_OAUTH_CONFIG_KEY)
    if not isinstance(value, dict):
        return None
    access, refresh, expires = value.get("access"), value.get("refresh"), value.get("expires_ms")
    if not isinstance(access, str) or not isinstance(refresh, str) or not isinstance(expires, (int, float)):
        return None
    return XaiCredentials(access, refresh, int(expires), str(value.get("token_endpoint") or ""))


def save_xai_credentials(creds: XaiCredentials) -> None:
    from core.auth.store import save_auth

    save_auth(XAI_OAUTH_CONFIG_KEY, {
        "access": creds.access, "refresh": creds.refresh,
        "expires_ms": int(creds.expires_ms), "token_endpoint": creds.token_endpoint,
    })


async def _refresh_under_lock(*, skew_seconds: int = 300) -> XaiCredentials:
    from core.auth.store import auth_lock

    with auth_lock(XAI_OAUTH_CONFIG_KEY):
        creds = load_xai_credentials()
        if not creds:
            raise RuntimeError("xAI OAuth is not configured. Run: `hexis auth xai-oauth login`")
        if creds.expires_ms > int(time.time() * 1000) + skew_seconds * 1000:
            return creds
        refreshed = await refresh_xai_token(creds)
        save_xai_credentials(refreshed)
        return refreshed


async def ensure_fresh_xai_credentials(*, skew_seconds: int = 300) -> XaiCredentials:
    """Serialize refreshes across API/workers without blocking their event loops."""
    import asyncio
    return await asyncio.to_thread(lambda: asyncio.run(_refresh_under_lock(skew_seconds=skew_seconds)))


def delete_xai_credentials() -> None:
    from core.auth.store import delete_auth

    delete_auth(XAI_OAUTH_CONFIG_KEY)


# Names the generic `hexis auth <provider>` status/logout commands expect.
load_credentials = load_xai_credentials
save_credentials = save_xai_credentials
delete_credentials = delete_xai_credentials
ensure_fresh_credentials = ensure_fresh_xai_credentials
