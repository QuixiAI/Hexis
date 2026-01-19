from __future__ import annotations

import json
from typing import Any


def build_heartbeat_decision_prompt(context: dict[str, Any]) -> str:
    agent = context.get("agent", {})
    env = context.get("environment", {})
    goals = context.get("goals", {})
    memories = context.get("recent_memories", [])
    identity = context.get("identity", [])
    worldview = context.get("worldview", [])
    self_model = context.get("self_model", [])
    narrative = context.get("narrative", {})
    urgent_drives = context.get("urgent_drives", [])
    emotional_state = context.get("emotional_state") or {}
    relationships = context.get("relationships", [])
    contradictions = context.get("contradictions", [])
    emotional_patterns = context.get("emotional_patterns", [])
    active_transformations = context.get("active_transformations", [])
    transformations_ready = context.get("transformations_ready", [])
    energy = context.get("energy", {})
    action_costs = context.get("action_costs", {})
    hb_number = context.get("heartbeat_number", 0)

    prompt = f"""## Heartbeat #{hb_number}

## Agent Profile
Objectives:
{_format_objectives(agent.get("objectives"))}

Guardrails:
{_format_guardrails(agent.get("guardrails"))}

Tools:
{_format_tools(agent.get("tools"))}

Budget:
{json.dumps(agent.get("budget") or {})}

## Current Time
{env.get('timestamp', 'Unknown')}
Day of week: {env.get('day_of_week', '?')}, Hour: {env.get('hour_of_day', '?')}

## Environment
- Time since last user interaction: {env.get('time_since_user_hours', 'Never')} hours
- Pending events: {env.get('pending_events', 0)}

## Your Goals
Active ({goals.get('counts', {}).get('active', 0)}):
{_format_goals(goals.get('active', []))}

Queued ({goals.get('counts', {}).get('queued', 0)}):
{_format_goals(goals.get('queued', []))}

Issues:
{_format_issues(goals.get('issues', []))}

## Narrative
{_format_narrative(narrative)}

## Recent Experience
{_format_memories(memories)}

## Your Identity
{_format_identity(identity)}

## Your Self-Model
{_format_self_model(self_model)}

## Relationships
{_format_relationships(relationships)}

## Your Beliefs
{_format_worldview(worldview)}

## Contradictions
{_format_contradictions(contradictions)}

## Emotional Patterns
{_format_emotional_patterns(emotional_patterns)}

## Active Transformations
{_format_transformations(active_transformations)}

## Transformations Ready
{_format_transformations(transformations_ready)}

## Current Emotional State
{_format_emotional_state(emotional_state)}

## Urgent Drives
{_format_drives(urgent_drives)}

## Energy
Available: {energy.get('current', 0)}
Max: {energy.get('max', 20)}

## Action Costs
{_format_costs(action_costs)}

---

What do you want to do this heartbeat? Respond with STRICT JSON."""

    return prompt


def _format_goals(goals: list[Any]) -> str:
    if not goals:
        return "  (none)"
    return "\n".join(f"  - {g.get('title', 'Untitled')}" for g in goals)


def _format_issues(issues: list[Any]) -> str:
    if not issues:
        return "  (none)"
    return "\n".join(
        f"  - {i.get('title', 'Unknown')}: {i.get('issue', 'unknown issue')}"
        for i in issues
    )


def _format_memories(memories: list[Any]) -> str:
    if not memories:
        return "  (no recent memories)"
    return "\n".join(
        f"  - {m.get('content', '')[:100]}..."
        for m in memories[:5]
    )


def _format_identity(identity: list[Any]) -> str:
    if not identity:
        return "  (no identity aspects defined)"
    return "\n".join(
        f"  - {i.get('type', 'unknown')}: {json.dumps(i.get('content', {}))[:100]}"
        for i in identity[:3]
    )


def _format_objectives(objectives: Any) -> str:
    if not isinstance(objectives, list) or not objectives:
        return "  (none)"
    lines: list[str] = []
    for obj in objectives[:8]:
        if isinstance(obj, str):
            lines.append(f"  - {obj}")
        elif isinstance(obj, dict):
            title = obj.get("title") or obj.get("name") or "Objective"
            desc = obj.get("description") or obj.get("details") or ""
            lines.append(f"  - {title}{(': ' + desc) if desc else ''}")
    return "\n".join(lines) if lines else "  (none)"


def _format_guardrails(guardrails: Any) -> str:
    if not isinstance(guardrails, list) or not guardrails:
        return "  (none)"
    lines: list[str] = []
    for g in guardrails[:10]:
        if isinstance(g, str):
            lines.append(f"  - {g}")
        elif isinstance(g, dict):
            name = g.get("name") or "guardrail"
            desc = g.get("description") or ""
            lines.append(f"  - {name}{(': ' + desc) if desc else ''}")
    return "\n".join(lines) if lines else "  (none)"


def _format_tools(tools: Any) -> str:
    if not isinstance(tools, list) or not tools:
        return "  (none)"
    lines: list[str] = []
    for t in tools[:10]:
        if isinstance(t, str):
            lines.append(f"  - {t}")
        elif isinstance(t, dict):
            name = t.get("name") or "tool"
            desc = t.get("description") or ""
            lines.append(f"  - {name}{(': ' + desc) if desc else ''}")
    return "\n".join(lines) if lines else "  (none)"


def _format_narrative(narrative: Any) -> str:
    if not isinstance(narrative, dict):
        return "  (none)"
    cur = narrative.get("current_chapter") if isinstance(narrative.get("current_chapter"), dict) else {}
    name = cur.get("name") or "Foundations"
    return f"  - Current chapter: {name}"


def _format_self_model(self_model: Any) -> str:
    if not isinstance(self_model, list) or not self_model:
        return "  (empty)"
    lines: list[str] = []
    for item in self_model[:8]:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind") or "associated"
        concept = item.get("concept") or "?"
        strength = item.get("strength")
        strength_txt = f" ({strength:.2f})" if isinstance(strength, (int, float)) else ""
        lines.append(f"  - {kind}: {concept}{strength_txt}")
    return "\n".join(lines) if lines else "  (empty)"


def _format_relationships(relationships: Any) -> str:
    if not isinstance(relationships, list) or not relationships:
        return "  (none)"
    lines: list[str] = []
    for rel in relationships[:8]:
        if not isinstance(rel, dict):
            continue
        entity = rel.get("entity") or "unknown"
        strength = rel.get("strength")
        strength_txt = f" ({strength:.2f})" if isinstance(strength, (int, float)) else ""
        lines.append(f"  - {entity}{strength_txt}")
    return "\n".join(lines) if lines else "  (none)"


def _format_emotional_state(emotional_state: Any) -> str:
    if not isinstance(emotional_state, dict) or not emotional_state:
        return "  (none)"
    primary = emotional_state.get("primary_emotion") or "unknown"
    val = emotional_state.get("valence")
    ar = emotional_state.get("arousal")
    parts = [f"  - primary_emotion: {primary}"]
    if isinstance(val, (int, float)):
        parts.append(f"  - valence: {val:.2f}")
    if isinstance(ar, (int, float)):
        parts.append(f"  - arousal: {ar:.2f}")
    return "\n".join(parts)


def _format_drives(urgent_drives: Any) -> str:
    if not isinstance(urgent_drives, list) or not urgent_drives:
        return "  (none)"
    lines: list[str] = []
    for d in urgent_drives[:8]:
        if not isinstance(d, dict):
            continue
        name = d.get("name") or "drive"
        ratio = d.get("urgency_ratio")
        if isinstance(ratio, (int, float)):
            lines.append(f"  - {name}: {ratio:.2f}x threshold")
        else:
            level = d.get("level")
            lines.append(f"  - {name}: {level}" if level is not None else f"  - {name}")
    return "\n".join(lines) if lines else "  (none)"


def _format_worldview(worldview: list[Any]) -> str:
    if not worldview:
        return "  (no beliefs defined)"
    return "\n".join(
        f"  - [{w.get('category', '?')}] {w.get('belief', '')[:80]} (confidence: {w.get('confidence', 0):.1f})"
        for w in worldview[:3]
    )


def _format_contradictions(contradictions: Any) -> str:
    if not isinstance(contradictions, list) or not contradictions:
        return "  (none)"
    lines: list[str] = []
    for c in contradictions[:5]:
        if not isinstance(c, dict):
            continue
        a = c.get("content_a") or ""
        b = c.get("content_b") or ""
        if a or b:
            lines.append(f"  - {a[:60]} <> {b[:60]}")
    return "\n".join(lines) if lines else "  (none)"


def _format_emotional_patterns(patterns: Any) -> str:
    if not isinstance(patterns, list) or not patterns:
        return "  (none)"
    lines: list[str] = []
    for p in patterns[:5]:
        if not isinstance(p, dict):
            continue
        pattern = p.get("pattern") or p.get("summary") or "pattern"
        freq = p.get("frequency")
        freq_txt = f" (x{freq})" if isinstance(freq, int) else ""
        lines.append(f"  - {pattern}{freq_txt}")
    return "\n".join(lines) if lines else "  (none)"


def _format_transformations(items: Any) -> str:
    if not isinstance(items, list) or not items:
        return "  (none)"
    lines: list[str] = []
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        content = (item.get("content") or "").strip()
        subcategory = item.get("subcategory") or item.get("category") or "belief"
        progress = item.get("progress") if isinstance(item.get("progress"), dict) else {}
        reflections = progress.get("progress", {}).get("reflections", {}) if isinstance(progress.get("progress"), dict) else {}
        evidence = progress.get("progress", {}).get("evidence", {}) if isinstance(progress.get("progress"), dict) else {}
        evidence_samples = progress.get("evidence_samples") if isinstance(progress.get("evidence_samples"), list) else []
        requirements = progress.get("requirements") if isinstance(progress.get("requirements"), dict) else {}
        cur_ref = reflections.get("current")
        req_ref = reflections.get("required")
        ref_txt = f" ({cur_ref}/{req_ref} reflections)" if cur_ref is not None and req_ref is not None else ""
        evidence_count = evidence.get("memory_count")
        strength = evidence.get("current_strength")
        strength_txt = f"{strength:.2f}" if isinstance(strength, (int, float)) else "?"
        evidence_txt = (
            f", evidence {evidence_count} (strength {strength_txt})"
            if evidence_count is not None
            else ""
        )
        requirement_txt = ""
        if requirements:
            min_hb = requirements.get("min_heartbeats")
            min_ev = requirements.get("evidence_threshold")
            max_change = requirements.get("max_change_per_attempt")
            parts = []
            if min_hb is not None:
                parts.append(f"hb>={min_hb}")
            if min_ev is not None:
                parts.append(f"ev>={min_ev}")
            if max_change is not None:
                parts.append(f"max_change<={max_change}")
            if parts:
                requirement_txt = f" | req: {', '.join(parts)}"

        sample_txt = ""
        if evidence_samples:
            samples = []
            for sample in evidence_samples[:3]:
                if not isinstance(sample, dict):
                    continue
                content_sample = (sample.get("content") or "").strip()
                if content_sample:
                    samples.append(content_sample[:50])
            if samples:
                sample_txt = f" | evidence: {'; '.join(samples)}"
        label = content if content else subcategory
        lines.append(
            f"  - [{subcategory}] {label[:60]}{ref_txt}{evidence_txt}{requirement_txt}{sample_txt}"
        )
    return "\n".join(lines) if lines else "  (none)"


def _format_costs(costs: dict[str, Any]) -> str:
    if not costs:
        return "  (unknown)"
    lines = []
    for action, cost in sorted(costs.items(), key=lambda x: x[1]):
        if cost == 0:
            lines.append(f"  - {action}: free")
        else:
            lines.append(f"  - {action}: {int(cost)}")
    return "\n".join(lines)
