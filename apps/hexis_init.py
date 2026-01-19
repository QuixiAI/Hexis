from __future__ import annotations

import argparse
import asyncio
import os
import sys
from getpass import getpass

from dotenv import load_dotenv

from core import agent_api


def _print_err(msg: str) -> None:
    sys.stderr.write(msg + "\n")


def _prompt(
    label: str,
    *,
    default: str | None = None,
    required: bool = False,
    secret: bool = False,
) -> str:
    while True:
        suffix = f" [{default}]" if default is not None and default != "" else ""
        prompt = f"{label}{suffix}: "
        raw = getpass(prompt) if secret else input(prompt)
        value = raw.strip()
        if not value and default is not None:
            value = str(default)
        if required and not value:
            _print_err("Value required.")
            continue
        return value


def _prompt_int(label: str, *, default: int, min_value: int | None = None) -> int:
    while True:
        raw = _prompt(label, default=str(default), required=True)
        try:
            value = int(raw)
        except ValueError:
            _print_err("Enter an integer.")
            continue
        if min_value is not None and value < min_value:
            _print_err(f"Must be >= {min_value}.")
            continue
        return value


def _prompt_float(label: str, *, default: float, min_value: float | None = None) -> float:
    while True:
        raw = _prompt(label, default=str(default), required=True)
        try:
            value = float(raw)
        except ValueError:
            _print_err("Enter a number.")
            continue
        if min_value is not None and value < min_value:
            _print_err(f"Must be >= {min_value}.")
            continue
        return value


def _prompt_yes_no(label: str, *, default: bool) -> bool:
    default_str = "y" if default else "n"
    while True:
        raw = _prompt(label, default=default_str).lower()
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        _print_err("Enter y/n.")


def _prompt_list(label: str, *, required: bool = False) -> list[str]:
    print(f"{label} (one per line; blank to finish):")
    items: list[str] = []
    while True:
        raw = input("> ").strip()
        if not raw:
            if required and not items:
                _print_err("At least one item required.")
                continue
            return items
        items.append(raw)


async def _run_init(dsn: str, *, wait_seconds: int) -> int:
    await agent_api.ensure_schema_has_config(dsn, wait_seconds=wait_seconds)
    defaults = await agent_api.get_init_defaults(dsn, wait_seconds=wait_seconds)
    default_interval = int(defaults.get("heartbeat_interval_minutes", 60))
    default_max_energy = float(defaults.get("max_energy", 20))
    default_regen = float(defaults.get("base_regeneration", 10))
    default_max_active_goals = int(defaults.get("max_active_goals", 3))
    default_maint_interval = int(defaults.get("maintenance_interval_seconds", 60))
    default_subcon_interval = int(defaults.get("subconscious_interval_seconds", 300))

    print("Hexis init: configure heartbeat + objectives + guardrails.\n")

    heartbeat_interval = _prompt_int(
        "Heartbeat interval (minutes)", default=default_interval, min_value=1
    )
    maintenance_interval = _prompt_int(
        "Subconscious maintenance interval (seconds)",
        default=default_maint_interval,
        min_value=1,
    )
    subconscious_interval = _prompt_int(
        "Subconscious decider interval (seconds)",
        default=default_subcon_interval,
        min_value=1,
    )
    max_energy = _prompt_float("Max energy budget", default=default_max_energy, min_value=0.0)
    base_regeneration = _prompt_float(
        "Energy regenerated per heartbeat", default=default_regen, min_value=0.0
    )
    max_active_goals = _prompt_int(
        "Max active goals", default=default_max_active_goals, min_value=0
    )

    objectives = _prompt_list("Major objectives", required=True)
    guardrails = _prompt_list("Guardrails / boundaries (plain language)", required=False)
    initial_message = _prompt(
        "Initial message to Hexis (stored + provided to the heartbeat)",
        default="",
        required=False,
    )

    print("\nModel configuration (stored in DB; worker will also use env vars for keys).")
    hb_provider = _prompt(
        "Heartbeat model provider (openai|anthropic|openai_compatible|ollama)",
        default=os.getenv("LLM_PROVIDER", "openai"),
        required=True,
    )
    hb_model = _prompt("Heartbeat model", default=os.getenv("LLM_MODEL", "gpt-4o"), required=True)
    hb_endpoint = _prompt(
        "Heartbeat endpoint (blank for provider default)",
        default=os.getenv("OPENAI_BASE_URL", ""),
        required=False,
    )
    hb_key_env = _prompt(
        "Heartbeat API key env var name (e.g. OPENAI_API_KEY; blank for none)",
        default="OPENAI_API_KEY" if hb_provider.startswith("openai") else "",
        required=False,
    )

    chat_provider = _prompt(
        "Chat model provider (openai|anthropic|openai_compatible|ollama)",
        default=hb_provider,
        required=True,
    )
    chat_model = _prompt("Chat model", default=hb_model, required=True)
    chat_endpoint = _prompt("Chat endpoint (blank for provider default)", default=hb_endpoint, required=False)
    chat_key_env = _prompt(
        "Chat API key env var name (blank for none)",
        default=hb_key_env,
        required=False,
    )

    use_subconscious_llm = _prompt_yes_no(
        "Use separate model for subconscious decider?",
        default=False,
    )
    if use_subconscious_llm:
        sub_provider = _prompt(
            "Subconscious model provider (openai|anthropic|openai_compatible|ollama)",
            default=hb_provider,
            required=True,
        )
        sub_model = _prompt("Subconscious model", default=hb_model, required=True)
        sub_endpoint = _prompt(
            "Subconscious endpoint (blank for provider default)",
            default=hb_endpoint,
            required=False,
        )
        sub_key_env = _prompt(
            "Subconscious API key env var name (blank for none)",
            default=hb_key_env,
            required=False,
        )
    else:
        sub_provider = hb_provider
        sub_model = hb_model
        sub_endpoint = hb_endpoint
        sub_key_env = hb_key_env

    contact_channels = _prompt_list(
        "How should Hexis reach you? (e.g. email, sms, telegram, signal) [names only]",
        required=False,
    )
    contact_details: dict[str, str] = {}
    for ch in contact_channels:
        contact_details[ch] = _prompt(f"  {ch} destination (address/handle)", default="", required=False, secret=False)

    tools = _prompt_list(
        "Tools Hexis can use (e.g. email, sms, tweet, web_research) [names only]",
        required=False,
    )

    enable_autonomy = _prompt_yes_no("Enable autonomous heartbeats now?", default=True)
    enable_maintenance = _prompt_yes_no("Enable subconscious maintenance now?", default=True)
    enable_subconscious = _prompt_yes_no("Enable subconscious decider now?", default=False)

    await agent_api.apply_agent_config(
        dsn=dsn,
        wait_seconds=wait_seconds,
        heartbeat_interval_minutes=heartbeat_interval,
        maintenance_interval_seconds=maintenance_interval,
        subconscious_interval_seconds=subconscious_interval,
        max_energy=max_energy,
        base_regeneration=base_regeneration,
        max_active_goals=max_active_goals,
        objectives=objectives,
        guardrails=guardrails,
        initial_message=initial_message,
        tools=tools,
        llm_heartbeat={
            "provider": hb_provider,
            "model": hb_model,
            "endpoint": hb_endpoint,
            "api_key_env": hb_key_env,
        },
        llm_chat={
            "provider": chat_provider,
            "model": chat_model,
            "endpoint": chat_endpoint,
            "api_key_env": chat_key_env,
        },
        llm_subconscious={
            "provider": sub_provider,
            "model": sub_model,
            "endpoint": sub_endpoint,
            "api_key_env": sub_key_env,
        },
        contact_channels=contact_channels,
        contact_destinations=contact_details,
        enable_autonomy=enable_autonomy,
        enable_maintenance=enable_maintenance,
        enable_subconscious=enable_subconscious,
        mark_configured=True,
    )

    bootstrap_error = await agent_api.bootstrap_identity(dsn, wait_seconds=wait_seconds)
    if bootstrap_error:
        _print_err(f"init warning: worldview bootstrap skipped ({bootstrap_error})")

    print("\nSaved configuration to Postgres `config` table.")
    print("Next steps:")
    print("- Start services: `docker compose up -d` (or `hexis up`)")
    print(
        "- Start workers: `docker compose --profile active up -d` "
        "(or `hexis start` / `--profile heartbeat` / `--profile maintenance`)"
    )
    print("- Verify: `SELECT is_agent_configured();`, `SELECT should_run_heartbeat();`, `SELECT should_run_maintenance();`")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hexis init", description="Interactive bootstrap for Hexis configuration (stored in Postgres).")
    p.add_argument("--dsn", default=None, help="Postgres DSN; defaults to POSTGRES_* env vars")
    p.add_argument("--wait-seconds", type=int, default=int(os.getenv("POSTGRES_WAIT_SECONDS", "30")))
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)

    if args.dsn:
        dsn = args.dsn
    else:
        dsn = agent_api.db_dsn_from_env()

    try:
        return asyncio.run(_run_init(dsn, wait_seconds=args.wait_seconds))
    except KeyboardInterrupt:
        _print_err("\nCancelled.")
        return 130
    except Exception as e:
        _print_err(f"init failed: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
