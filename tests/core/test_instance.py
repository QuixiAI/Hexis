"""Tests for the instance registry system."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from core.instance import (
    InstanceConfig,
    InstanceRegistry,
    validate_instance_name,
    resolve_instance,
)


pytestmark = pytest.mark.core


class TestInstanceConfig:
    def test_to_dict(self):
        config = InstanceConfig(
            name="test",
            database="hexis_test",
            host="localhost",
            port=5432,
            description="Test instance",
        )
        d = config.to_dict()
        assert d["database"] == "hexis_test"
        assert d["host"] == "localhost"
        assert d["port"] == 5432
        assert d["description"] == "Test instance"
        assert "name" not in d  # name is key, not in value

    def test_from_dict(self):
        data = {
            "database": "hexis_test",
            "host": "localhost",
            "port": 5432,
            "user": "hexis_user",
            "password_env": "POSTGRES_PASSWORD",
            "created_at": "2024-01-01T00:00:00+00:00",
            "description": "Test",
        }
        config = InstanceConfig.from_dict("test", data)
        assert config.name == "test"
        assert config.database == "hexis_test"
        assert config.port == 5432

    def test_dsn(self):
        config = InstanceConfig(
            name="test",
            database="hexis_test",
            host="localhost",
            port=5432,
            user="testuser",
            password_env="TEST_PASSWORD",
        )
        with patch.dict(os.environ, {"TEST_PASSWORD": "secret"}):
            dsn = config.dsn()
        assert dsn == "postgresql://testuser:secret@localhost:5432/hexis_test"

    def test_dsn_missing_password(self):
        config = InstanceConfig(
            name="test",
            database="hexis_test",
            host="localhost",
            port=5432,
            user="testuser",
            password_env="NONEXISTENT_PASSWORD",
        )
        with patch.dict(os.environ, {}, clear=True):
            dsn = config.dsn()
        assert dsn == "postgresql://testuser:@localhost:5432/hexis_test"


class TestInstanceRegistry:
    @pytest.fixture
    def temp_config_dir(self, tmp_path):
        config_dir = tmp_path / ".hexis"
        config_dir.mkdir()
        return config_dir

    def test_empty_registry(self, temp_config_dir):
        registry = InstanceRegistry(config_dir=temp_config_dir)
        assert registry.get_current() is None
        assert registry.list_all() == []

    def test_add_instance(self, temp_config_dir):
        registry = InstanceRegistry(config_dir=temp_config_dir)
        config = InstanceConfig(name="test", database="hexis_test")
        registry.add(config)

        assert registry.exists("test")
        assert registry.get("test").database == "hexis_test"

    def test_add_duplicate_fails(self, temp_config_dir):
        registry = InstanceRegistry(config_dir=temp_config_dir)
        config = InstanceConfig(name="test", database="hexis_test")
        registry.add(config)

        with pytest.raises(ValueError, match="already exists"):
            registry.add(config)

    def test_add_invalid_name_fails(self, temp_config_dir):
        registry = InstanceRegistry(config_dir=temp_config_dir)
        config = InstanceConfig(name="123test", database="hexis_123test")

        with pytest.raises(ValueError, match="Invalid name"):
            registry.add(config)

    def test_remove_instance(self, temp_config_dir):
        registry = InstanceRegistry(config_dir=temp_config_dir)
        config = InstanceConfig(name="test", database="hexis_test")
        registry.add(config)
        registry.remove("test")

        assert not registry.exists("test")

    def test_remove_nonexistent_fails(self, temp_config_dir):
        registry = InstanceRegistry(config_dir=temp_config_dir)
        with pytest.raises(ValueError, match="not found"):
            registry.remove("nonexistent")

    def test_set_current(self, temp_config_dir):
        registry = InstanceRegistry(config_dir=temp_config_dir)
        config = InstanceConfig(name="test", database="hexis_test")
        registry.add(config)
        registry.set_current("test")

        assert registry.get_current() == "test"

    def test_set_current_nonexistent_fails(self, temp_config_dir):
        registry = InstanceRegistry(config_dir=temp_config_dir)
        with pytest.raises(ValueError, match="not found"):
            registry.set_current("nonexistent")

    def test_remove_clears_current(self, temp_config_dir):
        registry = InstanceRegistry(config_dir=temp_config_dir)
        config = InstanceConfig(name="test", database="hexis_test")
        registry.add(config)
        registry.set_current("test")
        registry.remove("test")

        assert registry.get_current() is None

    def test_list_all(self, temp_config_dir):
        registry = InstanceRegistry(config_dir=temp_config_dir)
        registry.add(InstanceConfig(name="a", database="hexis_a"))
        registry.add(InstanceConfig(name="b", database="hexis_b"))

        instances = registry.list_all()
        names = [i.name for i in instances]
        assert "a" in names
        assert "b" in names

    def test_persistence(self, temp_config_dir):
        registry1 = InstanceRegistry(config_dir=temp_config_dir)
        registry1.add(InstanceConfig(name="test", database="hexis_test"))
        registry1.set_current("test")

        # New registry instance should load persisted data
        registry2 = InstanceRegistry(config_dir=temp_config_dir)
        assert registry2.exists("test")
        assert registry2.get_current() == "test"

    def test_update_instance(self, temp_config_dir):
        registry = InstanceRegistry(config_dir=temp_config_dir)
        config = InstanceConfig(name="test", database="hexis_test", description="Original")
        registry.add(config)

        updated = InstanceConfig(name="test", database="hexis_test", description="Updated")
        registry.update(updated)

        loaded = registry.get("test")
        assert loaded.description == "Updated"

    def test_update_nonexistent_fails(self, temp_config_dir):
        registry = InstanceRegistry(config_dir=temp_config_dir)
        config = InstanceConfig(name="test", database="hexis_test")

        with pytest.raises(ValueError, match="not found"):
            registry.update(config)

    def test_dsn_for(self, temp_config_dir):
        registry = InstanceRegistry(config_dir=temp_config_dir)
        config = InstanceConfig(
            name="test",
            database="hexis_test",
            host="localhost",
            port=5432,
            user="testuser",
            password_env="TEST_PASSWORD",
        )
        registry.add(config)

        with patch.dict(os.environ, {"TEST_PASSWORD": "secret"}):
            dsn = registry.dsn_for("test")
        assert "hexis_test" in dsn

    def test_dsn_for_nonexistent_fails(self, temp_config_dir):
        registry = InstanceRegistry(config_dir=temp_config_dir)
        with pytest.raises(ValueError, match="not found"):
            registry.dsn_for("nonexistent")


class TestValidateInstanceName:
    @pytest.mark.parametrize("name", ["test", "my-instance", "test_123", "A1", "myInstance"])
    def test_valid_names(self, name):
        validate_instance_name(name)  # Should not raise

    @pytest.mark.parametrize("name", ["123test", "-invalid", "_invalid", "has space", "has.dot", ""])
    def test_invalid_names(self, name):
        with pytest.raises(ValueError):
            validate_instance_name(name)


class TestResolveInstance:
    def test_resolve_from_env(self, tmp_path):
        with patch.dict(os.environ, {"HEXIS_INSTANCE": "from-env"}):
            result = resolve_instance()
        assert result == "from-env"

    def test_resolve_from_registry(self, tmp_path):
        config_dir = tmp_path / ".hexis"
        config_dir.mkdir()

        registry = InstanceRegistry(config_dir=config_dir)
        registry.add(InstanceConfig(name="test", database="hexis_test"))
        registry.set_current("test")

        with patch.dict(os.environ, {}, clear=True):
            with patch.object(InstanceRegistry, "CONFIG_DIR", config_dir):
                with patch.object(InstanceRegistry, "CONFIG_FILE", config_dir / "instances.json"):
                    result = resolve_instance()

        assert result == "test"
