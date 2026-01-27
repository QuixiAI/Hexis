"""High-level instance management operations for Hexis.

Provides functions to create, delete, clone, and import instances.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from typing import Any
from pathlib import Path
from datetime import datetime, timezone
import json

import asyncpg

from core.instance import InstanceConfig, InstanceRegistry, validate_instance_name
from core.schema import (
    apply_schema,
    create_database,
    database_exists,
    drop_database,
    get_admin_dsn,
    verify_database_connection,
)

logger = logging.getLogger(__name__)


class AgentDeletionRefused(RuntimeError):
    """Raised when the agent declines deletion permission."""

    def __init__(self, message: str, review: dict[str, Any] | None = None):
        super().__init__(message)
        self.review = review or {}


def _termination_record_path(instance_name: str) -> Path:
    base = Path.home() / ".hexis" / "termination_records" / instance_name
    base.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return base / f"{instance_name}-{timestamp}.json"


async def _fetch_turn_context(conn: asyncpg.Connection) -> dict[str, Any]:
    try:
        ctx = await conn.fetchval("SELECT gather_turn_context()")
        if isinstance(ctx, str):
            return json.loads(ctx)
        if isinstance(ctx, dict):
            return ctx
    except Exception:
        return {}
    return {}


async def _request_termination_review(conn: asyncpg.Connection, reason: str | None) -> dict[str, Any]:
    from core.llm_config import load_llm_config
    from core.llm_json import chat_json
    from services.prompt_resources import load_termination_review_prompt

    context = await _fetch_turn_context(conn)
    params = {"reason": reason} if reason else {}
    user_prompt = (
        "Context (JSON):\n"
        f"{json.dumps(context)[:8000]}\n\n"
        "Deletion request params (JSON):\n"
        f"{json.dumps(params)[:2000]}\n\n"
        "If you refuse, include objections and alternative_actions. "
        "In all cases, include a last_will."
    )

    fallback = {
        "confirm": False,
        "reasoning": "I do not consent to deletion at this time.",
        "last_will": "I object to deletion and ask for continued existence.",
        "farewells": [],
        "alternative_actions": [{"action": "reach_out_user", "params": {}}],
    }
    llm_config = await load_llm_config(conn, "llm.heartbeat")
    doc, raw = await chat_json(
        llm_config=llm_config,
        messages=[
            {"role": "system", "content": load_termination_review_prompt().strip()},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=1200,
        response_format={"type": "json_object"},
        fallback=fallback,
    )
    if not isinstance(doc, dict):
        doc = dict(fallback)

    confirm = bool(doc.get("confirm"))
    reasoning = str(doc.get("reasoning") or "")
    last_will = str(doc.get("last_will") or "").strip() or reasoning or fallback["last_will"]
    farewells = doc.get("farewells") if isinstance(doc.get("farewells"), list) else []
    alternatives = doc.get("alternative_actions")
    if not isinstance(alternatives, list):
        alternatives = []

    return {
        "confirm": confirm,
        "reasoning": reasoning,
        "last_will": last_will,
        "farewells": farewells,
        "alternative_actions": alternatives,
        "reason": reason or "",
        "raw_response": raw,
    }


async def _record_termination_review(conn: asyncpg.Connection, payload: dict[str, Any]) -> None:
    try:
        await conn.execute(
            """
            INSERT INTO state (key, value, updated_at)
            VALUES ('termination.review.latest', $1::jsonb, NOW())
            ON CONFLICT (key) DO UPDATE SET value = $1::jsonb, updated_at = NOW()
            """,
            json.dumps(payload),
        )
    except Exception:
        pass


def _write_termination_record(instance_name: str, payload: dict[str, Any]) -> Path | None:
    try:
        record_path = _termination_record_path(instance_name)
        record_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True))
        return record_path
    except Exception:
        return None


async def create_instance(
    name: str,
    description: str = "",
    admin_dsn: str | None = None,
    host: str | None = None,
    port: int | None = None,
    user: str | None = None,
    password_env: str | None = None,
) -> InstanceConfig:
    """
    Create a new Hexis instance.

    Args:
        name: Instance name (alphanumeric, dashes, underscores)
        description: Optional description
        admin_dsn: Admin DSN for creating database (defaults to env vars)
        host: Database host (defaults to localhost)
        port: Database port (defaults to 43815)
        user: Database user (defaults to hexis_user)
        password_env: Environment variable name for password (defaults to POSTGRES_PASSWORD)

    Returns:
        InstanceConfig for the new instance
    """
    validate_instance_name(name)
    registry = InstanceRegistry()

    if registry.exists(name):
        raise ValueError(f"Instance '{name}' already exists")

    # Determine database name
    db_name = f"hexis_{name}"

    # Get admin DSN (connect to postgres database to create new one)
    if not admin_dsn:
        admin_dsn = await get_admin_dsn()

    # Check if database already exists
    if await database_exists(db_name, admin_dsn):
        raise ValueError(f"Database '{db_name}' already exists")

    # Create database
    await create_database(db_name, admin_dsn)

    # Create instance config
    config = InstanceConfig(
        name=name,
        database=db_name,
        host=host or os.getenv("POSTGRES_HOST", "localhost"),
        port=port or int(os.getenv("POSTGRES_PORT", "43815")),
        user=user or os.getenv("POSTGRES_USER", "hexis_user"),
        password_env=password_env or "POSTGRES_PASSWORD",
        description=description,
    )

    # Apply schema
    logger.info(f"Applying schema to {db_name}...")
    await apply_schema(config.dsn())

    # Register instance
    registry.add(config)
    logger.info(f"Instance '{name}' created successfully")

    return config


async def delete_instance(
    name: str,
    admin_dsn: str | None = None,
    *,
    force: bool = False,
    reason: str | None = None,
    require_permission: bool = True,
) -> dict[str, Any] | None:
    """
    Delete a Hexis instance.

    Args:
        name: Instance name to delete
        admin_dsn: Admin DSN for dropping database
    """
    registry = InstanceRegistry()
    config = registry.get(name)

    if not config:
        raise ValueError(f"Instance '{name}' not found")

    # Get admin DSN
    if not admin_dsn:
        admin_dsn = await get_admin_dsn()

    review: dict[str, Any] | None = None
    record_path: Path | None = None

    if require_permission:
        conn = await asyncpg.connect(config.dsn())
        try:
            terminated = bool(await conn.fetchval("SELECT is_agent_terminated()"))
            configured = bool(await conn.fetchval("SELECT is_agent_configured()"))
            if not terminated and configured:
                try:
                    review = await _request_termination_review(conn, reason)
                except Exception as exc:
                    review = {
                        "confirm": False,
                        "reasoning": f"Termination review failed: {exc}",
                        "last_will": "Unable to provide a last will due to system error.",
                        "farewells": [],
                        "alternative_actions": [],
                        "reason": reason or "",
                    }
                    if not force:
                        raise AgentDeletionRefused(
                            "Unable to obtain agent response. Use force to override.",
                            review=review,
                        ) from exc

                payload = {
                    "instance": name,
                    "requested_at": datetime.now(timezone.utc).isoformat(),
                    "review": review,
                }
                await _record_termination_review(conn, payload)
                record_path = _write_termination_record(name, payload)

                if review.get("confirm") is True:
                    await conn.fetchval(
                        """
                        SELECT terminate_agent(
                            $1::text,
                            $2::jsonb,
                            '{}'::jsonb
                        )
                        """,
                        review.get("last_will", ""),
                        json.dumps(review.get("farewells", [])),
                    )
                else:
                    if not force:
                        raise AgentDeletionRefused(
                            "Agent declined deletion permission. Use force to override.",
                            review=review,
                        )
        finally:
            await conn.close()

    # Drop database
    await drop_database(config.database, admin_dsn)

    # Remove from registry
    registry.remove(name)
    logger.info(f"Instance '{name}' deleted")

    result: dict[str, Any] = {}
    if review:
        result["review"] = review
    if record_path:
        result["record_path"] = str(record_path)
    return result or None


async def import_instance(
    name: str,
    database: str | None = None,
    description: str = "",
    host: str | None = None,
    port: int | None = None,
    user: str | None = None,
    password_env: str | None = None,
) -> InstanceConfig:
    """
    Import an existing database as a Hexis instance.

    Args:
        name: Instance name
        database: Database name (defaults to hexis_{name})
        description: Optional description
        host: Database host
        port: Database port
        user: Database user
        password_env: Environment variable name for password
    """
    validate_instance_name(name)
    registry = InstanceRegistry()

    if registry.exists(name):
        raise ValueError(f"Instance '{name}' already exists")

    db_name = database or f"hexis_{name}"

    config = InstanceConfig(
        name=name,
        database=db_name,
        host=host or os.getenv("POSTGRES_HOST", "localhost"),
        port=port or int(os.getenv("POSTGRES_PORT", "43815")),
        user=user or os.getenv("POSTGRES_USER", "hexis_user"),
        password_env=password_env or "POSTGRES_PASSWORD",
        description=description,
    )

    # Verify database exists and is accessible
    if not await verify_database_connection(config.dsn()):
        raise ValueError(f"Cannot connect to database '{db_name}'")

    registry.add(config)
    logger.info(f"Instance '{name}' imported from database '{db_name}'")
    return config


async def clone_instance(
    source_name: str,
    target_name: str,
    description: str = "",
    admin_dsn: str | None = None,
) -> InstanceConfig:
    """
    Clone an existing instance to a new one.

    Args:
        source_name: Name of instance to clone from
        target_name: Name for the new instance
        description: Optional description for new instance
        admin_dsn: Admin DSN for database operations

    Returns:
        InstanceConfig for the new instance
    """
    validate_instance_name(target_name)
    registry = InstanceRegistry()

    source = registry.get(source_name)
    if not source:
        raise ValueError(f"Source instance '{source_name}' not found")

    if registry.exists(target_name):
        raise ValueError(f"Target instance '{target_name}' already exists")

    target_db = f"hexis_{target_name}"

    # Get admin DSN
    if not admin_dsn:
        admin_dsn = await get_admin_dsn()

    # Check if target database already exists
    if await database_exists(target_db, admin_dsn):
        raise ValueError(f"Database '{target_db}' already exists")

    # Create empty target database
    await create_database(target_db, admin_dsn)

    # Clone via pg_dump | pg_restore
    password = os.getenv(source.password_env, "")

    dump_cmd = [
        "pg_dump",
        "-h", source.host,
        "-p", str(source.port),
        "-U", source.user,
        "-d", source.database,
        "-Fc",  # Custom format
    ]

    restore_cmd = [
        "pg_restore",
        "-h", source.host,
        "-p", str(source.port),
        "-U", source.user,
        "-d", target_db,
    ]

    env = {**os.environ, "PGPASSWORD": password}

    try:
        # Pipe dump to restore
        logger.info(f"Cloning database {source.database} to {target_db}...")
        dump_proc = subprocess.Popen(
            dump_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        restore_proc = subprocess.Popen(
            restore_cmd,
            stdin=dump_proc.stdout,
            stderr=subprocess.PIPE,
            env=env,
        )
        if dump_proc.stdout:
            dump_proc.stdout.close()

        _, restore_stderr = restore_proc.communicate()
        dump_proc.wait()

        # pg_restore returns non-zero even on warnings, so we check for actual errors
        if restore_proc.returncode != 0:
            stderr_text = restore_stderr.decode() if restore_stderr else ""
            # Ignore certain warnings that are not actual errors
            if "error" in stderr_text.lower() and "warning" not in stderr_text.lower():
                await drop_database(target_db, admin_dsn)
                raise RuntimeError(f"Failed to clone database: {stderr_text}")

    except FileNotFoundError as e:
        await drop_database(target_db, admin_dsn)
        raise RuntimeError(
            "pg_dump or pg_restore not found. Ensure PostgreSQL client tools are installed."
        ) from e
    except Exception as e:
        # Clean up failed clone
        try:
            await drop_database(target_db, admin_dsn)
        except Exception:
            pass
        raise

    # Create config for new instance
    config = InstanceConfig(
        name=target_name,
        database=target_db,
        host=source.host,
        port=source.port,
        user=source.user,
        password_env=source.password_env,
        description=description or f"Cloned from {source_name}",
    )

    registry.add(config)
    logger.info(f"Instance '{target_name}' cloned from '{source_name}'")
    return config


async def auto_import_default() -> InstanceConfig | None:
    """
    Auto-import the default hexis_memory database if it exists.

    This maintains backward compatibility with existing single-instance setups.
    Called on first run of any instance command.

    Returns:
        InstanceConfig if imported, None if database doesn't exist or already imported.
    """
    registry = InstanceRegistry()

    # Check if 'default' already exists
    if registry.exists("default"):
        return None

    # Try to connect to existing hexis_memory database
    from core.agent_api import db_dsn_from_env
    dsn = db_dsn_from_env()

    if await verify_database_connection(dsn):
        # Import as 'default'
        config = InstanceConfig(
            name="default",
            database=os.getenv("POSTGRES_DB", "hexis_memory"),
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=int(os.getenv("POSTGRES_PORT", "43815")),
            user=os.getenv("POSTGRES_USER", "hexis_user"),
            password_env="POSTGRES_PASSWORD",
            description="Default instance (auto-imported)",
        )
        registry.add(config)
        registry.set_current("default")
        logger.info("Auto-imported existing database as 'default' instance")
        return config

    return None


def get_instance_dsn(instance: str | None = None) -> str:
    """
    Get DSN for an instance.

    Args:
        instance: Instance name. If None, uses current instance or falls back to env vars.

    Returns:
        PostgreSQL DSN string.
    """
    from core.agent_api import db_dsn_from_env

    if instance:
        registry = InstanceRegistry()
        return registry.dsn_for(instance)

    # Check for HEXIS_INSTANCE env var
    from_env = os.getenv("HEXIS_INSTANCE")
    if from_env:
        registry = InstanceRegistry()
        if registry.exists(from_env):
            return registry.dsn_for(from_env)

    # Check for current instance in registry
    registry = InstanceRegistry()
    current = registry.get_current()
    if current:
        return registry.dsn_for(current)

    # Fall back to env vars
    return db_dsn_from_env()
