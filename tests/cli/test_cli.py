import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.cli]


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
