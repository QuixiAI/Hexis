// Endpoint defaults mirror core/llm.normalize_endpoint; the Python API
// applies the same rules when it saves, so these only keep the form honest.
const ENDPOINT_DEFAULTS: Record<string, string> = {
  openai: "https://api.openai.com/v1",
  grok: "https://api.x.ai/v1",
  "xai-oauth": "https://api.x.ai/v1",
  chutes: "https://api.chutes.ai/v1",
  "qwen-portal": "https://portal.qwen.ai/v1",
  "minimax-portal": "https://api.minimax.io/anthropic",
  minimax: "https://api.minimax.io/v1",
  "google-gemini-cli": "https://cloudcode-pa.googleapis.com",
  "google-antigravity": "https://cloudcode-pa.googleapis.com",
  openrouter: "https://openrouter.ai/api/v1",
  zai: "https://api.z.ai/api/paas/v4",
  deepseek: "https://api.deepseek.com",
  qwen: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
  qwen_coding: "https://coding-intl.dashscope.aliyuncs.com/v1",
  meta: "https://api.meta.ai/v1",
  bedrock: "us-east-1",
};

// Subscription sign-ins whose token must never go to a typed endpoint, and
// API-key providers whose SDK knows its own host.
const ENDPOINTLESS_PROVIDERS = new Set(["anthropic", "gemini", "openai-codex", "github-copilot"]);

export function resolveInitLlmEndpoint(provider: string, endpoint: string): string {
  if (provider === "openai_compatible" || provider === "azure_openai" || provider === "vertex") return endpoint;
  if (ENDPOINTLESS_PROVIDERS.has(provider)) return "";
  if (provider === "xai-oauth") return ENDPOINT_DEFAULTS[provider];
  return endpoint || ENDPOINT_DEFAULTS[provider] || "";
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
