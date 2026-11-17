"""Skills as the capability catalog (#39/#41): manifest MCP bindings parse,
usability is tri-state with exact next steps, unmet skills stay visible, and
configured-but-unbound MCP servers surface as implicit skills.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.tools.config import MCPServerConfig, ToolsConfig
from services.skill_runtime import (
    format_skills_prompt,
    load_available_skills,
    select_skills,
    skill_catalog,
    synthesize_implicit_mcp_skills,
)
from skills.base import MCPBinding, SkillSpec

pytestmark = pytest.mark.asyncio(loop_scope="session")


def test_mcp_binding_parses_from_frontmatter():
    spec = SkillSpec.from_frontmatter(
        {
            "name": "github-issues",
            "description": "GitHub issues",
            "mcp": {
                "server": "github",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env_requires": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
            },
            "bound_tools": ["mcp_github_create_issue", "mcp_github_search_issues"],
        },
        "body",
    )
    binding = spec.mcp_binding
    assert binding is not None
    assert binding.server == "github"
    assert binding.command == "npx"
    assert binding.args == ["-y", "@modelcontextprotocol/server-github"]
    # env-name-only invariant: names, never values.
    assert binding.env_requires == ["GITHUB_PERSONAL_ACCESS_TOKEN"]

    # No mcp block -> no binding.
    plain = SkillSpec.from_frontmatter({"name": "plain"}, "body")
    assert plain.mcp_binding is None


def test_requirements_ignore_mcp_prefixed_tools():
    spec = SkillSpec.from_frontmatter(
        {
            "name": "x",
            "requires": {"tools": ["recall", "mcp_github_create_issue"]},
        },
        "body",
    )
    # mcp_* tools exist only after activation; only native tools gate loading.
    assert spec.requirements_met({"recall"}) is True
    assert spec.requirements_met(set()) is False


class TestUsability:
    def _skill(self, **overrides) -> SkillSpec:
        base = dict(
            name="test-skill",
            description="d",
            content="c",
        )
        base.update(overrides)
        return SkillSpec(**base)

    def test_usable_when_everything_present(self):
        skill = self._skill(requires_tools=["recall"])
        status, missing, next_step = skill.usability({"recall"}, set())
        assert status == "usable"
        assert not missing

    def test_missing_native_tool_is_unavailable(self):
        skill = self._skill(requires_tools=["nonexistent_tool"])
        status, missing, next_step = skill.usability(set(), set())
        assert status == "unavailable"
        assert "missing tool: nonexistent_tool" in missing
        assert next_step

    def test_missing_env_is_needs_setup_with_exact_step(self, monkeypatch):
        monkeypatch.delenv("HEXIS_TEST_TOKEN_XYZ", raising=False)
        skill = self._skill(
            mcp_binding=MCPBinding(
                server="stub", command="python3", env_requires=["HEXIS_TEST_TOKEN_XYZ"]
            )
        )
        status, missing, next_step = skill.usability(set(), set())
        assert status == "needs_setup"
        assert "missing env var: HEXIS_TEST_TOKEN_XYZ" in missing
        assert "HEXIS_TEST_TOKEN_XYZ" in next_step

    def test_unconfigured_server_without_command_is_unavailable(self):
        skill = self._skill(mcp_binding=MCPBinding(server="github"))
        status, missing, next_step = skill.usability(set(), set())
        assert status == "unavailable"
        assert "mcp server not configured: github" in missing
        assert "mcp_servers" in next_step

        # A configured server of that name flips it to usable.
        status2, _, _ = skill.usability(set(), {"github"})
        assert status2 == "usable"


def test_implicit_skills_for_unbound_configured_servers():
    configs = [
        MCPServerConfig(name="github", command="npx", args=[]),
        MCPServerConfig(name="slack", command="npx", args=[]),
        MCPServerConfig(name="disabled", command="npx", args=[], enabled=False),
    ]
    implicit = synthesize_implicit_mcp_skills(configs, bound_servers={"github"})
    names = {s.name for s in implicit}
    assert names == {"mcp-slack"}
    slack = implicit[0]
    assert slack.mcp_binding.server == "slack"
    assert slack.bound_tools == ["mcp_slack_*"]
    assert slack.provenance == {"generated": "mcp_server_config"}


def _mock_registry(
    tool_names: list[str],
    mcp_servers: list[MCPServerConfig],
    unready: dict[str, str] | None = None,
) -> MagicMock:
    registry = MagicMock()
    registry.list_names.return_value = tool_names
    registry.unready_tools = AsyncMock(return_value=unready or {})
    registry.extra_skill_dirs = []
    registry.get_config = AsyncMock(return_value=ToolsConfig(mcp_servers=mcp_servers))
    return registry


async def test_catalog_is_tri_state_and_never_silently_drops():
    from core.tools.base import ToolContext

    registry = _mock_registry(
        ["recall", "remember", "list_skills", "use_skill"],
        [MCPServerConfig(name="stubserver", command="python3", args=[])],
    )
    catalog = await skill_catalog(registry, ToolContext.CHAT)
    by_name = {entry["name"]: entry for entry in catalog}

    # github-issues (installed skill) is present with a status even though
    # its token is (presumably) not set — never silently dropped (#39).
    assert "github-issues" in by_name
    gh = by_name["github-issues"]
    assert gh["status"] in {"usable", "needs_setup"}
    assert gh["transport"] == "mcp:github"
    # MCP tools listed from the manifest, pre-activation.
    assert "mcp_github_create_issue" in gh["bound_tools"]
    if gh["status"] == "needs_setup":
        assert "GITHUB_PERSONAL_ACCESS_TOKEN" in gh["next_step"]

    # Implicit skill for the configured-but-unbound server.
    assert "mcp-stubserver" in by_name
    assert by_name["mcp-stubserver"]["status"] == "usable"

    # Every entry carries a status.
    assert all("status" in entry for entry in catalog)


async def test_load_available_skills_include_unmet_keeps_broken_skills():
    from core.tools.base import ToolContext

    registry = _mock_registry([], [])
    met_only = load_available_skills(registry, ToolContext.CHAT)
    everything = load_available_skills(registry, ToolContext.CHAT, include_unmet=True)
    assert len(everything) >= len(met_only)


def test_prompt_instructed_tools_are_bound_in_default_chat_skills():
    """#66: every epistemics tool the conversation prompt instructs must be
    reachable through skill bindings — the prompt taught add_evidence and
    belief_history while no skill bound them, so chat never offered them."""
    from pathlib import Path

    from services.skill_runtime import DEFAULT_SKILL_NAMES, skill_bound_tools
    from skills.loader import load_skills_from_dir

    repo_skills = Path(__file__).resolve().parents[2] / "skills" / "installed"
    by_name = {s.name: s for s in load_skills_from_dir(repo_skills)}

    core_memory = skill_bound_tools(by_name["core-memory"])
    assert "add_evidence" in core_memory
    assert "belief_history" in core_memory
    assert "core-memory" in DEFAULT_SKILL_NAMES

    self_inspection = skill_bound_tools(by_name["self-inspection"])
    assert "inspect_config" in self_inspection
    assert "review_recent_actions" in self_inspection


class TestReadiness:
    def test_registered_but_unready_tool_needs_setup(self):
        skill = SkillSpec(name="calendar", description="d", content="c", requires_tools=["calendar_events"])
        status, missing, next_step = skill.usability(
            {"calendar_events"}, set(), {"calendar_events": "Connect a calendar."}
        )
        assert status == "needs_setup"
        assert missing == ["not set up: calendar_events"]
        assert next_step == "Connect a calendar."


async def test_unready_skills_are_listed_as_not_set_up_and_never_auto_activate():
    from core.tools.base import ToolContext

    registry = _mock_registry(
        ["recall", "remember", "search_history", "calendar_events", "search_contacts", "list_skills", "use_skill"],
        [],
        unready={"calendar_events": "Google Calendar is not connected."},
    )
    registry.pool = None
    selection = await select_skills(registry, ToolContext.CHAT, query="prepare my calendar meeting")
    assert "calendar" in selection.setup_steps
    assert "calendar" not in {s.name for s in selection.skills}

    prompt = format_skills_prompt(selection.skills, selection.available, selection.setup_steps)
    ready_part, _, unset_part = prompt.partition("Installed but not set up")
    assert "- calendar:" not in ready_part
    assert "- calendar:" in unset_part
    assert "Google Calendar is not connected." in unset_part

    catalog = {e["name"]: e for e in await skill_catalog(registry, ToolContext.CHAT)}
    assert catalog["calendar"]["status"] == "needs_setup"
    assert catalog["calendar"]["next_step"] == "Google Calendar is not connected."


async def test_registry_reports_unready_credential_tools():
    from core.tools.base import ToolContext
    from core.tools.calendar import create_calendar_tools
    from core.tools.registry import ToolRegistry

    registry = ToolRegistry(None)
    for handler in create_calendar_tools(credentials_resolver=lambda: None):
        registry.register(handler)
    unready = await registry.unready_tools(ToolContext.CHAT)
    assert set(unready) == {"calendar_events", "calendar_create", "calendar_update", "calendar_delete", "meeting_prep"}

    ready = ToolRegistry(None)
    for handler in create_calendar_tools(credentials_resolver=lambda: {"token": "t"}):
        ready.register(handler)
    assert await ready.unready_tools(ToolContext.CHAT) == {}
