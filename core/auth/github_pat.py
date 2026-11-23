"""GitHub personal access token auth module (#102).

Interim credential flow while a real first-class OAuth/device-code GitHub
connector isn't built yet -- the issue's own Non-Goals explicitly allow this:
"Do not require GitHub OAuth on day one if a fine-grained PAT bootstrap is
faster, but make the PAT path productized and resumable." The token is
collected via a terminal prompt (never chat) and verified against GitHub's
own API before being stored, mirroring core/auth/anthropic_setup_token.py.

Hexis manages this credential in its own store ONLY. It deliberately never
reads `gh auth token`, a `GITHUB_TOKEN`/`GITHUB_PERSONAL_ACCESS_TOKEN`
environment variable, or any other ambient credential -- the issue's other
explicit Non-Goal ("do not silently consume gh auth ... just because it
exists").
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

GITHUB_CONNECTOR_ID = "github_issues"
GITHUB_PAT_CONFIG_KEY = "token.github_pat"
GITHUB_FINE_GRAINED_PAT_PREFIX = "github_pat_"
GITHUB_CLASSIC_PAT_PREFIX = "ghp_"
GITHUB_PAT_CREATE_URL = "https://github.com/settings/personal-access-tokens/new"
GITHUB_DEFAULT_CAPABILITIES = ["search_issues", "read_issues", "create_issue"]


@dataclass(frozen=True)
class GithubPatCredentials:
    token: str
    login: str | None = None


def validate_pat_format(token: str) -> str | None:
    """Return an error message if the token is obviously malformed, else None."""
    if not token:
        return "Token is empty."
    if not (
        token.startswith(GITHUB_FINE_GRAINED_PAT_PREFIX)
        or token.startswith(GITHUB_CLASSIC_PAT_PREFIX)
    ):
        return (
            f"Token must start with '{GITHUB_FINE_GRAINED_PAT_PREFIX}' (fine-grained, "
            f"preferred) or '{GITHUB_CLASSIC_PAT_PREFIX}' (classic)."
        )
    return None


async def verify_pat(token: str) -> tuple[str | None, str | None]:
    """Call GitHub's own API to confirm the token actually works.

    Returns (login, error) -- exactly one is set. Runs the blocking HTTP call
    in a worker thread so it never stalls the asyncio event loop.
    """
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Hexis",
        },
    )

    def _fetch() -> tuple[dict[str, Any] | None, str | None]:
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8")), None
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                return None, "GitHub rejected the token (401 Unauthorized) — check it wasn't mistyped or revoked."
            return None, f"GitHub API error: HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001 -- network/DNS/timeout, report verbatim
            return None, f"Could not reach GitHub API: {exc}"

    data, fetch_error = await asyncio.to_thread(_fetch)
    if fetch_error:
        return None, fetch_error

    login = data.get("login") if isinstance(data, dict) else None
    if not login:
        return None, "GitHub API response did not include a login — unexpected response shape."
    return str(login), None


def credentials_to_dict(creds: GithubPatCredentials) -> dict[str, Any]:
    return {"token": creds.token, "login": creds.login}


def credentials_from_value(value: Any) -> GithubPatCredentials | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return None
    if not isinstance(value, dict):
        return None
    token = value.get("token")
    if not isinstance(token, str) or not token:
        return None
    login = value.get("login")
    return GithubPatCredentials(token=token, login=login if isinstance(login, str) else None)


def load_credentials() -> GithubPatCredentials | None:
    from core.auth.store import load_auth
    return credentials_from_value(load_auth(GITHUB_PAT_CONFIG_KEY))


def save_credentials(creds: GithubPatCredentials) -> None:
    from core.auth.store import save_auth
    save_auth(GITHUB_PAT_CONFIG_KEY, credentials_to_dict(creds))


def delete_credentials() -> None:
    from core.auth.store import delete_auth
    delete_auth(GITHUB_PAT_CONFIG_KEY)


def resolve_github_token() -> tuple[str | None, str]:
    """Resolve a GitHub token from Hexis's OWN store only.

    Populate the store with:
        hexis auth github setup-token     # paste a fine-grained or classic PAT

    Returns (token, login): login is the verified GitHub username the token
    was last confirmed against, or "" if nothing is stored. Never falls back
    to `gh auth token`, `GITHUB_TOKEN`, or `GITHUB_PERSONAL_ACCESS_TOKEN` from
    the process environment -- see the module docstring.
    """
    creds = load_credentials()
    if creds:
        return creds.token, (creds.login or "")
    return None, ""


def _coerce_json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


async def sync_github_connection_db(
    pool: Any,
    *,
    login: str,
    capabilities: list[str] | None = None,
    attempt_id: str | None = None,
    source_channel: str | None = None,
    source_session_id: str | None = None,
) -> dict[str, Any]:
    """Record a verified GitHub PAT connection in Postgres without storing any secret.

    Completes the matching open connection_attempt (if any) and returns a
    secret-free summary dict including any preserved pending_action so the
    caller can resume immediately.
    """
    caps = [str(c).strip() for c in (capabilities or GITHUB_DEFAULT_CAPABILITIES) if str(c).strip()]
    if not caps:
        caps = list(GITHUB_DEFAULT_CAPABILITIES)

    pending_action: dict[str, Any] | None = None
    connection_payload: dict[str, Any] = {}
    if pool is not None:
        async with pool.acquire() as conn:
            resolved_attempt_id = attempt_id
            if resolved_attempt_id:
                row = await conn.fetchrow(
                    """
                    SELECT id::text AS id, flow_state, requested_capabilities
                    FROM connection_attempts
                    WHERE id = $1::uuid AND connector_id = $2
                    """,
                    resolved_attempt_id,
                    GITHUB_CONNECTOR_ID,
                )
            else:
                # Prefer the attempt from this session: without the filter the
                # latest attempt for the connector wins, so a second
                # conversation can resume (or complete) another session's
                # pending setup. Fall back to the latest attempt only when
                # no session was recorded.
                if source_session_id:
                    row = await conn.fetchrow(
                        """
                        SELECT id::text AS id, flow_state, requested_capabilities
                        FROM connection_attempts
                        WHERE connector_id = $1
                          AND source_session_id = $2
                          AND status IN ('pending_user', 'awaiting_input', 'error')
                        ORDER BY created_at DESC
                        LIMIT 1
                        """,
                        GITHUB_CONNECTOR_ID,
                        source_session_id,
                    )
                else:
                    row = await conn.fetchrow(
                        """
                        SELECT id::text AS id, flow_state, requested_capabilities
                        FROM connection_attempts
                        WHERE connector_id = $1
                          AND status IN ('pending_user', 'awaiting_input', 'error')
                        ORDER BY created_at DESC
                        LIMIT 1
                        """,
                        GITHUB_CONNECTOR_ID,
                    )

            if row is not None:
                resolved_attempt_id = row["id"]
                flow_state = _coerce_json(row["flow_state"])
                if isinstance(flow_state, dict) and isinstance(flow_state.get("pending_action"), dict):
                    pending_action = flow_state["pending_action"]
                req_caps = _coerce_json(row["requested_capabilities"])
                if not capabilities and isinstance(req_caps, list) and req_caps:
                    caps = [str(c) for c in req_caps if str(c).strip()]
            else:
                started = _coerce_json(
                    await conn.fetchval(
                        """
                        SELECT start_connection_attempt(
                            $1,
                            $2::jsonb,
                            ARRAY[]::text[],
                            $3::jsonb,
                            $4,
                            $5,
                            $6,
                            $7,
                            NULL
                        )
                        """,
                        GITHUB_CONNECTOR_ID,
                        json.dumps(caps),
                        json.dumps({"auth_mode": "fine_grained_pat"}),
                        GITHUB_PAT_CREATE_URL,
                        "Connect GitHub Issues with a fine-grained personal access token.",
                        source_channel,
                        source_session_id,
                    )
                )
                resolved_attempt_id = started["attempt_id"]

            raw = await conn.fetchval(
                """
                SELECT complete_connection_attempt(
                    $1::uuid,
                    $2,
                    $3,
                    $4,
                    ARRAY[]::text[],
                    $5::jsonb,
                    $6::jsonb
                )
                """,
                resolved_attempt_id,
                login.lower(),
                f"@{login}",
                GITHUB_PAT_CONFIG_KEY,
                json.dumps(caps),
                json.dumps(
                    {
                        "auth_store": "filesystem",
                        "auth_mode": "fine_grained_pat",
                        "github_login": login,
                        **({"resumed_pending_action": pending_action} if pending_action else {}),
                    }
                ),
            )
            connection_payload = _coerce_json(raw) or {}

    return {
        "connected": True,
        "connector_id": GITHUB_CONNECTOR_ID,
        "account_key": login.lower(),
        "display_name": f"@{login}",
        "login": login,
        "credential_ref": GITHUB_PAT_CONFIG_KEY,
        "capabilities": connection_payload.get("capabilities") or caps,
        "connection_id": connection_payload.get("connection_id"),
        "pending_action": pending_action,
    }


async def complete_github_connection_db(
    pool: Any,
    *,
    token: str,
    capabilities: list[str] | None = None,
    attempt_id: str | None = None,
    source_channel: str | None = None,
    source_session_id: str | None = None,
) -> dict[str, Any]:
    """Verify a GitHub PAT, store it privately in ~/.hexis/auth (never in
    Postgres), and record a verified github_issues connection in Postgres.

    Returns a secret-free summary dict including any preserved pending_action
    from an open setup attempt so the caller can resume immediately.
    """
    cleaned = token.strip()
    fmt_error = validate_pat_format(cleaned)
    if fmt_error:
        raise ValueError(fmt_error)

    login, verify_error = await verify_pat(cleaned)
    if verify_error or not login:
        raise RuntimeError(verify_error or "GitHub token verification failed.")

    save_credentials(GithubPatCredentials(token=cleaned, login=login))
    return await sync_github_connection_db(
        pool,
        login=login,
        capabilities=capabilities,
        attempt_id=attempt_id,
        source_channel=source_channel,
        source_session_id=source_session_id,
    )


async def revoke_github_connection_db(
    pool: Any,
    *,
    account_key: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Delete the stored GitHub PAT and revoke the DB connection record.

    The stored PAT is deleted only when the disconnect owns it: a
    whole-connector logout (account_key is None, e.g. the CLI logout) or a
    disconnect whose account_key matches the login the stored PAT was
    verified against. Disconnecting some other account must not silently
    delete a PAT that belongs to a different login; the DB revoke still
    applies to the named account.
    """
    if account_key is None:
        delete_credentials()
    else:
        creds = load_credentials()
        if (
            creds is not None
            and creds.login
            and creds.login.lower() == account_key.strip().lower()
        ):
            delete_credentials()
    revoked_payload: dict[str, Any] = {}
    if pool is not None:
        async with pool.acquire() as conn:
            raw = await conn.fetchval(
                "SELECT revoke_integration_connection($1, $2, $3)",
                GITHUB_CONNECTOR_ID,
                account_key,
                reason or "disconnected by user",
            )
            revoked_payload = _coerce_json(raw) or {}
    return {
        "revoked": True,
        "connector_id": GITHUB_CONNECTOR_ID,
        "account_key": account_key,
        **revoked_payload,
    }
