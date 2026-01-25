"""Instance registry for multi-instance Hexis management.

Each instance is a separate PostgreSQL database with its own identity, memories,
and configuration. The registry tracks instances in ~/.hexis/instances.json.

Registry file format:
    {
        "version": 1,
        "current": "default",
        "instances": {
            "default": {
                "database": "hexis_memory",
                "host": "localhost",
                "port": 43815,
                "user": "hexis_user",
                "password_env": "POSTGRES_PASSWORD",
                "created_at": "2024-01-25T00:00:00Z",
                "description": "Default instance"
            }
        }
    }
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class InstanceConfig:
    """Configuration for a Hexis instance."""

    name: str
    database: str
    host: str = "localhost"
    port: int = 43815
    user: str = "hexis_user"
    password_env: str = "POSTGRES_PASSWORD"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "database": self.database,
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password_env": self.password_env,
            "created_at": self.created_at.isoformat(),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> InstanceConfig:
        return cls(
            name=name,
            database=data["database"],
            host=data.get("host", "localhost"),
            port=data.get("port", 43815),
            user=data.get("user", "hexis_user"),
            password_env=data.get("password_env", "POSTGRES_PASSWORD"),
            created_at=datetime.fromisoformat(data["created_at"]),
            description=data.get("description", ""),
        )

    def dsn(self) -> str:
        """Build PostgreSQL DSN for this instance."""
        password = os.getenv(self.password_env, "")
        return f"postgresql://{self.user}:{password}@{self.host}:{self.port}/{self.database}"


class InstanceRegistry:
    """Manages Hexis instances via ~/.hexis/instances.json."""

    CONFIG_DIR = Path.home() / ".hexis"
    CONFIG_FILE = CONFIG_DIR / "instances.json"
    NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")

    def __init__(self, config_dir: Path | None = None):
        if config_dir is not None:
            self.CONFIG_DIR = config_dir
            self.CONFIG_FILE = config_dir / "instances.json"
        self._ensure_config_dir()
        self._data = self._load()

    def _ensure_config_dir(self) -> None:
        self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, Any]:
        if not self.CONFIG_FILE.exists():
            return {"version": 1, "current": None, "instances": {}}
        try:
            return json.loads(self.CONFIG_FILE.read_text())
        except (json.JSONDecodeError, IOError):
            return {"version": 1, "current": None, "instances": {}}

    def _save(self) -> None:
        self.CONFIG_FILE.write_text(json.dumps(self._data, indent=2))

    def get_current(self) -> str | None:
        """Get name of current instance."""
        return self._data.get("current")

    def set_current(self, name: str) -> None:
        """Set current instance by name."""
        if name not in self._data["instances"]:
            raise ValueError(f"Instance '{name}' not found")
        self._data["current"] = name
        self._save()

    def get(self, name: str) -> InstanceConfig | None:
        """Get instance config by name."""
        data = self._data["instances"].get(name)
        return InstanceConfig.from_dict(name, data) if data else None

    def list_all(self) -> list[InstanceConfig]:
        """List all instances."""
        return [
            InstanceConfig.from_dict(name, data)
            for name, data in self._data["instances"].items()
        ]

    def add(self, config: InstanceConfig) -> None:
        """Add a new instance."""
        if config.name in self._data["instances"]:
            raise ValueError(f"Instance '{config.name}' already exists")
        if not self.NAME_PATTERN.match(config.name):
            raise ValueError(f"Invalid name: must start with letter, contain only alphanumeric, dashes, underscores")
        self._data["instances"][config.name] = config.to_dict()
        self._save()

    def update(self, config: InstanceConfig) -> None:
        """Update an existing instance."""
        if config.name not in self._data["instances"]:
            raise ValueError(f"Instance '{config.name}' not found")
        self._data["instances"][config.name] = config.to_dict()
        self._save()

    def remove(self, name: str) -> None:
        """Remove an instance from registry."""
        if name not in self._data["instances"]:
            raise ValueError(f"Instance '{name}' not found")
        del self._data["instances"][name]
        if self._data["current"] == name:
            self._data["current"] = None
        self._save()

    def exists(self, name: str) -> bool:
        """Check if instance exists."""
        return name in self._data["instances"]

    def dsn_for(self, name: str) -> str:
        """Get DSN for an instance."""
        config = self.get(name)
        if not config:
            raise ValueError(f"Instance '{name}' not found")
        return config.dsn()


def validate_instance_name(name: str) -> None:
    """Raise ValueError if name is invalid."""
    if not InstanceRegistry.NAME_PATTERN.match(name):
        raise ValueError(
            f"Invalid instance name '{name}'. "
            "Must start with letter, contain only alphanumeric, dashes, underscores."
        )


def resolve_instance() -> str | None:
    """Get current instance from HEXIS_INSTANCE env or registry."""
    from_env = os.getenv("HEXIS_INSTANCE")
    if from_env:
        return from_env
    return InstanceRegistry().get_current()
