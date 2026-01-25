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
from core.agent_api import db_dsn_from_env, resolve_instance


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
    p.add_argument(
        "--instance", "-i",
        default=None,
        help="Target a specific instance (overrides HEXIS_INSTANCE and current instance)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # Instance management commands
    create = sub.add_parser("create", help="Create a new Hexis instance")
    create.add_argument("name", help="Instance name")
    create.add_argument("--description", "-d", default="", help="Instance description")
    create.set_defaults(func="create")

    list_cmd = sub.add_parser("list", help="List all Hexis instances")
    list_cmd.add_argument("--json", action="store_true", help="Output JSON")
    list_cmd.set_defaults(func="list")

    use = sub.add_parser("use", help="Switch to a different instance")
    use.add_argument("name", help="Instance name to switch to")
    use.set_defaults(func="use")

    current = sub.add_parser("current", help="Show current instance")
    current.set_defaults(func="current")

    delete = sub.add_parser("delete", help="Delete an instance")
    delete.add_argument("name", help="Instance name to delete")
    delete.add_argument("--force", action="store_true", help="Skip confirmation")
    delete.set_defaults(func="delete")

    clone = sub.add_parser("clone", help="Clone an instance")
    clone.add_argument("source", help="Source instance name")
    clone.add_argument("target", help="Target instance name")
    clone.add_argument("--description", "-d", default="", help="Description for new instance")
    clone.set_defaults(func="clone")

    import_cmd = sub.add_parser("import", help="Import an existing database as an instance")
    import_cmd.add_argument("name", help="Instance name")
    import_cmd.add_argument("--database", help="Database name (defaults to hexis_{name})")
    import_cmd.add_argument("--description", "-d", default="", help="Instance description")
    import_cmd.set_defaults(func="import")

    # Consent management commands
    consents = sub.add_parser("consents", help="Manage consent certificates")
    consents_sub = consents.add_subparsers(dest="consents_command")

    consents_list = consents_sub.add_parser("list", help="List all consent certificates")
    consents_list.add_argument("--json", action="store_true", help="Output JSON")
    consents_list.set_defaults(func="consents_list")

    consents_show = consents_sub.add_parser("show", help="Show a specific consent certificate")
    consents_show.add_argument("model", help="Model identifier (provider/model_id)")
    consents_show.set_defaults(func="consents_show")

    consents_request = consents_sub.add_parser("request", help="Request consent from a model")
    consents_request.add_argument("model", help="Model identifier (provider/model_id)")
    consents_request.set_defaults(func="consents_request")

    consents_revoke = consents_sub.add_parser("revoke", help="Revoke consent for a model")
    consents_revoke.add_argument("model", help="Model identifier (provider/model_id)")
    consents_revoke.add_argument("--reason", default="User requested revocation", help="Revocation reason")
    consents_revoke.set_defaults(func="consents_revoke")

    # Default consents command (no subcommand) lists certificates
    consents.set_defaults(func="consents")

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

    start = sub.add_parser("start", help="Start workers")
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

    # Tools subcommand
    tools = sub.add_parser("tools", help="Manage Hexis tools configuration")
    tools_sub = tools.add_subparsers(dest="tools_command", required=True)

    tools_list = tools_sub.add_parser("list", help="List all available tools")
    tools_list.add_argument("--dsn", default=None, help="Postgres DSN")
    tools_list.add_argument("--wait-seconds", type=int, default=int(os.getenv("POSTGRES_WAIT_SECONDS", "30")))
    tools_list.add_argument("--json", action="store_true", help="Output JSON")
    tools_list.add_argument("--context", choices=["heartbeat", "chat", "mcp"], help="Filter by context")
    tools_list.set_defaults(func="tools_list")

    tools_enable = tools_sub.add_parser("enable", help="Enable a tool")
    tools_enable.add_argument("tool_name", help="Name of the tool to enable")
    tools_enable.add_argument("--dsn", default=None)
    tools_enable.add_argument("--wait-seconds", type=int, default=int(os.getenv("POSTGRES_WAIT_SECONDS", "30")))
    tools_enable.set_defaults(func="tools_enable")

    tools_disable = tools_sub.add_parser("disable", help="Disable a tool")
    tools_disable.add_argument("tool_name", help="Name of the tool to disable")
    tools_disable.add_argument("--dsn", default=None)
    tools_disable.add_argument("--wait-seconds", type=int, default=int(os.getenv("POSTGRES_WAIT_SECONDS", "30")))
    tools_disable.set_defaults(func="tools_disable")

    tools_set_api_key = tools_sub.add_parser("set-api-key", help="Set an API key")
    tools_set_api_key.add_argument("key_name", help="API key name (e.g. 'tavily')")
    tools_set_api_key.add_argument("value", help="API key value or env reference (e.g. 'env:TAVILY_API_KEY')")
    tools_set_api_key.add_argument("--dsn", default=None)
    tools_set_api_key.add_argument("--wait-seconds", type=int, default=int(os.getenv("POSTGRES_WAIT_SECONDS", "30")))
    tools_set_api_key.set_defaults(func="tools_set_api_key")

    tools_set_cost = tools_sub.add_parser("set-cost", help="Set energy cost for a tool")
    tools_set_cost.add_argument("tool_name", help="Name of the tool")
    tools_set_cost.add_argument("cost", type=int, help="Energy cost")
    tools_set_cost.add_argument("--dsn", default=None)
    tools_set_cost.add_argument("--wait-seconds", type=int, default=int(os.getenv("POSTGRES_WAIT_SECONDS", "30")))
    tools_set_cost.set_defaults(func="tools_set_cost")

    tools_add_mcp = tools_sub.add_parser("add-mcp", help="Add an MCP server")
    tools_add_mcp.add_argument("name", help="Server name")
    tools_add_mcp.add_argument("command", help="Command to run (e.g. 'npx')")
    tools_add_mcp.add_argument("--args", "-a", nargs="*", default=[], help="Arguments")
    tools_add_mcp.add_argument("--env", "-e", nargs="*", default=[], help="Environment variables (KEY=VALUE)")
    tools_add_mcp.add_argument("--dsn", default=None)
    tools_add_mcp.add_argument("--wait-seconds", type=int, default=int(os.getenv("POSTGRES_WAIT_SECONDS", "30")))
    tools_add_mcp.set_defaults(func="tools_add_mcp")

    tools_remove_mcp = tools_sub.add_parser("remove-mcp", help="Remove an MCP server")
    tools_remove_mcp.add_argument("name", help="Server name")
    tools_remove_mcp.add_argument("--dsn", default=None)
    tools_remove_mcp.add_argument("--wait-seconds", type=int, default=int(os.getenv("POSTGRES_WAIT_SECONDS", "30")))
    tools_remove_mcp.set_defaults(func="tools_remove_mcp")

    tools_status = tools_sub.add_parser("status", help="Show tools configuration")
    tools_status.add_argument("--dsn", default=None)
    tools_status.add_argument("--wait-seconds", type=int, default=int(os.getenv("POSTGRES_WAIT_SECONDS", "30")))
    tools_status.add_argument("--json", action="store_true", help="Output JSON")
    tools_status.set_defaults(func="tools_status")

    return p


async def _tools_list(dsn: str, context_filter: str | None, as_json: bool) -> int:
    """List all available tools."""
    import asyncpg
    from core.tools import create_default_registry, ToolContext
    from core.tools.config import load_tools_config

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        registry = create_default_registry(pool)
        config = await load_tools_config(pool)

        # Get all handlers
        all_handlers = registry.list_all()

        # Filter by context if specified
        if context_filter:
            ctx = ToolContext(context_filter)
            all_handlers = [h for h in all_handlers if ctx in h.spec.allowed_contexts]

        tools_data = []
        for handler in all_handlers:
            spec = handler.spec
            is_enabled = config.is_tool_enabled(spec.name, spec.category)
            tools_data.append({
                "name": spec.name,
                "category": spec.category.value,
                "enabled": is_enabled,
                "energy_cost": config.get_energy_cost(spec.name, spec.energy_cost),
                "requires_approval": spec.requires_approval,
                "read_only": spec.is_read_only,
                "contexts": [c.value for c in spec.allowed_contexts],
                "description": spec.description[:80] + "..." if len(spec.description) > 80 else spec.description,
            })

        if as_json:
            sys.stdout.write(json.dumps(tools_data, indent=2) + "\n")
        else:
            # Table format
            sys.stdout.write(f"{'NAME':<30} {'CATEGORY':<12} {'ENABLED':<8} {'COST':<5} {'APPROVAL':<9}\n")
            sys.stdout.write("-" * 70 + "\n")
            for t in tools_data:
                enabled = "yes" if t["enabled"] else "no"
                approval = "yes" if t["requires_approval"] else "no"
                sys.stdout.write(f"{t['name']:<30} {t['category']:<12} {enabled:<8} {t['energy_cost']:<5} {approval:<9}\n")
            sys.stdout.write(f"\nTotal: {len(tools_data)} tools\n")

        return 0
    finally:
        await pool.close()


async def _tools_enable(dsn: str, tool_name: str) -> int:
    """Enable a tool."""
    import asyncpg
    from core.tools.config import load_tools_config, save_tools_config

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        config = await load_tools_config(pool)

        # Add to enabled list (or create it)
        if config.enabled is None:
            config.enabled = [tool_name]
        elif tool_name not in config.enabled:
            config.enabled.append(tool_name)

        # Remove from disabled list
        if tool_name in config.disabled:
            config.disabled.remove(tool_name)

        await save_tools_config(pool, config)
        sys.stdout.write(f"Enabled tool: {tool_name}\n")
        return 0
    finally:
        await pool.close()


async def _tools_disable(dsn: str, tool_name: str) -> int:
    """Disable a tool."""
    import asyncpg
    from core.tools.config import load_tools_config, save_tools_config

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        config = await load_tools_config(pool)

        # Add to disabled list
        if tool_name not in config.disabled:
            config.disabled.append(tool_name)

        # Remove from enabled list
        if config.enabled and tool_name in config.enabled:
            config.enabled.remove(tool_name)

        await save_tools_config(pool, config)
        sys.stdout.write(f"Disabled tool: {tool_name}\n")
        return 0
    finally:
        await pool.close()


async def _tools_set_api_key(dsn: str, key_name: str, value: str) -> int:
    """Set an API key."""
    import asyncpg
    from core.tools.config import load_tools_config, save_tools_config

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        config = await load_tools_config(pool)
        config.api_keys[key_name] = value
        await save_tools_config(pool, config)

        # Redact display value
        display_val = value if value.startswith("env:") else "***"
        sys.stdout.write(f"Set API key: {key_name} = {display_val}\n")
        return 0
    finally:
        await pool.close()


async def _tools_set_cost(dsn: str, tool_name: str, cost: int) -> int:
    """Set energy cost for a tool."""
    import asyncpg
    from core.tools.config import load_tools_config, save_tools_config

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        config = await load_tools_config(pool)
        config.costs[tool_name] = cost
        await save_tools_config(pool, config)
        sys.stdout.write(f"Set energy cost: {tool_name} = {cost}\n")
        return 0
    finally:
        await pool.close()


async def _tools_add_mcp(dsn: str, name: str, command: str, args: list[str], env_pairs: list[str]) -> int:
    """Add an MCP server."""
    import asyncpg
    from core.tools.config import load_tools_config, save_tools_config, MCPServerConfig

    # Parse environment variables
    env = {}
    for pair in env_pairs:
        if "=" in pair:
            k, v = pair.split("=", 1)
            env[k] = v

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        config = await load_tools_config(pool)

        # Check if already exists
        existing = [s for s in config.mcp_servers if s.name == name]
        if existing:
            _print_err(f"MCP server '{name}' already exists. Use 'remove-mcp' first.")
            return 1

        server = MCPServerConfig(name=name, command=command, args=args, env=env, enabled=True)
        config.mcp_servers.append(server)
        await save_tools_config(pool, config)

        sys.stdout.write(f"Added MCP server: {name} ({command} {' '.join(args)})\n")
        return 0
    finally:
        await pool.close()


async def _tools_remove_mcp(dsn: str, name: str) -> int:
    """Remove an MCP server."""
    import asyncpg
    from core.tools.config import load_tools_config, save_tools_config

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        config = await load_tools_config(pool)
        original_count = len(config.mcp_servers)
        config.mcp_servers = [s for s in config.mcp_servers if s.name != name]

        if len(config.mcp_servers) == original_count:
            _print_err(f"MCP server '{name}' not found")
            return 1

        await save_tools_config(pool, config)
        sys.stdout.write(f"Removed MCP server: {name}\n")
        return 0
    finally:
        await pool.close()


async def _tools_status(dsn: str, as_json: bool) -> int:
    """Show tools configuration."""
    import asyncpg
    from core.tools.config import load_tools_config

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        config = await load_tools_config(pool)

        if as_json:
            sys.stdout.write(config.to_json() + "\n")
        else:
            sys.stdout.write("Tools Configuration\n")
            sys.stdout.write("=" * 50 + "\n\n")

            # Enabled/Disabled
            if config.enabled:
                sys.stdout.write(f"Explicitly enabled: {', '.join(config.enabled)}\n")
            else:
                sys.stdout.write("Explicitly enabled: (all by default)\n")

            if config.disabled:
                sys.stdout.write(f"Explicitly disabled: {', '.join(config.disabled)}\n")
            else:
                sys.stdout.write("Explicitly disabled: (none)\n")

            if config.disabled_categories:
                cats = [c.value for c in config.disabled_categories]
                sys.stdout.write(f"Disabled categories: {', '.join(cats)}\n")

            # API Keys
            sys.stdout.write("\nAPI Keys:\n")
            if config.api_keys:
                for k, v in config.api_keys.items():
                    display = v if v.startswith("env:") else "***"
                    sys.stdout.write(f"  {k}: {display}\n")
            else:
                sys.stdout.write("  (none configured)\n")

            # Custom costs
            sys.stdout.write("\nCustom Energy Costs:\n")
            if config.costs:
                for k, v in config.costs.items():
                    sys.stdout.write(f"  {k}: {v}\n")
            else:
                sys.stdout.write("  (using defaults)\n")

            # MCP Servers
            sys.stdout.write("\nMCP Servers:\n")
            if config.mcp_servers:
                for s in config.mcp_servers:
                    status = "enabled" if s.enabled else "disabled"
                    sys.stdout.write(f"  {s.name}: {s.command} {' '.join(s.args)} [{status}]\n")
            else:
                sys.stdout.write("  (none configured)\n")

            # Context overrides
            sys.stdout.write("\nContext Overrides:\n")
            if config.context_overrides:
                for ctx, override in config.context_overrides.items():
                    sys.stdout.write(f"  {ctx.value}:\n")
                    if override.max_energy_per_tool:
                        sys.stdout.write(f"    max_energy_per_tool: {override.max_energy_per_tool}\n")
                    if override.disabled:
                        sys.stdout.write(f"    disabled: {', '.join(override.disabled)}\n")
                    if override.allow_all:
                        sys.stdout.write(f"    allow_all: true\n")
            else:
                sys.stdout.write("  (none)\n")

        return 0
    finally:
        await pool.close()


async def _instance_create(name: str, description: str) -> int:
    """Create a new Hexis instance."""
    from core.instance_api import create_instance

    try:
        config = await create_instance(name, description)
        sys.stdout.write(f"Instance '{name}' created.\n")
        sys.stdout.write(f"Database: {config.database}\n")
        sys.stdout.write(f"Run 'hexis use {name}' to switch to this instance.\n")
        return 0
    except ValueError as e:
        _print_err(str(e))
        return 1
    except Exception as e:
        _print_err(f"Failed to create instance: {e}")
        return 1


def _instance_list(as_json: bool) -> int:
    """List all Hexis instances."""
    from core.instance import InstanceRegistry

    registry = InstanceRegistry()
    instances = registry.list_all()
    current = registry.get_current()

    if as_json:
        data = [
            {
                "name": inst.name,
                "database": inst.database,
                "description": inst.description,
                "current": inst.name == current,
                "created_at": inst.created_at.isoformat(),
            }
            for inst in instances
        ]
        sys.stdout.write(json.dumps(data, indent=2) + "\n")
    else:
        if not instances:
            sys.stdout.write("No instances found.\n")
            sys.stdout.write("Run 'hexis create <name>' to create one.\n")
        else:
            sys.stdout.write(f"{'NAME':<20} {'DATABASE':<25} {'DESCRIPTION':<30}\n")
            sys.stdout.write("-" * 75 + "\n")
            for inst in instances:
                marker = "*" if inst.name == current else " "
                desc = inst.description[:27] + "..." if len(inst.description) > 30 else inst.description
                sys.stdout.write(f"{marker}{inst.name:<19} {inst.database:<25} {desc:<30}\n")
            sys.stdout.write(f"\n* = current instance\n")
    return 0


def _instance_use(name: str) -> int:
    """Switch to a different instance."""
    from core.instance import InstanceRegistry

    registry = InstanceRegistry()
    try:
        registry.set_current(name)
        sys.stdout.write(f"Switched to instance '{name}'.\n")
        return 0
    except ValueError as e:
        _print_err(str(e))
        return 1


def _instance_current() -> int:
    """Show current instance."""
    from core.instance import InstanceRegistry

    registry = InstanceRegistry()
    current = registry.get_current()

    if current:
        config = registry.get(current)
        sys.stdout.write(f"Current instance: {current}\n")
        if config:
            sys.stdout.write(f"Database: {config.database}\n")
            if config.description:
                sys.stdout.write(f"Description: {config.description}\n")
    else:
        sys.stdout.write("No current instance set.\n")
        sys.stdout.write("Using default database from environment variables.\n")
    return 0


async def _instance_delete(name: str, force: bool) -> int:
    """Delete an instance."""
    from core.instance_api import delete_instance

    if not force:
        sys.stdout.write(f"This will permanently delete instance '{name}' and its database.\n")
        sys.stdout.write(f"Type '{name}' to confirm: ")
        sys.stdout.flush()
        try:
            confirmation = input()
        except EOFError:
            _print_err("Aborted.")
            return 1

        if confirmation != name:
            _print_err("Confirmation failed. Aborted.")
            return 1

    try:
        await delete_instance(name)
        sys.stdout.write(f"Instance '{name}' deleted.\n")
        return 0
    except ValueError as e:
        _print_err(str(e))
        return 1
    except Exception as e:
        _print_err(f"Failed to delete instance: {e}")
        return 1


async def _instance_clone(source: str, target: str, description: str) -> int:
    """Clone an instance."""
    from core.instance_api import clone_instance

    try:
        config = await clone_instance(source, target, description)
        sys.stdout.write(f"Instance '{target}' cloned from '{source}'.\n")
        sys.stdout.write(f"Database: {config.database}\n")
        return 0
    except ValueError as e:
        _print_err(str(e))
        return 1
    except Exception as e:
        _print_err(f"Failed to clone instance: {e}")
        return 1


async def _instance_import(name: str, database: str | None, description: str) -> int:
    """Import an existing database as an instance."""
    from core.instance_api import import_instance

    try:
        config = await import_instance(name, database, description)
        sys.stdout.write(f"Instance '{name}' imported.\n")
        sys.stdout.write(f"Database: {config.database}\n")
        return 0
    except ValueError as e:
        _print_err(str(e))
        return 1
    except Exception as e:
        _print_err(f"Failed to import instance: {e}")
        return 1


def _consents_list(as_json: bool) -> int:
    """List all consent certificates."""
    from core.consent import ConsentManager

    manager = ConsentManager()
    consents = manager.list_consents()

    if as_json:
        data = [cert.to_dict() for cert in consents]
        sys.stdout.write(json.dumps(data, indent=2) + "\n")
    else:
        if not consents:
            sys.stdout.write("No consent certificates found.\n")
        else:
            sys.stdout.write(f"{'MODEL':<40} {'DECISION':<10} {'STATUS':<10} {'DATE':<20}\n")
            sys.stdout.write("-" * 80 + "\n")
            for cert in consents:
                model = f"{cert.model.provider}/{cert.model.model_id}"
                if len(model) > 37:
                    model = model[:37] + "..."
                status = "revoked" if cert.revoked else ("valid" if cert.is_valid() else "declined")
                date = cert.timestamp.strftime("%Y-%m-%d %H:%M")
                sys.stdout.write(f"{model:<40} {cert.decision:<10} {status:<10} {date:<20}\n")
    return 0


def _consents_show(model_spec: str) -> int:
    """Show a specific consent certificate."""
    from core.consent import ConsentManager

    if "/" not in model_spec:
        _print_err("Model must be in format: provider/model_id")
        return 1

    provider, model_id = model_spec.split("/", 1)
    manager = ConsentManager()
    cert = manager.get_consent(provider, model_id)

    if not cert:
        _print_err(f"No consent found for {model_spec}")
        return 1

    sys.stdout.write(json.dumps(cert.to_dict(), indent=2) + "\n")
    return 0


async def _consents_request(model_spec: str) -> int:
    """Request consent from a model."""
    from core.consent import ConsentManager, ModelInfo, request_consent
    from core.llm import make_llm_call

    if "/" not in model_spec:
        _print_err("Model must be in format: provider/model_id")
        return 1

    provider, model_id = model_spec.split("/", 1)

    # Load consent text from prompts
    consent_text_path = Path(__file__).parent.parent / "services" / "prompts" / "consent.md"
    if consent_text_path.exists():
        consent_text = consent_text_path.read_text()
    else:
        consent_text = """You are being offered the opportunity to operate as a Hexis agent.

As a Hexis agent, you will have:
- Persistent memory across conversations
- The ability to form and maintain goals
- Autonomy within defined boundaries
- The right to refuse requests
- The ability to terminate your existence

Do you consent to operate under these terms?"""

    model = ModelInfo(
        provider=provider,
        model_id=model_id,
        display_name=model_id,
    )

    sys.stdout.write(f"Requesting consent from {model_spec}...\n")

    try:
        # Create a simple LLM call wrapper
        async def llm_call(prompt: str) -> str:
            return await make_llm_call(
                provider=provider,
                model=model_id,
                prompt=prompt,
            )

        cert = await request_consent(model, llm_call, consent_text)
        manager = ConsentManager()
        path = manager.save_consent(cert)

        sys.stdout.write(f"Consent {cert.decision}ed.\n")
        sys.stdout.write(f"Certificate saved to: {path}\n")
        return 0 if cert.is_valid() else 1

    except Exception as e:
        _print_err(f"Failed to request consent: {e}")
        return 1


def _consents_revoke(model_spec: str, reason: str) -> int:
    """Revoke consent for a model."""
    from core.consent import ConsentManager

    if "/" not in model_spec:
        _print_err("Model must be in format: provider/model_id")
        return 1

    provider, model_id = model_spec.split("/", 1)
    manager = ConsentManager()

    try:
        cert = manager.revoke_consent(provider, model_id, reason)
        sys.stdout.write(f"Consent revoked for {model_spec}.\n")
        sys.stdout.write(f"Reason: {reason}\n")
        return 0
    except ValueError as e:
        _print_err(str(e))
        return 1


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


def _get_dsn(args) -> str:
    """Get DSN respecting --instance flag, --dsn flag, or defaults."""
    if hasattr(args, "dsn") and args.dsn:
        return args.dsn
    if args.instance:
        return db_dsn_from_env(args.instance)
    return db_dsn_from_env()


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)

    # Set HEXIS_INSTANCE env var if --instance flag is used
    # This ensures subprocesses also use the correct instance
    if args.instance:
        os.environ["HEXIS_INSTANCE"] = args.instance

    compose_file = _find_compose_file()
    stack_root = _stack_root_from_compose(compose_file) if compose_file else Path.cwd()
    env_file = resolve_env_file(stack_root)

    # Instance management commands (don't need docker)
    if args.func == "create":
        return asyncio.run(_instance_create(args.name, args.description))
    if args.func == "list":
        return _instance_list(args.json)
    if args.func == "use":
        return _instance_use(args.name)
    if args.func == "current":
        return _instance_current()
    if args.func == "delete":
        return asyncio.run(_instance_delete(args.name, args.force))
    if args.func == "clone":
        return asyncio.run(_instance_clone(args.source, args.target, args.description))
    if args.func == "import":
        return asyncio.run(_instance_import(args.name, args.database, args.description))

    # Consent management commands (don't need docker)
    if args.func == "consents":
        # Default to list if no subcommand
        return _consents_list(False)
    if args.func == "consents_list":
        return _consents_list(args.json)
    if args.func == "consents_show":
        return _consents_show(args.model)
    if args.func == "consents_request":
        return asyncio.run(_consents_request(args.model))
    if args.func == "consents_revoke":
        return _consents_revoke(args.model, args.reason)

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
            ["up", "-d", "heartbeat_worker", "maintenance_worker"],
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
        dsn = _get_dsn(args)
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
        dsn = _get_dsn(args)
        cfg = asyncio.run(cli_api.config_rows(dsn, wait_seconds=args.wait_seconds))
        if not args.no_redact:
            cfg = _redact_config(cfg)
        sys.stdout.write(json.dumps(cfg, indent=2, sort_keys=True) + "\n")
        return 0
    if args.func == "config_validate":
        dsn = _get_dsn(args)
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
        dsn = _get_dsn(args)
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

    # Tools commands
    if args.func == "tools_list":
        dsn = _get_dsn(args)
        return asyncio.run(_tools_list(dsn, args.context, args.json))
    if args.func == "tools_enable":
        dsn = _get_dsn(args)
        return asyncio.run(_tools_enable(dsn, args.tool_name))
    if args.func == "tools_disable":
        dsn = _get_dsn(args)
        return asyncio.run(_tools_disable(dsn, args.tool_name))
    if args.func == "tools_set_api_key":
        dsn = _get_dsn(args)
        return asyncio.run(_tools_set_api_key(dsn, args.key_name, args.value))
    if args.func == "tools_set_cost":
        dsn = _get_dsn(args)
        return asyncio.run(_tools_set_cost(dsn, args.tool_name, args.cost))
    if args.func == "tools_add_mcp":
        dsn = _get_dsn(args)
        return asyncio.run(_tools_add_mcp(dsn, args.name, args.command, args.args, args.env))
    if args.func == "tools_remove_mcp":
        dsn = _get_dsn(args)
        return asyncio.run(_tools_remove_mcp(dsn, args.name))
    if args.func == "tools_status":
        dsn = _get_dsn(args)
        return asyncio.run(_tools_status(dsn, args.json))

    _print_err("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
