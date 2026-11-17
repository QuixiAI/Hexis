import { prisma } from "@/lib/prisma";
import { normalizeJsonValue } from "@/lib/db";
import { hexisApiHeaders, resolveHexisApiUrl } from "@/lib/python-api";

export type ResolvedLlmConfig = {
  provider: string;
  model: string;
  endpoint: string | null;
};

/**
 * Read the conscious LLM identity from the database (provider, model,
 * endpoint). Credentials never reach the dashboard: the Python API resolves
 * saved keys and sign-ins when it makes the call.
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
  const endpoint =
    typeof config.endpoint === "string" && config.endpoint.trim() ? config.endpoint.trim() : null;
  return { provider: (config.provider as string).toLowerCase(), model: config.model as string, endpoint };
}

/**
 * Call the conscious model with a system + user prompt pair and return text.
 * One path for every provider: the Python API makes the call through the
 * same LLM layer the agent uses.
 */
export async function callLlm(params: {
  config: ResolvedLlmConfig;
  system: string;
  user: string;
  temperature?: number;
  maxTokens?: number;
  jsonMode?: boolean;
}): Promise<string> {
  const { system, user, temperature = 0.3, maxTokens = 4000, jsonMode = false } = params;
  const response = await fetch(resolveHexisApiUrl("/api/init/llm/complete"), {
    method: "POST",
    headers: hexisApiHeaders({ "content-type": "application/json" }),
    body: JSON.stringify({ system, user, temperature, max_tokens: maxTokens, json_mode: jsonMode }),
    cache: "no-store",
  });
  const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;
  if (!response.ok) {
    const message = typeof payload.error === "string" ? payload.error : `Hexis API responded ${response.status}`;
    throw new Error(message);
  }
  return typeof payload.content === "string" ? payload.content : "";
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
