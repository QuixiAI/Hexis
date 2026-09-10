const ENDPOINT_DEFAULTS: Record<string, string> = {
  atlascloud: "https://api.atlascloud.ai/v1",
  openai: "https://api.openai.com/v1",
};

const ENDPOINTLESS_PROVIDERS = new Set([
  "anthropic",
  "grok",
  "gemini",
  "openai-codex",
  "chutes",
  "github-copilot",
  "qwen-portal",
  "minimax-portal",
  "google-gemini-cli",
  "google-antigravity",
]);

export function resolveInitLlmEndpoint(provider: string, endpoint: string): string {
  if (provider === "openai_compatible") return endpoint;
  if (ENDPOINTLESS_PROVIDERS.has(provider)) return "";
  return ENDPOINT_DEFAULTS[provider] || endpoint;
}

export function catalogDeclaredDefault(
  block: unknown,
  availableModels: string[]
): string {
  if (!block || typeof block !== "object") return "";
  const models = (block as { models?: unknown }).models;
  if (!models || typeof models !== "object") return "";
  const available = new Set(availableModels);
  for (const value of Object.values(models)) {
    if (!value || typeof value !== "object") continue;
    const row = value as { id?: unknown; description?: unknown };
    const id = typeof row.id === "string" ? row.id : "";
    const description = typeof row.description === "string" ? row.description : "";
    if (available.has(id) && /\bdefault\b/i.test(description)) return id;
  }
  return "";
}
