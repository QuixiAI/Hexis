"""Tests for the schema management module."""
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest

from core.schema import get_schema_files, get_schema_dir, get_admin_dsn


pytestmark = pytest.mark.core


class TestGetSchemaDir:
    def test_returns_path(self):
        schema_dir = get_schema_dir()
        assert isinstance(schema_dir, Path)
        assert schema_dir.name == "db"


class TestGetSchemaFiles:
    def test_returns_sorted_files(self, tmp_path):
        # Create mock schema files
        (tmp_path / "02_second.sql").write_text("-- second")
        (tmp_path / "01_first.sql").write_text("-- first")
        (tmp_path / "03_third.sql").write_text("-- third")
        (tmp_path / "not_sql.txt").write_text("not sql")

        with patch("core.schema.get_schema_dir", return_value=tmp_path):
            files = get_schema_files()

        names = [f.name for f in files]
        assert names == ["01_first.sql", "02_second.sql", "03_third.sql"]

    def test_no_schema_dir_raises(self, tmp_path):
        with patch("core.schema.get_schema_dir", return_value=tmp_path / "nonexistent"):
            with pytest.raises(FileNotFoundError, match="Schema directory not found"):
                get_schema_files()

    def test_empty_schema_dir_raises(self, tmp_path):
        with patch("core.schema.get_schema_dir", return_value=tmp_path):
            with pytest.raises(FileNotFoundError, match="No schema files found"):
                get_schema_files()


@pytest.mark.asyncio(loop_scope="session")
class TestGetAdminDsn:
    async def test_replaces_database_with_postgres(self):
        with patch("core.agent_api.db_dsn_from_env", return_value="postgresql://user:pass@localhost:5432/hexis_memory"):
            admin_dsn = await get_admin_dsn()
        assert admin_dsn == "postgresql://user:pass@localhost:5432/postgres"

    async def test_uses_provided_base_dsn(self):
        admin_dsn = await get_admin_dsn("postgresql://user:pass@host:1234/mydb")
        assert admin_dsn == "postgresql://user:pass@host:1234/postgres"

    async def test_handles_dsn_without_database(self):
        admin_dsn = await get_admin_dsn("postgresql://user:pass@host:1234")
        assert admin_dsn == "postgresql://user:pass@host:1234/postgres"
