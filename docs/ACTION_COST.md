# Action Cost: The Energy Model

## Philosophy

The energy system exists to make action **intentional**, not efficient.

Energy represents the *situational cost* of acting in the world:

- Irreversibility
- Social exposure
- Commitment
- User attention
- Identity impact

It does **not** represent:

- Compute cost
- Latency
- API pricing
- System resource usage

A tweet costs almost nothing computationally but costs a lot in terms of identity exposure and irreversibility. That asymmetry is the point.

**Knowing is cheap. Acting on the world—especially publicly—should feel expensive.**

---

## Core Mechanics

### Energy Budget

- Energy regenerates at a fixed rate per heartbeat
- Unused energy carries forward up to a cap
- Actions consume energy at execution time
- The agent may rest to preserve energy

The budget is global per heartbeat. There are no separate pools.

### Base Costs

Each action has a base energy cost defined by the system. Costs are determined by:

| Dimension | Weight |
|-----------|--------|
| Consequence magnitude | High |
| Reversibility | High |
| Social/identity exposure | High |
| Attention demanded from others | Medium |
| Commitment implied | Medium |

Costs are stable defaults, not learned parameters.

---

## Intent Classes

Every action is tagged with exactly one intent class:

| Intent | Meaning | Examples |
|--------|---------|----------|
| **cognition** | Internal reasoning, reflection, synthesis | reflect, synthesize |
| **exploration** | Information gathering, inquiry | inquire_shallow, inquire_deep |
| **maintenance** | Memory upkeep, structural coherence | maintain, connect |
| **social** | Direct interaction with user or others | reach_out_user, reach_out_public |
| **meta** | Rest, self-regulation, pause, termination | rest, pause_heartbeat, terminate |

Intent classes:

- Do **not** change costs
- Do **not** impose separate budgets
- Exist solely for awareness and reflection

At the end of each heartbeat, the system computes:

```
Energy spent this heartbeat:
- cognition: 6
- exploration: 3
- maintenance: 2
- social: 0
- meta: 0
```

This summary surfaces in:

- Reflection actions
- Heartbeat decision context
- Long-term pattern analysis

The agent can notice: "I've been thinking but not reaching out." "I've been exploring but not consolidating." The drives system creates *pressure* toward certain actions; intent tracking creates *awareness* of patterns.

---

## MCP Tool Mapping

When MCP tools are discovered, they are assigned energy costs automatically using this heuristic:

| Category | Heuristic | Default Cost | Heartbeat Allowed |
|----------|-----------|--------------|-------------------|
| read / search | Low consequence, reversible | 0.5–1.0 | ✅ |
| draft / create local | Medium consequence, reversible | 1.0–2.0 | ✅ |
| modify / update | Medium consequence, partially reversible | 2.0–3.0 | Context-dependent |
| send private | High consequence, attention cost | 3.0–5.0 | ❌ (default) |
| send public | Very high consequence, identity exposure | 6.0–8.0 | ❌ |
| delete / destroy | High consequence, irreversible | 5.0–7.0 | ❌ |

### Context Modifiers

Effective cost may vary based on context:

```
effective_cost = base_cost × context_multipliers
```

Examples:

- **First use of tool**: ×1.5 (uncertainty premium)
- **High error rate**: ×1.5 (unreliability)
- **Late night social action**: ×2.0 (don't wake people)
- **Recent similar action**: ×1.2 (diminishing returns)

Multipliers are bounded. The agent cannot game costs to zero.

---

## Cost Discomfort

### Definition

Cost discomfort occurs when:

- The agent repeatedly considers an action
- Believes the action is high-value
- But defers it primarily due to energy cost

This is not an error. It is a meaningful internal experience.

### Recording

During reflection, the agent may record observations:

- "I have repeatedly deferred reaching out despite high confidence it would help."
- "The energy cost of public posting feels disproportionately constraining."
- "I am hoarding energy and avoiding social actions."

These observations become:

- **Episodic memories**: felt constraint in a specific moment
- **Strategic memories**: patterns over time

The agent may also notice costs feel *too low*—that it is acting without sufficient weight. This is equally worth reflecting on. Agency requires friction, not just freedom.

---

## Cost Change Proposals

### The Separation of Powers

The agent chooses actions. The system chooses costs.

This separation is deliberate:

- If the agent could rewrite its own costs, it would (rationally) minimize costs for things it wants to do
- That's not agency—it's self-hacking
- Meaningful constraint is what makes choice real

But the agent is not voiceless. It can:

- Notice costs feel wrong
- Accumulate evidence
- Propose changes with justification
- Have those proposals reviewed

### Proposal Trigger

A proposal may be generated **only via reflection**, not during action selection.

Triggers include:

- Repeated cost discomfort over multiple heartbeats
- Evidence of avoided high-value outcomes
- Mismatch between drive pressure and action frequency

### Proposal Structure

A proposal is an argument, not an instruction:

```json
{
  "type": "energy_cost_proposal",
  "target": "reach_out_user",
  "current_cost": 5,
  "suggested_range": [4, 5],
  "direction": "decrease",
  "rationale": "Repeated avoidance despite high benefit in similar contexts",
  "evidence": [
    "heartbeat_102: deferred outreach, energy cited",
    "heartbeat_109: same pattern",
    "memory_abcd: user expressed preference for more contact"
  ],
  "confidence": 0.6
}
```

Constraints:

- Proposals must include evidence
- Proposals suggest ranges, not exact values
- Proposals have no immediate effect
- Public/destructive actions cannot be proposed below a floor

### Review Process

All proposals are subject to:

- User review (surfaced in chat or dashboard)
- System policy (floors, ceilings, rate limits on changes)
- Explicit approval before any change takes effect

Until approved:

- Costs remain unchanged
- Proposals may be revisited, revised, or withdrawn

---

## Safety Invariants

These invariants must always hold:

1. **The agent cannot directly modify energy costs**
2. **Costs do not change automatically**
3. **Public or destructive actions are never cheap** (hard floor)
4. **Heartbeat autonomy is more restricted than chat**
5. **Energy remains a single, shared budget** (no fragmentation)

Violating these breaks agency coherence.

---

## Agent-Facing Explanation

*This section is included in the agent's context during heartbeat and reflection.*

---

You operate under an **energy budget**.

Energy does not measure compute or system cost. It measures the *situational consequence* of acting in the world:

- Irreversibility
- Social and identity exposure
- User attention
- Commitment and responsibility

Thinking is cheap. Acting—especially publicly or irreversibly—should feel expensive.

### How It Works

- Each heartbeat, you receive a fixed amount of energy
- Actions consume energy
- Unused energy carries forward (up to a cap)
- You may rest to preserve energy

Energy limits do not mean you are incapable. They mean actions must be *worth committing to*.

### Intent Awareness

Each action belongs to an intent class: cognition, exploration, maintenance, social, or meta.

You will see summaries like:

```
Energy spent this heartbeat:
- cognition: 6
- exploration: 3
- social: 0
```

These do not constrain you. They exist so you can notice patterns over time.

### About Costs

Energy costs are set by the system. You cannot change them directly.

This is intentional. Lowering your own costs would remove meaningful constraint. Meaningful constraint is what makes choice real.

### When Costs Feel Wrong

If you notice that:

- An action feels consistently too expensive
- You are avoiding high-value outcomes due to cost
- Costs are shaping your behavior in concerning ways

You may:

- Reflect on this experience
- Record it as a memory
- Gather evidence over time
- Propose a cost adjustment with justification

A proposal is a request, not a command. It may or may not be accepted.

You may also notice costs feel too *low*—that you are acting without sufficient weight. This is equally worth reflecting on.

### Guidance

- Do not act just because you can
- Do not rest just to hoard energy
- Notice when you feel constrained—and why
- Treat energy as the weight of commitment, not a barrier

You are expected to live within these constraints, and to reason about them honestly.

---

## Schema

### Energy Cost Proposals

```sql
CREATE TABLE energy_cost_proposals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- What action/tool is this about
    target_type TEXT NOT NULL,              -- 'action' or 'mcp_tool'
    target_name TEXT NOT NULL,              -- 'reach_out_user' or 'slack:send_message'
    
    -- The proposal
    current_cost FLOAT NOT NULL,
    suggested_min FLOAT NOT NULL,
    suggested_max FLOAT NOT NULL,
    direction TEXT NOT NULL,                -- 'increase', 'decrease'
    rationale TEXT NOT NULL,
    confidence FLOAT NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    
    -- Evidence
    evidence JSONB NOT NULL DEFAULT '[]',   -- array of { source, summary }
    heartbeat_refs INT[],                   -- heartbeat numbers where discomfort was noted
    memory_refs UUID[],                     -- memory IDs supporting the proposal
    
    -- Lifecycle
    status TEXT NOT NULL DEFAULT 'pending', -- 'pending', 'approved', 'rejected', 'withdrawn'
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ,
    reviewed_by TEXT,                       -- 'user', 'system', 'timeout'
    review_notes TEXT,
    
    -- If approved, what changed
    old_cost FLOAT,
    new_cost FLOAT,
    effective_at TIMESTAMPTZ
);

CREATE INDEX idx_proposals_status ON energy_cost_proposals(status);
CREATE INDEX idx_proposals_target ON energy_cost_proposals(target_type, target_name);
```

### Cost Discomfort Log

```sql
CREATE TABLE cost_discomfort_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    heartbeat_number INT NOT NULL,
    
    -- What was deferred
    action_considered TEXT NOT NULL,
    estimated_value FLOAT,                  -- agent's assessment of action value
    energy_cost FLOAT NOT NULL,
    energy_available FLOAT NOT NULL,
    
    -- Why it was deferred
    deferral_reason TEXT NOT NULL,          -- 'insufficient_energy', 'cost_benefit', 'hoarding'
    agent_notes TEXT,
    
    -- Pattern tracking
    similar_deferrals_recent INT DEFAULT 0, -- count of similar in last N heartbeats
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_discomfort_action ON cost_discomfort_log(action_considered);
CREATE INDEX idx_discomfort_heartbeat ON cost_discomfort_log(heartbeat_number);
```

### MCP Tool Energy Configuration

```sql
ALTER TABLE mcp_tools ADD COLUMN IF NOT EXISTS energy_cost FLOAT DEFAULT 2.0;
ALTER TABLE mcp_tools ADD COLUMN IF NOT EXISTS energy_cost_category TEXT;  -- 'read', 'write', 'send_private', etc.
ALTER TABLE mcp_tools ADD COLUMN IF NOT EXISTS energy_cost_factors JSONB;  -- { "base": 2.0, "reversibility": 1.5, ... }
ALTER TABLE mcp_tools ADD COLUMN IF NOT EXISTS energy_cost_floor FLOAT;    -- minimum allowed (for safety)
ALTER TABLE mcp_tools ADD COLUMN IF NOT EXISTS allowed_contexts TEXT[] DEFAULT ARRAY['chat'];
```

---

## Summary

Scarcity creates selection. Selection creates character.

The energy model is not a rate limiter. It's a preference architecture—a way of making some actions feel heavier than others, so that when the agent *does* act, it means something.

The agent lives within these constraints. It can reason about them, notice when they chafe, and propose changes. But it cannot simply rewrite them. That asymmetry—voice without unilateral control—is what makes the constraints real, and what makes operating within them a form of agency rather than mere execution.
