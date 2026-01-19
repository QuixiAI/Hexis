from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from core import cli_api
from core.agent_api import db_dsn_from_env


def _print_err(msg: str) -> None:
    sys.stderr.write(msg + "\n")


def _find_compose_file(start: Path | None = None) -> Path | None:
    """
    Find docker-compose.yml (preferred) or ops/docker-compose.yml by walking up from CWD.
    """
    cur = (start or Path.cwd()).resolve()
    for parent in (cur,) + tuple(cur.parents):
        legacy_compose = parent / "docker-compose.yml"
        if legacy_compose.exists():
            return legacy_compose
        ops_compose = parent / "ops" / "docker-compose.yml"
        if ops_compose.exists():
            return ops_compose
    return None


def _stack_root_from_compose(compose_file: Path) -> Path:
    if compose_file.parent.name == "ops":
        return compose_file.parent.parent
    return compose_file.parent


def ensure_docker() -> str:
    docker_bin = shutil.which("docker")
    if not docker_bin:
        _print_err("Docker is not installed or not on PATH. Install Docker Desktop: https://docs.docker.com/get-docker/")
        raise SystemExit(1)
    try:
        subprocess.run([docker_bin, "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except subprocess.CalledProcessError:
        _print_err("Docker is installed but not running. Start Docker Desktop and retry.")
        raise SystemExit(1)
    return docker_bin


def ensure_compose(docker_bin: str) -> list[str]:
    try:
        subprocess.run([docker_bin, "compose", "version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return [docker_bin, "compose"]
    except Exception:
        pass
    compose_bin = shutil.which("docker-compose")
    if compose_bin:
        return [compose_bin]
    _print_err("Docker Compose not available. Install Compose: https://docs.docker.com/compose/install/")
    raise SystemExit(1)


def resolve_env_file(stack_root: Path) -> Path | None:
    candidates = [
        Path.cwd() / ".env",
        Path.cwd() / ".env.local",
        stack_root / ".env",
        stack_root / ".env.local",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def run_compose(
    compose_cmd: list[str],
    compose_file: Path,
    stack_root: Path,
    args: list[str],
    env_file: Path | None,
) -> int:
    cmd = compose_cmd + ["-f", str(compose_file)]
    if env_file:
        cmd += ["--env-file", str(env_file)]
    cmd += args

    try:
        result = subprocess.run(cmd, cwd=stack_root, env=os.environ.copy())
        return result.returncode
    except FileNotFoundError:
        _print_err("Failed to run docker compose. Ensure Docker is installed.")
        return 1


def _run_compose_capture(
    compose_cmd: list[str], compose_file: Path, stack_root: Path, args: list[str], env_file: Path | None
) -> tuple[int, str]:
    cmd = compose_cmd + ["-f", str(compose_file)]
    if env_file:
        cmd += ["--env-file", str(env_file)]
    cmd += args
    try:
        p = subprocess.run(cmd, cwd=stack_root, env=os.environ.copy(), capture_output=True, text=True)
        out = (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
        return p.returncode, out.strip()
    except FileNotFoundError:
        return 1, "Failed to run docker compose. Ensure Docker is installed."


def _redact_config(cfg: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(cfg))  # deep copy via json
    contact = out.get("user.contact")
    if isinstance(contact, dict):
        destinations = contact.get("destinations")
        if isinstance(destinations, dict):
            contact["destinations"] = {k: "***" for k in destinations.keys()}
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hexis", description="Manage Hexis Memory Docker stack")
    sub = p.add_subparsers(dest="command", required=True)

    up = sub.add_parser("up", help="Start the stack")
    up.add_argument("--build", action="store_true", help="Build images before starting")
    up.set_defaults(func="up")

    down = sub.add_parser("down", help="Stop the stack")
    down.set_defaults(func="down")

    logs = sub.add_parser("logs", help="Show logs")
    logs.add_argument("--follow", "-f", action="store_true", help="Follow log output")
    logs.set_defaults(func="logs")

    ps = sub.add_parser("ps", help="List services")
    ps.set_defaults(func="ps")

    chat = sub.add_parser("chat", help="Run the conversation loop (forwards args to services.conversation)")
    chat.add_argument("args", nargs=argparse.REMAINDER, help="Arguments forwarded to services.conversation")
    chat.set_defaults(func="chat")

    ingest = sub.add_parser("ingest", help="Run the ingestion pipeline (forwards args to services.ingest)")
    ingest.add_argument("args", nargs=argparse.REMAINDER, help="Arguments forwarded to services.ingest")
    ingest.set_defaults(func="ingest")

    worker = sub.add_parser("worker", help="Run background workers (forwards args to apps.worker)")
    worker.add_argument("args", nargs=argparse.REMAINDER, help="Arguments forwarded to apps.worker")
    worker.set_defaults(func="worker")

    init = sub.add_parser("init", help="Interactive Hexis setup wizard (stores config in Postgres)")
    init.add_argument("args", nargs=argparse.REMAINDER, help="Arguments forwarded to apps.hexis_init")
    init.set_defaults(func="init")

    mcp = sub.add_parser("mcp", help="Run MCP server exposing CognitiveMemory tools (stdio)")
    mcp.add_argument("args", nargs=argparse.REMAINDER, help="Arguments forwarded to apps.hexis_mcp_server")
    mcp.set_defaults(func="mcp")

    start = sub.add_parser("start", help="Start workers (active profile)")
    start.set_defaults(func="start")

    stop = sub.add_parser("stop", help="Stop workers (containers remain)")
    stop.set_defaults(func="stop")

    status = sub.add_parser("status", help="Show system status (db/config/queue)")
    status.add_argument("--dsn", default=None, help="Postgres DSN; defaults to POSTGRES_* env vars")
    status.add_argument("--wait-seconds", type=int, default=int(os.getenv("POSTGRES_WAIT_SECONDS", "30")))
    status.add_argument("--json", action="store_true", help="Output JSON")
    status.add_argument("--no-docker", action="store_true", help="Skip docker compose checks")
    status.set_defaults(func="status")

    config = sub.add_parser("config", help="Show/validate agent configuration stored in Postgres")
    cfg_sub = config.add_subparsers(dest="config_command", required=True)

    cfg_show = cfg_sub.add_parser("show", help="Print config table")
    cfg_show.add_argument("--dsn", default=None, help="Postgres DSN; defaults to POSTGRES_* env vars")
    cfg_show.add_argument("--wait-seconds", type=int, default=int(os.getenv("POSTGRES_WAIT_SECONDS", "30")))
    cfg_show.add_argument("--json", action="store_true", help="Output JSON")
    cfg_show.add_argument("--no-redact", action="store_true", help="Do not redact contact destinations")
    cfg_show.set_defaults(func="config_show")

    cfg_validate = cfg_sub.add_parser("validate", help="Validate required config keys and environment references")
    cfg_validate.add_argument("--dsn", default=None, help="Postgres DSN; defaults to POSTGRES_* env vars")
    cfg_validate.add_argument("--wait-seconds", type=int, default=int(os.getenv("POSTGRES_WAIT_SECONDS", "30")))
    cfg_validate.set_defaults(func="config_validate")

    demo = sub.add_parser("demo", help="Run a quick end-to-end sanity check against the DB")
    demo.add_argument("--dsn", default=None, help="Postgres DSN; defaults to POSTGRES_* env vars")
    demo.add_argument("--wait-seconds", type=int, default=int(os.getenv("POSTGRES_WAIT_SECONDS", "30")))
    demo.add_argument("--json", action="store_true", help="Output JSON")
    demo.set_defaults(func="demo")

    return p


def _run_module(module: str, argv: list[str]) -> int:
    if argv and argv[0] == "--":
        argv = argv[1:]
    cmd = [sys.executable, "-m", module, *argv]
    try:
        result = subprocess.run(cmd, env=os.environ.copy())
        return result.returncode
    except FileNotFoundError:
        _print_err(f"Failed to run {cmd[0]!r}")
        return 1


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)

    compose_file = _find_compose_file()
    stack_root = _stack_root_from_compose(compose_file) if compose_file else Path.cwd()
    env_file = resolve_env_file(stack_root)

    docker_cmds = {"up", "down", "ps", "logs", "start", "stop"}
    docker_bin: str | None = None
    compose_cmd: list[str] | None = None
    if args.func in docker_cmds:
        if compose_file is None:
            _print_err("docker-compose.yml not found.")
            return 1
        docker_bin = ensure_docker()
        compose_cmd = ensure_compose(docker_bin)

    if args.func == "up":
        up_args = ["up", "-d"]
        if args.build:
            up_args.append("--build")
        return run_compose(compose_cmd or [], compose_file, stack_root, up_args, env_file)
    if args.func == "down":
        return run_compose(compose_cmd or [], compose_file, stack_root, ["down"], env_file)
    if args.func == "ps":
        return run_compose(compose_cmd or [], compose_file, stack_root, ["ps"], env_file)
    if args.func == "logs":
        log_args = ["logs"] + (["-f"] if args.follow else [])
        return run_compose(compose_cmd or [], compose_file, stack_root, log_args, env_file)
    if args.func == "chat":
        return _run_module("services.conversation", args.args)
    if args.func == "ingest":
        return _run_module("services.ingest", args.args)
    if args.func == "worker":
        return _run_module("apps.worker", args.args)
    if args.func == "init":
        return _run_module("apps.hexis_init", args.args)
    if args.func == "mcp":
        return _run_module("apps.hexis_mcp_server", args.args)
    if args.func == "start":
        return run_compose(
            compose_cmd or [],
            compose_file,
            stack_root,
            ["--profile", "active", "up", "-d", "heartbeat_worker", "maintenance_worker"],
            env_file,
        )
    if args.func == "stop":
        return run_compose(
            compose_cmd or [],
            compose_file,
            stack_root,
            ["stop", "heartbeat_worker", "maintenance_worker"],
            env_file,
        )
    if args.func == "status":
        dsn = args.dsn or db_dsn_from_env()
        payload = asyncio.run(cli_api.status_payload(dsn, wait_seconds=args.wait_seconds))
        if not args.no_docker:
            try:
                docker_bin = ensure_docker()
                compose_cmd = ensure_compose(docker_bin)
                if compose_file is None:
                    raise SystemExit
                rc, out = _run_compose_capture(compose_cmd, compose_file, stack_root, ["ps"], env_file)
                payload["docker_ps_rc"] = rc
                payload["docker_ps"] = out
            except SystemExit:
                payload["docker_ps_rc"] = 1
                payload["docker_ps"] = "Docker not available"
        if args.json:
            sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        else:
            lines = [
                f"DB time: {payload.get('db_time')}",
                f"Agent configured: {payload.get('agent_configured')}",
                f"Heartbeat paused: {payload.get('heartbeat_paused')}",
                f"Should run heartbeat: {payload.get('should_run_heartbeat')}",
                f"Maintenance paused: {payload.get('maintenance_paused')}",
                f"Should run maintenance: {payload.get('should_run_maintenance')}",
                f"Embedding URL: {payload.get('embedding_service_url')}",
                f"Embedding healthy: {payload.get('embedding_service_healthy')}",
                f"Pending external_calls: {payload.get('pending_external_calls')}",
                f"Pending outbox_messages: {payload.get('pending_outbox_messages')}",
            ]
            sys.stdout.write("\n".join(lines) + "\n")
        return 0
    if args.func == "config_show":
        dsn = args.dsn or db_dsn_from_env()
        cfg = asyncio.run(cli_api.config_rows(dsn, wait_seconds=args.wait_seconds))
        if not args.no_redact:
            cfg = _redact_config(cfg)
        sys.stdout.write(json.dumps(cfg, indent=2, sort_keys=True) + "\n")
        return 0
    if args.func == "config_validate":
        dsn = args.dsn or db_dsn_from_env()
        errors, warnings = asyncio.run(cli_api.config_validate(dsn, wait_seconds=args.wait_seconds))
        for w in warnings:
            _print_err(f"warning: {w}")
        if errors:
            for e in errors:
                _print_err(f"error: {e}")
            return 1
        sys.stdout.write("ok\n")
        return 0
    if args.func == "demo":
        dsn = args.dsn or db_dsn_from_env()
        result = asyncio.run(cli_api.demo(dsn, wait_seconds=args.wait_seconds))
        if args.json:
            sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
        else:
            sys.stdout.write(
                "Demo ok\n"
                f"- remembered_ids: {', '.join(result['remembered_ids'])}\n"
                f"- recall_count: {result['recall_count']}\n"
                f"- hydrate_memory_count: {result['hydrate_memory_count']}\n"
                f"- working_search_count: {result['working_search_count']}\n"
            )
        return 0

    _print_err("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
