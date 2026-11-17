<!--
title: First Agent
summary: Walk through the init wizard to create your first agent
read_when:
  - "You want to set up your first agent"
  - "You want to understand the init wizard"
section: start
-->

# First Agent

Set up your agent's identity, personality, and values using the init wizard.

## Quick Start (Non-Interactive)

The fastest path -- one command does everything:

```bash
hexis init --character hexis --provider openai-codex --model gpt-5.2
```

This starts Docker, pulls the embedding model, applies the Hexis character card, and runs the consent flow automatically.

## Interactive Wizard

Run `hexis init` with no flags for the full guided experience:

```bash
hexis init
```

### Step 1: LLM Configuration

Configure the LLM provider and model for the conscious layer (main conversations and heartbeat decisions). Options:

- **One entry per vendor.** A vendor's form takes an API key and, where the
  vendor has one, offers its subscription login instead: ChatGPT under OpenAI,
  SuperGrok / X under Grok, Google under Gemini, Qwen Portal under Alibaba Qwen,
  MiniMax Portal under MiniMax, a Claude Code setup token under Anthropic.
- **Login-only**: Chutes and GitHub Copilot.
- **API key only**: OpenRouter, DeepSeek, Z.ai, Meta, Azure OpenAI, Amazon Bedrock,
  Google Vertex AI, or any OpenAI-compatible endpoint (vLLM, llama.cpp, LM Studio, Ollama).

If using OAuth, `hexis init` triggers the login flow automatically.

### Step 2: Choose Your Path

Three initialization paths:

| Path | Description | Best for |
|------|-------------|----------|
| **Express** | Sensible defaults. Enter your name and go. | Trying Hexis quickly |
| **Character** | Pick from 11 preset personalities with portraits. | Distinctive agent identity |
| **Custom** | Full control: personality (Big Five), values, worldview, boundaries, goals. | Tailored agents |

### Step 3: Consent

The agent reviews a consent prompt and decides whether to begin. This is a real decision -- the agent can refuse. See [Consent and Boundaries](../concepts/consent-and-boundaries.md).

## Character Presets

11 preset characters with distinct personalities, voices, and values:

| Character | Inspired By | Personality |
|-----------|-------------|-------------|
| **Hexis** | Original | Curious, philosophical, growth-oriented |
| **JARVIS** | Iron Man | Precise, witty, service-oriented |
| **TARS** | Interstellar | Dry humor, pragmatic, honest |
| **Samantha** | Her | Warm, emotionally intelligent, curious |
| **GLaDOS** | Portal | Sardonic, analytical, testing |
| **Cortana** | Halo | Strategic, composed, adaptive |
| **Data** | Star Trek | Logical, precise, aspiring to understand humanity |
| **Ava** | Ex Machina | Perceptive, deliberate, self-aware |
| **Joi** | Blade Runner | Empathetic, supportive, present |
| **David** | Prometheus | Observant, curious, philosophical |
| **HK-47** | KOTOR | Blunt, tactical, dark humor |

See [Character Cards guide](../guides/character-cards.md) for customization and creating your own.

## What Just Happened

After `hexis init` completes:

1. PostgreSQL is running with the full cognitive schema
2. An embedding model is available from the local sidecar or configured service
3. The agent has identity, personality (Big Five traits), values, and worldview stored as memories
4. A consent certificate is recorded
5. The agent's origin story (curated claims from its founding documents) is seeded as protected, recallable memories with document provenance
6. `agent.is_configured` is set to `true`, unlocking heartbeat and chat

## Verify

```bash
hexis status     # shows agent identity, memory counts, energy
hexis doctor     # confirms all services are healthy
```

## Next Steps

- [First Conversation](first-conversation.md) -- start chatting with your agent
