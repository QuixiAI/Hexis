import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from core.instance import InstanceRegistry

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.cli]


# Instance CLI tests

@pytest.fixture
def temp_hexis_dir(tmp_path):
    """Create a temporary .hexis directory for instance tests."""
    hexis_dir = tmp_path / ".hexis"
    hexis_dir.mkdir()
    return hexis_dir


async def test_cli_instance_list_empty(temp_hexis_dir):
    """Test listing instances when none exist."""
    env = os.environ.copy()
    with patch.object(InstanceRegistry, "CONFIG_DIR", temp_hexis_dir):
        with patch.object(InstanceRegistry, "CONFIG_FILE", temp_hexis_dir / "instances.json"):
            p = subprocess.run(
                [sys.executable, "-m", "apps.hexis_cli", "list"],
                capture_output=True,
                text=True,
                env=env,
                cwd=str(Path(__file__).resolve().parents[1]),
            )
    assert p.returncode == 0
    assert "No instances" in p.stdout or p.stdout.strip() == ""


async def test_cli_instance_current_none(temp_hexis_dir):
    """Test showing current instance when none is set."""
    env = os.environ.copy()
    with patch.object(InstanceRegistry, "CONFIG_DIR", temp_hexis_dir):
        with patch.object(InstanceRegistry, "CONFIG_FILE", temp_hexis_dir / "instances.json"):
            p = subprocess.run(
                [sys.executable, "-m", "apps.hexis_cli", "current"],
                capture_output=True,
                text=True,
                env=env,
                cwd=str(Path(__file__).resolve().parents[1]),
            )
    assert p.returncode == 0


async def test_cli_instance_use_nonexistent():
    """Test switching to a nonexistent instance fails."""
    env = os.environ.copy()
    p = subprocess.run(
        [sys.executable, "-m", "apps.hexis_cli", "use", "nonexistent-instance-xyz"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert p.returncode != 0


# Consent CLI tests

async def test_cli_consents_list(temp_hexis_dir):
    """Test listing consent certificates."""
    env = os.environ.copy()
    consents_dir = temp_hexis_dir / "consents"
    consents_dir.mkdir()

    from core.consent import ConsentManager
    with patch.object(ConsentManager, "CONSENTS_DIR", consents_dir):
        p = subprocess.run(
            [sys.executable, "-m", "apps.hexis_cli", "consents"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
    assert p.returncode == 0


async def test_cli_consents_show_nonexistent(temp_hexis_dir):
    """Test showing a nonexistent consent certificate."""
    env = os.environ.copy()
    consents_dir = temp_hexis_dir / "consents"
    consents_dir.mkdir()

    from core.consent import ConsentManager
    with patch.object(ConsentManager, "CONSENTS_DIR", consents_dir):
        p = subprocess.run(
            [sys.executable, "-m", "apps.hexis_cli", "consents", "show", "anthropic/nonexistent"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
    assert p.returncode != 0 or "not found" in p.stderr.lower()


# Original tests


async def test_cli_status_json_no_docker(db_pool):
    env = os.environ.copy()
    p = subprocess.run(
        [sys.executable, "-m", "apps.hexis_cli", "status", "--json", "--no-docker", "--wait-seconds", "60"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert p.returncode == 0, p.stderr
    data = json.loads(p.stdout)
    assert "agent_configured" in data
    assert "pending_external_calls" in data


async def test_cli_config_show_and_validate(db_pool):
    env = os.environ.copy()

    show = subprocess.run(
        [sys.executable, "-m", "apps.hexis_cli", "config", "show", "--wait-seconds", "60"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert show.returncode == 0, show.stderr
    cfg = json.loads(show.stdout)
    assert "agent.is_configured" in cfg

    validate = subprocess.run(
        [sys.executable, "-m", "apps.hexis_cli", "config", "validate", "--wait-seconds", "60"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert validate.returncode == 0, validate.stderr


async def test_cli_config_validate_fails_when_unconfigured(db_pool):
    async with db_pool.acquire() as conn:
        await conn.execute("SELECT set_config('agent.is_configured', 'false'::jsonb)")
    try:
        env = os.environ.copy()
        validate = subprocess.run(
            [sys.executable, "-m", "apps.hexis_cli", "config", "validate", "--wait-seconds", "60"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        assert validate.returncode != 0
        assert "agent.is_configured is not true" in (validate.stderr + validate.stdout)
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("SELECT set_config('agent.is_configured', 'true'::jsonb)")
