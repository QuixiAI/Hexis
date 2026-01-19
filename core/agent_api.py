from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import asyncpg


def db_dsn_from_env() -> str:
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "43815"))
    database = os.getenv("POSTGRES_DB", "hexis_memory")
    user = os.getenv("POSTGRES_USER", "hexis_user")
    password = os.getenv("POSTGRES_PASSWORD", "hexis_password")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def _resolve_wait_seconds(wait_seconds: int | None) -> int:
    if wait_seconds is None:
        return int(os.getenv("POSTGRES_WAIT_SECONDS", "30"))
    return int(wait_seconds)


async def _connect_with_retry(dsn: str, *, wait_seconds: int = 30) -> asyncpg.Connection:
    deadline = time.monotonic() + wait_seconds
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return await asyncpg.connect(dsn, ssl=False, command_timeout=60.0)
        except Exception as exc:
            last_err = exc
            await asyncio.sleep(1)
    raise TimeoutError(f"Failed to connect to Postgres after {wait_seconds}s: {last_err!r}")


async def get_agent_status(dsn: str | None = None) -> dict[str, Any]:
    dsn = dsn or db_dsn_from_env()
    conn = await _connect_with_retry(dsn, wait_seconds=int(os.getenv("POSTGRES_WAIT_SECONDS", "30")))
    try:
        configured = await conn.fetchval("SELECT is_agent_configured()")
        terminated = await conn.fetchval("SELECT is_agent_terminated()")
        consent = await conn.fetchval("SELECT get_agent_consent_status()")
        consent_log_id = await conn.fetchval("SELECT get_config('agent.consent_log_id')")
        has_consent_log = consent_log_id is not None
        configured = bool(configured) and has_consent_log and consent == "consent"
        return {
            "configured": configured,
            "terminated": bool(terminated),
            "consent_status": consent,
            "consent_log_id": consent_log_id,
        }
    finally:
        await conn.close()


async def get_init_defaults(dsn: str | None = None, wait_seconds: int | None = None) -> dict[str, Any]:
    """Get default configuration values from unified config table.

    Phase 7 (ReduceScopeCreep): Uses unified config table instead of legacy heartbeat_config/maintenance_config.
    """
    dsn = dsn or db_dsn_from_env()
    conn = await _connect_with_retry(dsn, wait_seconds=_resolve_wait_seconds(wait_seconds))
    try:
        # Phase 7: Use unified config table with namespaced keys
        rows = await conn.fetch(
            """
            SELECT key, value
            FROM get_config_by_prefixes($1::text[])
            """,
            ["heartbeat.", "maintenance."],
        )
        cfg = {r["key"]: r["value"] for r in rows}

        def get_float(key: str, default: float) -> float:
            val = cfg.get(key)
            if val is None:
                return default
            # JSONB values may be strings or numbers
            if isinstance(val, (int, float)):
                return float(val)
            try:
                import json
                return float(json.loads(val) if isinstance(val, str) else val)
            except Exception:
                return default

        return {
            "heartbeat_interval_minutes": int(get_float("heartbeat.heartbeat_interval_minutes", 60)),
            "max_energy": get_float("heartbeat.max_energy", 20),
            "base_regeneration": get_float("heartbeat.base_regeneration", 10),
            "max_active_goals": int(get_float("heartbeat.max_active_goals", 3)),
            "maintenance_interval_seconds": int(get_float("maintenance.maintenance_interval_seconds", 60)),
            "subconscious_interval_seconds": int(get_float("maintenance.subconscious_interval_seconds", 300)),
        }
    finally:
        await conn.close()


async def ensure_schema_has_config(dsn: str | None = None, wait_seconds: int | None = None) -> None:
    dsn = dsn or db_dsn_from_env()
    conn = await _connect_with_retry(dsn, wait_seconds=_resolve_wait_seconds(wait_seconds))
    try:
        ok = await conn.fetchval("SELECT to_regclass('public.config') IS NOT NULL")
        if not ok:
            raise RuntimeError(
                "Database schema is missing `config` table. "
                "If you just updated `db/schema.sql`, reset the DB volume and retry: "
                "`docker compose down -v && docker compose up -d`."
            )
    finally:
        await conn.close()


async def bootstrap_identity(dsn: str | None = None, wait_seconds: int | None = None) -> str | None:
    dsn = dsn or db_dsn_from_env()
    conn = await _connect_with_retry(dsn, wait_seconds=_resolve_wait_seconds(wait_seconds))
    try:
        try:
            await conn.fetchval("SELECT initialize_personality(NULL)")
            await conn.fetchval("SELECT initialize_core_values(NULL)")
            await conn.fetchval("SELECT initialize_worldview(NULL)")
        except Exception as exc:
            return str(exc)
        return None
    finally:
        await conn.close()


async def get_config(dsn: str | None, key: str) -> Any:
    dsn = dsn or db_dsn_from_env()
    conn = await _connect_with_retry(dsn, wait_seconds=int(os.getenv("POSTGRES_WAIT_SECONDS", "30")))
    try:
        value = await conn.fetchval("SELECT get_config($1)", key)
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return value
        return value
    finally:
        await conn.close()


async def get_llm_config(dsn: str | None, key: str) -> dict[str, Any]:
    value = await get_config(dsn, key)
    if isinstance(value, dict):
        return value
    return {}


async def get_agent_profile_context(dsn: str | None = None) -> dict[str, Any]:
    dsn = dsn or db_dsn_from_env()
    conn = await _connect_with_retry(dsn, wait_seconds=int(os.getenv("POSTGRES_WAIT_SECONDS", "30")))
    try:
        value = await conn.fetchval("SELECT get_agent_profile_context()")
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return {}
        return value or {}
    finally:
        await conn.close()


async def apply_agent_config(
    *,
    dsn: str | None = None,
    wait_seconds: int | None = None,
    heartbeat_interval_minutes: int,
    maintenance_interval_seconds: int,
    subconscious_interval_seconds: int | None = None,
    max_energy: float,
    base_regeneration: float,
    max_active_goals: int,
    objectives: list[str],
    guardrails: list[str],
    initial_message: str,
    tools: list[str],
    llm_heartbeat: dict[str, Any],
    llm_chat: dict[str, Any],
    llm_subconscious: dict[str, Any] | None = None,
    contact_channels: list[str],
    contact_destinations: dict[str, str],
    enable_autonomy: bool,
    enable_maintenance: bool,
    enable_subconscious: bool | None = None,
    mark_configured: bool,
) -> None:
    dsn = dsn or db_dsn_from_env()
    conn = await _connect_with_retry(dsn, wait_seconds=_resolve_wait_seconds(wait_seconds))
    try:
        async with conn.transaction():
            # Phase 7 (ReduceScopeCreep): Use unified config table with namespaced keys
            await conn.execute(
                "SELECT set_config('heartbeat.heartbeat_interval_minutes', $1::jsonb)",
                str(float(heartbeat_interval_minutes)),
            )
            await conn.execute(
                "SELECT set_config('heartbeat.max_energy', $1::jsonb)",
                str(float(max_energy)),
            )
            await conn.execute(
                "SELECT set_config('heartbeat.base_regeneration', $1::jsonb)",
                str(float(base_regeneration)),
            )
            await conn.execute(
                "SELECT set_config('heartbeat.max_active_goals', $1::jsonb)",
                str(float(max_active_goals)),
            )
            await conn.execute(
                "SELECT set_config('maintenance.maintenance_interval_seconds', $1::jsonb)",
                str(float(maintenance_interval_seconds)),
            )
            if subconscious_interval_seconds is not None:
                await conn.execute(
                    "SELECT set_config('maintenance.subconscious_interval_seconds', $1::jsonb)",
                    str(float(subconscious_interval_seconds)),
                )
            if enable_subconscious is not None:
                await conn.execute(
                    "SELECT set_config('maintenance.subconscious_enabled', $1::jsonb)",
                    json.dumps(bool(enable_subconscious)),
                )

            await conn.execute("SELECT set_config('agent.objectives', $1::jsonb)", json.dumps(objectives))
            await conn.execute(
                "SELECT set_config('agent.budget', $1::jsonb)",
                json.dumps(
                    {
                        "max_energy": max_energy,
                        "base_regeneration": base_regeneration,
                        "heartbeat_interval_minutes": heartbeat_interval_minutes,
                        "max_active_goals": max_active_goals,
                    }
                ),
            )
            await conn.execute("SELECT set_config('agent.guardrails', $1::jsonb)", json.dumps(guardrails))
            await conn.execute("SELECT set_config('agent.initial_message', $1::jsonb)", json.dumps(initial_message))
            await conn.execute(
                "SELECT set_config('agent.tools', $1::jsonb)",
                json.dumps([{"name": t, "enabled": True} for t in tools]),
            )

            await conn.execute("SELECT set_config('llm.heartbeat', $1::jsonb)", json.dumps(llm_heartbeat))
            await conn.execute("SELECT set_config('llm.chat', $1::jsonb)", json.dumps(llm_chat))
            await conn.execute(
                "SELECT set_config('llm.subconscious', $1::jsonb)",
                json.dumps(llm_subconscious or llm_heartbeat),
            )
            await conn.execute(
                "SELECT set_config('user.contact', $1::jsonb)",
                json.dumps({"channels": contact_channels, "destinations": contact_destinations}),
            )

            if mark_configured:
                await conn.execute("SELECT set_config('agent.is_configured', 'true'::jsonb)")

            if enable_autonomy:
                await conn.execute("UPDATE heartbeat_state SET is_paused = FALSE WHERE id = 1")
            else:
                await conn.execute("UPDATE heartbeat_state SET is_paused = TRUE WHERE id = 1")

            try:
                if enable_maintenance:
                    await conn.execute("UPDATE maintenance_state SET is_paused = FALSE WHERE id = 1")
                else:
                    await conn.execute("UPDATE maintenance_state SET is_paused = TRUE WHERE id = 1")
            except Exception:
                pass
    finally:
        await conn.close()


async def save_init_profile(
    *,
    dsn: str | None = None,
    mode: str,
    agent_name: str,
    agent_pronouns: str,
    agent_voice: str,
    personality_description: str,
    user_name: str,
    relationship_type: str,
    purpose: str,
    values: list[str],
    boundaries: list[str],
    autonomy_level: str,
) -> None:
    dsn = dsn or db_dsn_from_env()
    profile = {
        "mode": mode,
        "agent": {
            "name": agent_name,
            "pronouns": agent_pronouns,
            "voice": agent_voice,
            "personality": personality_description,
        },
        "user": {"name": user_name},
        "relationship": {
            "type": relationship_type,
            "purpose": purpose,
        },
        "values": values,
        "boundaries": boundaries,
        "autonomy_level": autonomy_level,
    }
    conn = await _connect_with_retry(dsn, wait_seconds=int(os.getenv("POSTGRES_WAIT_SECONDS", "30")))
    try:
        await conn.execute("SELECT set_config('agent.mode', $1::jsonb)", json.dumps(mode))
        await conn.execute("SELECT set_config('agent.init_profile', $1::jsonb)", json.dumps(profile))
    finally:
        await conn.close()


async def set_agent_configured(dsn: str | None, *, configured: bool) -> None:
    dsn = dsn or db_dsn_from_env()
    conn = await _connect_with_retry(dsn, wait_seconds=int(os.getenv("POSTGRES_WAIT_SECONDS", "30")))
    try:
        if configured:
            await conn.execute("SELECT set_config('agent.is_configured', 'true'::jsonb)")
        else:
            await conn.execute("SELECT delete_config_key('agent.is_configured')")
    finally:
        await conn.close()


def get_agent_status_sync(dsn: str | None = None) -> dict[str, Any]:
    from core.sync_utils import run_sync

    return run_sync(get_agent_status(dsn))


def get_init_defaults_sync(dsn: str | None = None) -> dict[str, Any]:
    from core.sync_utils import run_sync

    return run_sync(get_init_defaults(dsn))


def get_config_sync(dsn: str | None, key: str) -> Any:
    from core.sync_utils import run_sync

    return run_sync(get_config(dsn, key))


def get_llm_config_sync(dsn: str | None, key: str) -> dict[str, Any]:
    from core.sync_utils import run_sync

    return run_sync(get_llm_config(dsn, key))


def get_agent_profile_context_sync(dsn: str | None = None) -> dict[str, Any]:
    from core.sync_utils import run_sync

    return run_sync(get_agent_profile_context(dsn))


def apply_agent_config_sync(**kwargs: Any) -> None:
    from core.sync_utils import run_sync

    return run_sync(apply_agent_config(**kwargs))


def save_init_profile_sync(**kwargs: Any) -> None:
    from core.sync_utils import run_sync

    return run_sync(save_init_profile(**kwargs))


def set_agent_configured_sync(dsn: str | None, *, configured: bool) -> None:
    from core.sync_utils import run_sync

    return run_sync(set_agent_configured(dsn, configured=configured))
