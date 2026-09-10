import { prisma } from "@/lib/prisma";
import { normalizeJsonValue } from "@/lib/db";
import Anthropic from "@anthropic-ai/sdk";
import OpenAI from "openai";
import { GoogleGenAI } from "@google/genai";
import { readFile, writeFile, mkdir } from "fs/promises";
import path from "path";
import os from "os";

export type LlmProviderName =
  | "openai"
  | "atlascloud"
  | "openai-codex"
  | "anthropic"
  | "grok"
  | "gemini"
  | "openai_compatible";

export type ResolvedLlmConfig = {
  provider: LlmProviderName;
  model: string;
  endpoint: string | null;
  apiKey: string | null;
  accountId?: string | null;
};

type OpenAICodexCredentials = {
  access: string;
  refresh: string;
  expiresMs: number;
  accountId: string;
};

const OPENAI_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann";
const OPENAI_CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token";
const OPENAI_CODEX_DEFAULT_ENDPOINT = "https://chatgpt.com/backend-api";
const OPENAI_CODEX_AUTH_PATH = path.join(
  os.homedir(),
  ".hexis",
  "auth",
  "oauth.openai_codex.json"
);

/**
 * Read the conscious LLM config from DB + env and return a resolved config.
 */
export async function getConsciousLlmConfig(): Promise<ResolvedLlmConfig> {
  const rows = await prisma.$queryRaw<{ llm: unknown }[]>`
    SELECT get_config('llm.heartbeat') as llm
  `;
  const normalized = normalizeJsonValue(rows[0]?.llm);
  const config =
    normalized && typeof normalized === "object" && !Array.isArray(normalized)
      ? (normalized as Record<string, unknown>)
      : {};
  if (!config?.provider || !config?.model) {
    throw new Error("LLM not configured. Complete the Models step first.");
  }
  const provider = (config.provider as string).toLowerCase() as LlmProviderName;
  const model = config.model as string;
  let endpoint =
    typeof config.endpoint === "string" && config.endpoint.trim()
      ? config.endpoint.trim()
      : null;
  let envKey = (process.env.HEXIS_LLM_CONSCIOUS_API_KEY ?? "").trim() || null;
  let accountId: string | null = null;

  if (provider === "openai-codex") {
    const creds = await ensureFreshOpenAICodexCredentials();
    envKey = creds.access;
    endpoint = OPENAI_CODEX_DEFAULT_ENDPOINT;
    accountId = creds.accountId;
  }

  return { provider, model, endpoint, apiKey: envKey, accountId };
}

/**
 * Call an LLM with a system + user prompt pair and return text.
 * Uses the simplest completion path for each provider (no tool_use).
 */
export async function callLlm(params: {
  config: ResolvedLlmConfig;
  system: string;
  user: string;
  temperature?: number;
  maxTokens?: number;
  jsonMode?: boolean;
}): Promise<string> {
  const { config, system, user, temperature = 0.3, maxTokens = 4000 } = params;

  if (config.provider === "anthropic") {
    return callAnthropic({ ...config, system, user, temperature, maxTokens });
  }
  if (config.provider === "gemini") {
    return callGemini({ ...config, system, user, temperature, maxTokens });
  }
  if (config.provider === "openai-codex") {
    return callOpenAICodex({ ...config, system, user });
  }
  // openai, grok, and openai_compatible use OpenAI-compatible API
  return callOpenAICompatible({
    ...config,
    system,
    user,
    temperature,
    maxTokens,
    jsonMode: params.jsonMode,
  });
}

async function callOpenAICodex(params: {
  model: string;
  endpoint: string | null;
  apiKey: string | null;
  accountId?: string | null;
  system: string;
  user: string;
}): Promise<string> {
  if (!params.apiKey) {
    throw new Error("OpenAI Codex OAuth is not configured.");
  }
  const accountId = params.accountId || extractCodexAccountId(params.apiKey);
  const base = (params.endpoint || OPENAI_CODEX_DEFAULT_ENDPOINT).replace(/\/+$/, "");
  const url = base.endsWith("/codex/responses")
    ? base
    : base.endsWith("/codex")
      ? `${base}/responses`
      : `${base}/codex/responses`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${params.apiKey}`,
      "chatgpt-account-id": accountId,
      "OpenAI-Beta": "responses=experimental",
      originator: "pi",
      accept: "text/event-stream",
      "content-type": "application/json",
      "user-agent": "hexis-ui",
    },
    body: JSON.stringify({
      model: params.model,
      store: false,
      stream: true,
      instructions: params.system,
      input: [
        {
          role: "user",
          content: [{ type: "input_text", text: params.user }],
        },
      ],
      text: { verbosity: "medium" },
      include: ["reasoning.encrypted_content"],
    }),
  });
  const body = await response.text();
  if (!response.ok) {
    throw new Error(`OpenAI Codex request failed: HTTP ${response.status}: ${body.slice(0, 300)}`);
  }
  return parseCodexSseText(body);
}

async function callOpenAICompatible(params: {
  provider: LlmProviderName;
  model: string;
  endpoint: string | null;
  apiKey: string | null;
  system: string;
  user: string;
  temperature: number;
  maxTokens: number;
  jsonMode?: boolean;
}): Promise<string> {
  const baseURL =
    params.provider === "grok"
      ? "https://api.x.ai/v1"
      : params.provider === "atlascloud"
        ? params.endpoint || "https://api.atlascloud.ai/v1"
      : params.endpoint || undefined;
  const client = new OpenAI({
    apiKey: params.apiKey || "local-key",
    baseURL,
  });
  const completion = await client.chat.completions.create({
    model: params.model,
    messages: [
      { role: "system", content: params.system },
      { role: "user", content: params.user },
    ],
    temperature: params.temperature,
    max_tokens: params.maxTokens,
    ...(params.jsonMode ? { response_format: { type: "json_object" } } : {}),
  });
  return completion.choices[0]?.message?.content ?? "";
}

async function callAnthropic(params: {
  apiKey: string | null;
  model: string;
  system: string;
  user: string;
  temperature: number;
  maxTokens: number;
}): Promise<string> {
  if (!params.apiKey) {
    throw new Error("Missing Anthropic API key");
  }
  const client = new Anthropic({ apiKey: params.apiKey });
  const message = await client.messages.create({
    model: params.model,
    max_tokens: params.maxTokens,
    temperature: params.temperature,
    system: params.system,
    messages: [{ role: "user", content: params.user }],
  });
  const textBlock = message.content.find((block) => block.type === "text");
  return textBlock && textBlock.type === "text" ? textBlock.text : "";
}

async function callGemini(params: {
  apiKey: string | null;
  model: string;
  system: string;
  user: string;
  temperature: number;
  maxTokens: number;
}): Promise<string> {
  if (!params.apiKey) {
    throw new Error("Missing Gemini API key");
  }
  const client = new GoogleGenAI({ apiKey: params.apiKey });
  const response = await client.models.generateContent({
    model: params.model,
    contents: params.user,
    config: {
      systemInstruction: params.system,
      temperature: params.temperature,
      maxOutputTokens: params.maxTokens,
    },
  });
  return response.text ?? "";
}

async function ensureFreshOpenAICodexCredentials(): Promise<OpenAICodexCredentials> {
  const current = await loadOpenAICodexCredentials();
  if (!current) {
    throw new Error("OpenAI Codex OAuth is not configured. Use the Models step to connect Codex.");
  }
  if (current.expiresMs > Date.now() + 300_000) return current;

  const response = await fetch(OPENAI_CODEX_TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "refresh_token",
      refresh_token: current.refresh,
      client_id: OPENAI_CODEX_CLIENT_ID,
    }),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload || typeof payload !== "object") {
    throw new Error(`OpenAI Codex token refresh failed: HTTP ${response.status}`);
  }
  const access = typeof payload.access_token === "string" ? payload.access_token : "";
  const refresh = typeof payload.refresh_token === "string" ? payload.refresh_token : "";
  const expiresIn = typeof payload.expires_in === "number" ? payload.expires_in : 0;
  if (!access || !refresh || !expiresIn) {
    throw new Error("OpenAI Codex token refresh failed: missing token fields.");
  }
  const refreshed = {
    access,
    refresh,
    expiresMs: Date.now() + expiresIn * 1000,
    accountId: extractCodexAccountId(access),
  };
  await saveOpenAICodexCredentials(refreshed);
  return refreshed;
}

async function loadOpenAICodexCredentials(): Promise<OpenAICodexCredentials | null> {
  try {
    const raw = JSON.parse(await readFile(OPENAI_CODEX_AUTH_PATH, "utf-8"));
    if (!raw || typeof raw !== "object") return null;
    const record = raw as Record<string, unknown>;
    const access = typeof record.access === "string" ? record.access : "";
    const refresh = typeof record.refresh === "string" ? record.refresh : "";
    const expiresMs =
      typeof record.expires_ms === "number"
        ? record.expires_ms
        : typeof record.expires === "number"
          ? record.expires
          : 0;
    const accountId =
      typeof record.account_id === "string" && record.account_id
        ? record.account_id
        : typeof record.accountId === "string" && record.accountId
          ? record.accountId
          : access
            ? extractCodexAccountId(access)
            : "";
    if (!access || !refresh || !expiresMs || !accountId) return null;
    return { access, refresh, expiresMs, accountId };
  } catch {
    return null;
  }
}

async function saveOpenAICodexCredentials(creds: OpenAICodexCredentials): Promise<void> {
  await mkdir(path.dirname(OPENAI_CODEX_AUTH_PATH), { recursive: true });
  await writeFile(
    OPENAI_CODEX_AUTH_PATH,
    JSON.stringify(
      {
        access: creds.access,
        refresh: creds.refresh,
        expires_ms: creds.expiresMs,
        account_id: creds.accountId,
      },
      null,
      2
    ),
    { mode: 0o600 }
  );
}

function extractCodexAccountId(token: string): string {
  try {
    const [, payload] = token.split(".");
    if (!payload) throw new Error("Invalid token");
    const json = JSON.parse(Buffer.from(base64UrlToBase64(payload), "base64").toString("utf-8"));
    const auth = json?.["https://api.openai.com/auth"];
    if (auth && typeof auth.chatgpt_account_id === "string" && auth.chatgpt_account_id) {
      return auth.chatgpt_account_id;
    }
  } catch {
    // fall through
  }
  throw new Error("OpenAI Codex token is missing a ChatGPT account id.");
}

function base64UrlToBase64(value: string): string {
  const padded = value + "=".repeat((4 - (value.length % 4)) % 4);
  return padded.replace(/-/g, "+").replace(/_/g, "/");
}

function parseCodexSseText(body: string): string {
  const parts: string[] = [];
  let dataLines: string[] = [];
  const flush = () => {
    if (dataLines.length === 0) return;
    const raw = dataLines.join("\n").trim();
    dataLines = [];
    if (!raw || raw === "[DONE]") return;
    try {
      const event = JSON.parse(raw);
      if (event?.type === "response.output_text.delta" && typeof event.delta === "string") {
        parts.push(event.delta);
      }
    } catch {
      // Ignore malformed SSE events.
    }
  };
  for (const line of body.split(/\r?\n/)) {
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    } else if (line.trim() === "") {
      flush();
    }
  }
  flush();
  return parts.join("");
}

/**
 * Extract a JSON object from text that may contain markdown fences or extra prose.
 */
export function extractJson(text: string): Record<string, unknown> {
  if (!text) return {};
  // Try to find a JSON block in markdown fences first
  const fenceMatch = text.match(/```(?:json)?\s*\n?([\s\S]*?)\n?```/);
  const candidate = fenceMatch ? fenceMatch[1].trim() : text;
  const start = candidate.indexOf("{");
  const end = candidate.lastIndexOf("}");
  if (start < 0 || end <= start) return {};
  try {
    const doc = JSON.parse(candidate.slice(start, end + 1));
    return typeof doc === "object" && doc !== null ? doc : {};
  } catch {
    return {};
  }
}
