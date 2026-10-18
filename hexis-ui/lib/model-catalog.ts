// Live per-provider model catalog for the init wizard.
//
// Mirrors apps/tui/model_catalog.py: prefer a live, auto-updating source over a
// hand-maintained list, treat it as advisory, and always allow a free-typed id.
//   * cloud providers -> the models.dev catalog (one no-auth JSON), cached ~24h
//     in-memory and sorted newest-first
//   * on any failure -> a short curated fallback; the model field stays free-text
//
// Providers whose endpoint lists its own models (OpenAI-compatible APIs, the
// subscription sign-ins) are asked first by the Python API; this catalog is
// the fallback when that list is empty.

import { catalogDeclaredDefault } from "@/lib/init-llm";

export const MODELS_DEV_URL = "https://models.dev/api.json";
const CACHE_TTL_MS = 24 * 3600 * 1000;
const TIMEOUT_MS = 12000;

// Hexis provider id -> models.dev slug.
export const PROVIDER_SLUG: Record<string, string> = {
  openai: "openai",
  "openai-codex": "openai",
  anthropic: "anthropic",
  grok: "xai",
  "xai-oauth": "xai",
  gemini: "google",
  chutes: "chutes",
  "github-copilot": "github-copilot",
  "qwen-portal": "alibaba",
  "minimax-portal": "minimax",
  minimax: "minimax",
  "google-gemini-cli": "google",
  "google-antigravity": "google",
  openrouter: "openrouter",
  azure_openai: "azure",
  bedrock: "amazon-bedrock",
  vertex: "google-vertex",
  deepseek: "deepseek",
  zai: "zai",
  qwen: "alibaba",
  qwen_coding: "alibaba",
  meta: "meta",
};

// Non-chat model ids to hide from the dropdown (still typeable as free text).
const NON_CHAT_RE =
  /embed|tts|whisper|moderation|rerank|image|audio|video|dall.?e|imagen|veo|sora|guard|ocr|speech|transcrib/i;

// Variant/specialty suffixes that shouldn't be the *default* pick (still listed).
const DEFAULT_SKIP = [
  "-pro",
  "-mini",
  "-nano",
  "-lite",
  "preview",
  "-exp",
  "experimental",
  "thinking",
  "chat-latest",
  "non-reasoning",
  "multi-agent",
  "imagine",
  "-build",
  "deep-research",
  "realtime",
  "-audio",
  "-tts",
  "-image",
  "-high",
  "-low",
  "-search",
  "computer-use",
];

// Minimal offline fallback if models.dev is unreachable.
export const FALLBACK: Record<string, string[]> = {
  openai: ["gpt-5.2", "gpt-4o", "gpt-4o-mini"],
  "openai-codex": ["gpt-5.2", "gpt-5.2-codex"],
  anthropic: ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5"],
  grok: ["grok-4.3", "grok-3"],
  "xai-oauth": ["grok-4.3", "grok-3"],
  gemini: ["gemini-3-pro-preview", "gemini-2.5-flash"],
  chutes: ["deepseek-ai/DeepSeek-V3-0324"],
  "github-copilot": ["gpt-4o"],
};

type JsonRecord = Record<string, unknown>;

let memCache: { data: JsonRecord; ts: number } | null = null;

export function asRecord(value: unknown): JsonRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : {};
}

export function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

export async function fetchWithTimeout(url: string, init: RequestInit = {}): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    return await fetch(url, { ...init, signal: controller.signal, cache: "no-store" });
  } finally {
    clearTimeout(timer);
  }
}

export async function modelsDev(): Promise<JsonRecord> {
  if (memCache && Date.now() - memCache.ts < CACHE_TTL_MS) {
    return memCache.data;
  }
  const resp = await fetchWithTimeout(MODELS_DEV_URL);
  if (!resp.ok) throw new Error(`models.dev responded ${resp.status}`);
  const data = asRecord(await resp.json());
  memCache = { data, ts: Date.now() };
  return data;
}

function sortKey(value: unknown): string {
  const model = asRecord(value);
  return typeof model.last_updated === "string"
    ? model.last_updated
    : typeof model.release_date === "string"
      ? model.release_date
      : "";
}

// Extract text/chat model ids from a models.dev provider block, newest first.
export function chatModels(value: unknown): string[] {
  const rows: [string, string][] = [];
  const models = asRecord(asRecord(value).models);
  for (const key of Object.keys(models)) {
    const m = asRecord(models[key]);
    const mid = typeof m.id === "string" ? m.id : "";
    if (!mid || NON_CHAT_RE.test(mid)) continue;
    const outs = asRecord(m.modalities).output;
    if (Array.isArray(outs) && !outs.includes("text")) continue;
    rows.push([mid, sortKey(m)]);
  }
  // Newest first (stable sort preserves catalog order within equal keys).
  rows.sort((a, b) => (a[1] < b[1] ? 1 : a[1] > b[1] ? -1 : 0));
  const seen = new Set<string>();
  const ids: string[] = [];
  for (const [mid] of rows) {
    if (!seen.has(mid)) {
      seen.add(mid);
      ids.push(mid);
    }
  }
  return ids;
}

// Pick a sensible default from the live catalog (newest-first list).
export function recommendedDefault(_provider: string, models: string[]): string {
  if (models.length === 0) return "";
  for (const mid of models) {
    const lower = mid.toLowerCase();
    if (!DEFAULT_SKIP.some((s) => lower.includes(s))) return mid;
  }
  return models[0];
}

export type CatalogResult = { models: string[]; default: string; error?: string };

/**
 * The advisory catalog for a provider: models.dev when reachable, else the
 * curated fallback. Never throws; a provider the catalog does not know gets
 * an empty list and the model field stays free text.
 */
export async function catalogModels(provider: string): Promise<CatalogResult> {
  const slug = PROVIDER_SLUG[provider];
  if (!slug) {
    const models = FALLBACK[provider] || [];
    return { models, default: recommendedDefault(provider, models) };
  }
  try {
    const data = await modelsDev();
    const block = data[slug];
    let models = block ? chatModels(block) : [];
    if (models.length === 0) models = FALLBACK[provider] || [];
    return { models, default: recommendedDefault(provider, models) };
  } catch (err: unknown) {
    const models = FALLBACK[provider] || [];
    return {
      models,
      default: recommendedDefault(provider, models),
      error: errorMessage(err, "Unable to load live model catalog."),
    };
  }
}

/** The catalog's own declared default for a list the account reported. */
export async function declaredDefault(provider: string, available: string[]): Promise<string> {
  const slug = PROVIDER_SLUG[provider];
  if (!slug) return "";
  try {
    return catalogDeclaredDefault((await modelsDev())[slug], available);
  } catch {
    return "";
  }
}
