"""Schema management for Hexis instances.

Provides functions to create databases, apply schema files, and manage
database lifecycle programmatically.
"""
from __future__ import annotations

import logging
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)


def get_schema_dir() -> Path:
    """Get path to db/ schema directory."""
    return Path(__file__).parent.parent / "db"


def get_schema_files() -> list[Path]:
    """Get sorted list of schema SQL files."""
    schema_dir = get_schema_dir()
    if not schema_dir.exists():
        raise FileNotFoundError(f"Schema directory not found: {schema_dir}")
    files = sorted(schema_dir.glob("*.sql"))
    if not files:
        raise FileNotFoundError(f"No schema files found in {schema_dir}")
    return files


async def create_database(db_name: str, admin_dsn: str) -> None:
    """
    Create a new empty database.

    Args:
        db_name: Name of database to create
        admin_dsn: DSN with permissions to create databases (should connect to 'postgres' database)
    """
    conn = await asyncpg.connect(admin_dsn)
    try:
        # Check if database already exists
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", db_name
        )
        if exists:
            raise ValueError(f"Database '{db_name}' already exists")

        # Can't use parameters for database names in DDL
        # Use safe identifier quoting
        await conn.execute(f'CREATE DATABASE "{db_name}"')
        logger.info(f"Created database: {db_name}")
    finally:
        await conn.close()


async def drop_database(db_name: str, admin_dsn: str) -> None:
    """
    Drop a database.

    Args:
        db_name: Name of database to drop
        admin_dsn: DSN with permissions to drop databases (should connect to 'postgres' database)
    """
    conn = await asyncpg.connect(admin_dsn)
    try:
        # Terminate existing connections
        await conn.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = $1 AND pid <> pg_backend_pid()
            """,
            db_name,
        )
        # Drop the database
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        logger.info(f"Dropped database: {db_name}")
    finally:
        await conn.close()


async def apply_schema(dsn: str) -> None:
    """
    Apply all schema files to a database.

    Args:
        dsn: Connection string for target database
    """
    schema_files = get_schema_files()
    if not schema_files:
        raise FileNotFoundError("No schema files found in db/")

    conn = await asyncpg.connect(dsn)
    try:
        for sql_file in schema_files:
            logger.info(f"Applying {sql_file.name}...")
            content = sql_file.read_text()
            try:
                await conn.execute(content)
            except Exception as e:
                logger.error(f"Error applying {sql_file.name}: {e}")
                raise
        logger.info(f"Applied {len(schema_files)} schema files")
    finally:
        await conn.close()


async def database_exists(db_name: str, admin_dsn: str) -> bool:
    """Check if a database exists."""
    conn = await asyncpg.connect(admin_dsn)
    try:
        result = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", db_name
        )
        return result is not None
    finally:
        await conn.close()


async def get_admin_dsn(base_dsn: str | None = None) -> str:
    """
    Get admin DSN for database operations.

    Replaces the database name in the DSN with 'postgres' for admin operations.

    Args:
        base_dsn: Base DSN to modify. If None, uses default from env.

    Returns:
        DSN pointing to 'postgres' database for admin operations.
    """
    if not base_dsn:
        from core.agent_api import db_dsn_from_env
        base_dsn = db_dsn_from_env()

    # Replace database name with 'postgres'
    # DSN format: postgresql://user:pass@host:port/database
    # Need to handle case where there's no database part
    # The :// is part of the scheme, so we need to find / after host:port

    # Find the position after ://
    scheme_end = base_dsn.find("://")
    if scheme_end != -1:
        # Look for / after the scheme
        after_scheme = base_dsn[scheme_end + 3:]
        slash_pos = after_scheme.find("/")
        if slash_pos != -1:
            # There is a database part, replace it
            base_part = base_dsn[:scheme_end + 3 + slash_pos]
            admin_dsn = base_part + "/postgres"
        else:
            # No database part, just append
            admin_dsn = base_dsn + "/postgres"
    else:
        # No :// found, just append /postgres
        admin_dsn = base_dsn + "/postgres"

    return admin_dsn


async def verify_database_connection(dsn: str) -> bool:
    """Verify that we can connect to a database."""
    try:
        conn = await asyncpg.connect(dsn)
        await conn.close()
        return True
    except Exception:
        return False
