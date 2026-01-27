"""Tests for the high-level instance API."""
import os
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from core.instance import InstanceConfig, InstanceRegistry
from core.instance_api import (
    create_instance,
    delete_instance,
    import_instance,
    clone_instance,
    auto_import_default,
    get_instance_dsn,
)


pytestmark = pytest.mark.core


@pytest.mark.asyncio(loop_scope="session")
class TestCreateInstance:
    @pytest.fixture
    def temp_registry(self, tmp_path):
        config_dir = tmp_path / ".hexis"
        config_dir.mkdir()
        with patch.object(InstanceRegistry, "CONFIG_DIR", config_dir):
            with patch.object(InstanceRegistry, "CONFIG_FILE", config_dir / "instances.json"):
                yield config_dir

    async def test_create_instance_success(self, temp_registry):
        with patch("core.instance_api.database_exists", new_callable=AsyncMock, return_value=False):
            with patch("core.instance_api.create_database", new_callable=AsyncMock):
                with patch("core.instance_api.apply_schema", new_callable=AsyncMock):
                    with patch("core.instance_api.get_admin_dsn", new_callable=AsyncMock, return_value="postgresql://admin@localhost/postgres"):
                        config = await create_instance("test", "Test instance")

        assert config.name == "test"
        assert config.database == "hexis_test"

        registry = InstanceRegistry()
        assert registry.exists("test")

    async def test_create_duplicate_fails(self, temp_registry):
        registry = InstanceRegistry()
        registry.add(InstanceConfig(name="test", database="hexis_test"))

        with pytest.raises(ValueError, match="already exists"):
            await create_instance("test")

    async def test_create_invalid_name_fails(self, temp_registry):
        with pytest.raises(ValueError, match="Invalid instance name"):
            await create_instance("123invalid")

    async def test_create_when_database_exists_fails(self, temp_registry):
        with patch("core.instance_api.database_exists", new_callable=AsyncMock, return_value=True):
            with patch("core.instance_api.get_admin_dsn", new_callable=AsyncMock, return_value="postgresql://admin@localhost/postgres"):
                with pytest.raises(ValueError, match="already exists"):
                    await create_instance("test")


@pytest.mark.asyncio(loop_scope="session")
class TestDeleteInstance:
    @pytest.fixture
    def temp_registry(self, tmp_path):
        config_dir = tmp_path / ".hexis"
        config_dir.mkdir()
        with patch.object(InstanceRegistry, "CONFIG_DIR", config_dir):
            with patch.object(InstanceRegistry, "CONFIG_FILE", config_dir / "instances.json"):
                yield config_dir

    async def test_delete_instance_success(self, temp_registry):
        registry = InstanceRegistry()
        registry.add(InstanceConfig(name="test", database="hexis_test"))

        with patch("core.instance_api.drop_database", new_callable=AsyncMock):
            with patch("core.instance_api.get_admin_dsn", new_callable=AsyncMock, return_value="postgresql://admin@localhost/postgres"):
                await delete_instance("test", require_permission=False)

        # Reload registry from file to check the deletion was persisted
        fresh_registry = InstanceRegistry()
        assert not fresh_registry.exists("test")

    async def test_delete_nonexistent_fails(self, temp_registry):
        with pytest.raises(ValueError, match="not found"):
            await delete_instance("nonexistent", require_permission=False)


@pytest.mark.asyncio(loop_scope="session")
class TestImportInstance:
    @pytest.fixture
    def temp_registry(self, tmp_path):
        config_dir = tmp_path / ".hexis"
        config_dir.mkdir()
        with patch.object(InstanceRegistry, "CONFIG_DIR", config_dir):
            with patch.object(InstanceRegistry, "CONFIG_FILE", config_dir / "instances.json"):
                yield config_dir

    async def test_import_instance_success(self, temp_registry):
        with patch("core.instance_api.verify_database_connection", new_callable=AsyncMock, return_value=True):
            config = await import_instance("test", "existing_db", "Imported instance")

        assert config.name == "test"
        assert config.database == "existing_db"

        registry = InstanceRegistry()
        assert registry.exists("test")

    async def test_import_duplicate_fails(self, temp_registry):
        registry = InstanceRegistry()
        registry.add(InstanceConfig(name="test", database="hexis_test"))

        with pytest.raises(ValueError, match="already exists"):
            await import_instance("test")

    async def test_import_unconnectable_fails(self, temp_registry):
        with patch("core.instance_api.verify_database_connection", new_callable=AsyncMock, return_value=False):
            with pytest.raises(ValueError, match="Cannot connect"):
                await import_instance("test", "nonexistent_db")


@pytest.mark.asyncio(loop_scope="session")
class TestCloneInstance:
    @pytest.fixture
    def temp_registry(self, tmp_path):
        config_dir = tmp_path / ".hexis"
        config_dir.mkdir()
        with patch.object(InstanceRegistry, "CONFIG_DIR", config_dir):
            with patch.object(InstanceRegistry, "CONFIG_FILE", config_dir / "instances.json"):
                yield config_dir

    async def test_clone_nonexistent_source_fails(self, temp_registry):
        with pytest.raises(ValueError, match="not found"):
            await clone_instance("nonexistent", "target")

    async def test_clone_to_existing_target_fails(self, temp_registry):
        registry = InstanceRegistry()
        registry.add(InstanceConfig(name="source", database="hexis_source"))
        registry.add(InstanceConfig(name="target", database="hexis_target"))

        with pytest.raises(ValueError, match="already exists"):
            await clone_instance("source", "target")


@pytest.mark.asyncio(loop_scope="session")
class TestAutoImportDefault:
    @pytest.fixture
    def temp_registry(self, tmp_path):
        config_dir = tmp_path / ".hexis"
        config_dir.mkdir()
        with patch.object(InstanceRegistry, "CONFIG_DIR", config_dir):
            with patch.object(InstanceRegistry, "CONFIG_FILE", config_dir / "instances.json"):
                yield config_dir

    async def test_auto_import_when_default_exists(self, temp_registry):
        registry = InstanceRegistry()
        registry.add(InstanceConfig(name="default", database="hexis_memory"))

        result = await auto_import_default()
        assert result is None

    async def test_auto_import_when_db_exists(self, temp_registry):
        with patch("core.instance_api.verify_database_connection", new_callable=AsyncMock, return_value=True):
            with patch("core.agent_api.db_dsn_from_env", return_value="postgresql://user@localhost/hexis_memory"):
                with patch.dict(os.environ, {"POSTGRES_DB": "hexis_memory"}):
                    result = await auto_import_default()

        assert result is not None
        assert result.name == "default"

        registry = InstanceRegistry()
        assert registry.exists("default")
        assert registry.get_current() == "default"

    async def test_auto_import_when_db_not_exists(self, temp_registry):
        with patch("core.instance_api.verify_database_connection", new_callable=AsyncMock, return_value=False):
            with patch("core.agent_api.db_dsn_from_env", return_value="postgresql://user@localhost/hexis_memory"):
                result = await auto_import_default()

        assert result is None


class TestGetInstanceDsn:
    @pytest.fixture
    def temp_registry(self, tmp_path):
        config_dir = tmp_path / ".hexis"
        config_dir.mkdir()
        with patch.object(InstanceRegistry, "CONFIG_DIR", config_dir):
            with patch.object(InstanceRegistry, "CONFIG_FILE", config_dir / "instances.json"):
                yield config_dir

    def test_get_dsn_for_specific_instance(self, temp_registry):
        registry = InstanceRegistry()
        registry.add(InstanceConfig(
            name="test",
            database="hexis_test",
            host="localhost",
            port=5432,
            user="testuser",
            password_env="TEST_PASSWORD",
        ))

        with patch.dict(os.environ, {"TEST_PASSWORD": "secret"}):
            dsn = get_instance_dsn("test")

        assert "hexis_test" in dsn

    def test_get_dsn_from_env(self, temp_registry):
        registry = InstanceRegistry()
        registry.add(InstanceConfig(
            name="from-env",
            database="hexis_from_env",
            host="localhost",
            port=5432,
            user="testuser",
            password_env="TEST_PASSWORD",
        ))

        with patch.dict(os.environ, {"HEXIS_INSTANCE": "from-env", "TEST_PASSWORD": "secret"}):
            dsn = get_instance_dsn()

        assert "hexis_from_env" in dsn

    def test_get_dsn_from_current(self, temp_registry):
        registry = InstanceRegistry()
        registry.add(InstanceConfig(
            name="current",
            database="hexis_current",
            host="localhost",
            port=5432,
            user="testuser",
            password_env="TEST_PASSWORD",
        ))
        registry.set_current("current")

        with patch.dict(os.environ, {"TEST_PASSWORD": "secret"}, clear=True):
            dsn = get_instance_dsn()

        assert "hexis_current" in dsn

    def test_get_dsn_fallback_to_env(self, temp_registry):
        with patch.dict(os.environ, {
            "POSTGRES_HOST": "localhost",
            "POSTGRES_PORT": "5432",
            "POSTGRES_DB": "hexis_fallback",
            "POSTGRES_USER": "testuser",
            "POSTGRES_PASSWORD": "secret",
        }, clear=True):
            dsn = get_instance_dsn()

        assert "hexis_fallback" in dsn
