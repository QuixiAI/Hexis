import { prisma } from "@/lib/prisma";
import { normalizeJsonValue } from "@/lib/db";
import { composePersonhoodPrompt, loadConversationPrompt, loadOptionalPrompts } from "@/lib/prompts";
import { createXai } from "@ai-sdk/xai";
import Anthropic from "@anthropic-ai/sdk";
import { GoogleGenAI } from "@google/genai";
import OpenAI from "openai";
import { streamText as streamAiText } from "ai";
import { randomUUID } from "crypto";
import path from "path";
import { readFile } from "fs/promises";

export const runtime = "nodejs";

type ChatRequest = {
  message?: string;
  history?: { role: "user" | "assistant"; content: string }[];
  prompt_addenda?: string[];
};

type LlmConfig = {
  provider: string;
  model: string;
  endpoint?: string | null;
  api_key?: string | null;
  api_key_env?: string | null;
};

const DEFAULT_PROVIDER = process.env.LLM_PROVIDER || "openai";
const DEFAULT_MODEL = process.env.LLM_MODEL || "gpt-5.2";
const PROMPT_ROOT = path.resolve(process.cwd(), "..", "services", "prompts");
const GOAL_PRIORITIES = new Set(["active", "queued", "backburner", "completed", "abandoned"]);
const GOAL_SOURCES = new Set(["curiosity", "user_request", "identity", "derived", "external"]);

const TOOL_DEFINITIONS: Record<string, { description: string; parameters: Record<string, unknown> }> = {
  recall: {
    description: "Search memories by semantic similarity.",
    parameters: {
      query: "string (required)",
      limit: "integer (optional, default 5)",
      memory_types: "array of memory types (optional)",
      min_importance: "number 0..1 (optional)",
    },
  },
  sense_memory_availability: {
    description: "Estimate whether relevant memories exist before recalling.",
    parameters: { query: "string (required)" },
  },
  request_background_search: {
    description: "Request a background memory search after a failed recall.",
    parameters: { query: "string (required)" },
  },
  recall_recent: {
    description: "Retrieve recently accessed or created memories.",
    parameters: { limit: "integer (optional, default 5)" },
  },
  recall_episode: {
    description: "Fetch all memories from a specific episode.",
    parameters: { episode_id: "uuid (required)" },
  },
  explore_concept: {
    description: "Explore memories linked to a concept in the graph.",
    parameters: { concept: "string (required)", limit: "integer (optional, default 5)" },
  },
  explore_cluster: {
    description: "Explore clusters related to a query with sample memories.",
    parameters: { query: "string (required)", limit: "integer (optional, default 3)" },
  },
  get_procedures: {
    description: "Recall procedural knowledge for a task.",
    parameters: { task: "string (required)", limit: "integer (optional, default 3)" },
  },
  get_strategies: {
    description: "Recall strategic knowledge for a topic.",
    parameters: { topic: "string (required)", limit: "integer (optional, default 3)" },
  },
  list_recent_episodes: {
    description: "List recent episodes for orientation.",
    parameters: { limit: "integer (optional, default 5)" },
  },
  create_goal: {
    description: "Create a new queued or active goal.",
    parameters: {
      title: "string (required)",
      description: "string (optional)",
      priority: "active|queued|backburner|completed|abandoned (optional)",
      source: "curiosity|user_request|identity|derived|external (optional)",
    },
  },
  queue_user_message: {
    description: "Queue a user-facing message payload.",
    parameters: { message: "string (required)", intent: "string (optional)" },
  },
};

function normalizeProvider(value: unknown) {
  if (typeof value !== "string") return DEFAULT_PROVIDER;
  const raw = value.trim().toLowerCase();
  return raw || DEFAULT_PROVIDER;
}

function normalizeEndpoint(provider: string, endpoint: unknown) {
  if (provider === "openai_compatible" || provider === "ollama") {
    if (typeof endpoint === "string" && endpoint.trim()) {
      return endpoint.trim();
    }
    if (provider === "ollama") return "http://localhost:11434/v1";
    return "http://localhost:8000/v1";
  }
  return null;
}

function resolveApiKey(config: LlmConfig) {
  if (config.api_key) return config.api_key;
  if (config.api_key_env && process.env[config.api_key_env]) {
    return process.env[config.api_key_env] || null;
  }
  switch (config.provider) {
    case "anthropic":
      return process.env.ANTHROPIC_API_KEY || null;
    case "gemini":
      return process.env.GEMINI_API_KEY || null;
    case "grok":
      return process.env.XAI_API_KEY || null;
    case "openai":
    case "openai_compatible":
    case "ollama":
    default:
      return process.env.OPENAI_API_KEY || null;
  }
}

function requiresApiKey(provider: string) {
  return !["ollama", "openai_compatible"].includes(provider);
}

async function loadLlmConfig(key: string, fallbackKey?: string) {
  let row = await prisma.$queryRaw<{ cfg: unknown }[]>`SELECT get_config(${key}) as cfg`;
  let cfg = normalizeJsonValue(row[0]?.cfg);
  if ((!cfg || typeof cfg !== "object") && fallbackKey) {
    row = await prisma.$queryRaw<{ cfg: unknown }[]>`SELECT get_config(${fallbackKey}) as cfg`;
    cfg = normalizeJsonValue(row[0]?.cfg);
  }
  const config = (cfg && typeof cfg === "object") ? (cfg as Record<string, unknown>) : {};
  const provider = normalizeProvider(config.provider);
  const model = typeof config.model === "string" && config.model.trim() ? config.model.trim() : DEFAULT_MODEL;
  const endpoint = normalizeEndpoint(provider, config.endpoint);
  const api_key_env = typeof config.api_key_env === "string" ? config.api_key_env : null;
  const api_key = typeof config.api_key === "string" ? config.api_key : null;
  return {
    provider,
    model,
    endpoint,
    api_key_env,
    api_key,
  } as LlmConfig;
}

function formatHistory(history: { role: "user" | "assistant"; content: string }[]) {
  if (!history?.length) return "";
  return history
    .slice(-8)
    .map((entry) => `${entry.role === "user" ? "User" : "Assistant"}: ${entry.content}`)
    .join("\n");
}

function extractJsonPayload(text: string) {
  if (!text) return {};
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start < 0 || end < 0 || end <= start) return {};
  try {
    return JSON.parse(text.slice(start, end + 1));
  } catch {
    return {};
  }
}

async function streamOpenAI(params: {
  config: LlmConfig;
  system: string;
  user: string;
  temperature: number;
  maxTokens: number;
  onToken: (text: string) => void;
}) {
  const client = new OpenAI({
    apiKey: params.config.api_key || "local-key",
    baseURL: params.config.endpoint || undefined,
  });
  const stream = await client.chat.completions.create({
    model: params.config.model,
    messages: [
      { role: "system", content: params.system },
      { role: "user", content: params.user },
    ],
    temperature: params.temperature,
    max_tokens: params.maxTokens,
    stream: true,
  });
  let full = "";
  for await (const chunk of stream) {
    const delta = chunk.choices?.[0]?.delta?.content;
    if (delta) {
      full += delta;
      params.onToken(delta);
    }
  }
  return full;
}

async function streamAnthropic(params: {
  config: LlmConfig;
  system: string;
  user: string;
  temperature: number;
  maxTokens: number;
  onToken: (text: string) => void;
}) {
  const client = new Anthropic({ apiKey: params.config.api_key || "" });
  const stream = await client.messages.create({
    model: params.config.model,
    system: params.system,
    messages: [{ role: "user", content: params.user }],
    max_tokens: params.maxTokens,
    temperature: params.temperature,
    stream: true,
  });
  let full = "";
  for await (const event of stream) {
    const delta = (event as any)?.delta;
    const text =
      (typeof delta?.text === "string" && delta.text) ||
      (typeof (event as any)?.text === "string" ? (event as any).text : "") ||
      "";
    if (text) {
      full += text;
      params.onToken(text);
    }
  }
  return full;
}

async function streamGemini(params: {
  config: LlmConfig;
  system: string;
  user: string;
  temperature: number;
  maxTokens: number;
  onToken: (text: string) => void;
}) {
  const client = new GoogleGenAI({ apiKey: params.config.api_key || "" });
  const modelApi: any = (client as any).models;
  if (modelApi && typeof modelApi.generateContentStream === "function") {
    const stream = await modelApi.generateContentStream({
      model: params.config.model,
      contents: params.user,
      config: {
        systemInstruction: params.system,
        temperature: params.temperature,
        maxOutputTokens: params.maxTokens,
      },
    });
    let full = "";
    for await (const chunk of stream) {
      const next = (chunk as any)?.text ?? (chunk as any)?.content ?? "";
      if (typeof next === "string" && next) {
        const delta = next.slice(full.length);
        if (delta) {
          params.onToken(delta);
        }
        full = next;
      }
    }
    return full;
  }
  const response = await modelApi.generateContent({
    model: params.config.model,
    contents: params.user,
    config: {
      systemInstruction: params.system,
      temperature: params.temperature,
      maxOutputTokens: params.maxTokens,
    },
  });
  const text = response?.text ?? "";
  for (const token of text.split(/(\s+)/)) {
    if (token) {
      params.onToken(token);
    }
  }
  return text;
}

async function streamGrok(params: {
  config: LlmConfig;
  system: string;
  user: string;
  temperature: number;
  maxTokens: number;
  onToken: (text: string) => void;
}) {
  const client = createXai({ apiKey: params.config.api_key || "" });
  const result = await streamAiText({
    model: client.responses(params.config.model),
    system: params.system,
    prompt: params.user,
    temperature: params.temperature,
    maxOutputTokens: params.maxTokens,
  });
  let full = "";
  for await (const chunk of result.textStream) {
    if (!chunk) continue;
    full += chunk;
    params.onToken(chunk);
  }
  return full;
}

async function streamText(params: {
  config: LlmConfig;
  system: string;
  user: string;
  temperature: number;
  maxTokens: number;
  onToken: (text: string) => void;
}) {
  const provider = params.config.provider;
  if (provider === "anthropic") {
    return streamAnthropic(params);
  }
  if (provider === "gemini") {
    return streamGemini(params);
  }
  if (provider === "grok") {
    return streamGrok(params);
  }
  return streamOpenAI(params);
}

async function getChatContext(message: string) {
  const rows = await prisma.$queryRaw<{ ctx: unknown }[]>`
    SELECT get_chat_context(${message}, 8) as ctx
  `;
  return normalizeJsonValue(rows[0]?.ctx) ?? {};
}

async function getSubconsciousContext(message: string) {
  const rows = await prisma.$queryRaw<{ ctx: unknown }[]>`
    SELECT get_subconscious_chat_context(${message}, 12) as ctx
  `;
  return normalizeJsonValue(rows[0]?.ctx) ?? {};
}

async function recordSubconscious(prompt: string, response: unknown) {
  const rows = await prisma.$queryRaw<{ id: string }[]>`
    SELECT record_subconscious_exchange(${prompt}, ${JSON.stringify(response ?? {})}::jsonb) as id
  `;
  return rows[0]?.id ?? null;
}

async function recordChatTurn(prompt: string, response: string, context: unknown) {
  const rows = await prisma.$queryRaw<{ id: string }[]>`
    SELECT record_chat_turn(${prompt}, ${response}, ${JSON.stringify(context ?? {})}::jsonb) as id
  `;
  return rows[0]?.id ?? null;
}

function summarizeToolResult(result: any) {
  if (result === null || result === undefined) return "(empty)";
  if (typeof result === "string") return result.slice(0, 180);
  try {
    return JSON.stringify(result).slice(0, 180);
  } catch {
    return "(unserializable result)";
  }
}

function toolCatalogBlock(toolNames: string[]) {
  if (!toolNames.length) {
    return "TOOLS: none enabled.";
  }
  const lines = toolNames.map((name) => {
    const def = TOOL_DEFINITIONS[name];
    if (!def) {
      return `- ${name}`;
    }
    const params = Object.entries(def.parameters || {})
      .map(([key, val]) => `${key}: ${String(val)}`)
      .join(", ");
    return `- ${name}: ${def.description}${params ? ` (params: ${params})` : ""}`;
  });
  return ["TOOLS (allowed):", ...lines].join("\n");
}

async function executeToolCall(name: string, args: Record<string, any>) {
  switch (name) {
    case "recall": {
      const query = String(args.query || "").trim();
      const limit = Math.min(Math.max(Number(args.limit || 5), 1), 20);
      const rows = await prisma.$queryRaw<
        { memory_id: string; content: string; memory_type: string; score: number; source: string }[]
      >`
        SELECT * FROM fast_recall(${query}::text, ${limit}::int)
      `;
      const ids = rows.map((row) => row.memory_id).filter(Boolean);
      if (ids.length) {
        await prisma.$queryRaw`SELECT touch_memories(${ids}::uuid[])`;
      }
      return { memories: rows, count: rows.length };
    }
    case "sense_memory_availability": {
      const query = String(args.query || "").trim();
      const rows = await prisma.$queryRaw<{ result: unknown }[]>`
        SELECT sense_memory_availability(${query}) as result
      `;
      return normalizeJsonValue(rows[0]?.result);
    }
    case "request_background_search": {
      const query = String(args.query || "").trim();
      const rows = await prisma.$queryRaw<{ id: string }[]>`
        SELECT request_background_search(${query}) as id
      `;
      return { activation_id: rows[0]?.id ?? null };
    }
    case "recall_recent": {
      const limit = Math.min(Math.max(Number(args.limit || 5), 1), 20);
      const rows = await prisma.$queryRaw<
        { id: string; content: string; type: string; importance: number; created_at: string; last_accessed: string | null }[]
      >`
        SELECT id, content, type, importance, created_at, last_accessed
        FROM memories
        WHERE status = 'active'
        ORDER BY COALESCE(last_accessed, created_at) DESC
        LIMIT ${limit}
      `;
      const ids = rows.map((row) => row.id).filter(Boolean);
      if (ids.length) {
        await prisma.$queryRaw`SELECT touch_memories(${ids}::uuid[])`;
      }
      return { memories: rows, count: rows.length };
    }
    case "recall_episode": {
      const episodeId = String(args.episode_id || "").trim();
      const rows = await prisma.$queryRaw<
        { memory_id: string; content: string; memory_type: string; importance: number; trust_level: number; created_at: string }[]
      >`
        SELECT * FROM get_episode_memories(${episodeId}::uuid)
      `;
      const ids = rows.map((row) => row.memory_id).filter(Boolean);
      if (ids.length) {
        await prisma.$queryRaw`SELECT touch_memories(${ids}::uuid[])`;
      }
      return { episode_id: episodeId, memories: rows, count: rows.length };
    }
    case "explore_concept": {
      const concept = String(args.concept || "").trim();
      const limit = Math.min(Math.max(Number(args.limit || 5), 1), 20);
      const rows = await prisma.$queryRaw<
        { memory_id: string; memory_content: string; memory_importance: number; memory_type: string; memory_created_at: string; emotional_valence: number; link_strength: number }[]
      >`
        SELECT * FROM find_memories_by_concept(${concept}, ${limit})
      `;
      const ids = rows.map((row) => row.memory_id).filter(Boolean);
      if (ids.length) {
        await prisma.$queryRaw`SELECT touch_memories(${ids}::uuid[])`;
      }
      return { concept, memories: rows, count: rows.length };
    }
    case "explore_cluster": {
      const query = String(args.query || "").trim();
      const limit = Math.min(Math.max(Number(args.limit || 3), 1), 6);
      const clusters = await prisma.$queryRaw<
        { id: string; name: string; cluster_type: string; similarity: number }[]
      >`
        SELECT * FROM search_clusters_by_query(${query}, ${limit})
      `;
      const expanded = [] as any[];
      for (const cluster of clusters) {
        const samples = await prisma.$queryRaw<
          { memory_id: string; content: string; memory_type: string; membership_strength: number }[]
        >`
          SELECT * FROM get_cluster_sample_memories(${cluster.id}::uuid, 3)
        `;
        expanded.push({ ...cluster, samples });
      }
      return { query, clusters: expanded, count: expanded.length };
    }
    case "get_procedures": {
      const task = String(args.task || "").trim();
      const limit = Math.min(Math.max(Number(args.limit || 3), 1), 10);
      const rows = await prisma.$queryRaw<
        { id: string; content: string; importance: number; score: number }[]
      >`
        WITH query AS (SELECT get_embedding(${task}) as emb)
        SELECT m.id, m.content, m.importance, 1 - (m.embedding <=> (SELECT emb FROM query)) as score
        FROM memories m
        WHERE m.type = 'procedural' AND m.status = 'active' AND m.embedding IS NOT NULL
        ORDER BY m.embedding <=> (SELECT emb FROM query)
        LIMIT ${limit}
      `;
      const ids = rows.map((row) => row.id).filter(Boolean);
      if (ids.length) {
        await prisma.$queryRaw`SELECT touch_memories(${ids}::uuid[])`;
      }
      return { task, procedures: rows, count: rows.length };
    }
    case "get_strategies": {
      const topic = String(args.topic || args.task || "").trim();
      const limit = Math.min(Math.max(Number(args.limit || 3), 1), 10);
      const rows = await prisma.$queryRaw<
        { id: string; content: string; importance: number; score: number }[]
      >`
        WITH query AS (SELECT get_embedding(${topic}) as emb)
        SELECT m.id, m.content, m.importance, 1 - (m.embedding <=> (SELECT emb FROM query)) as score
        FROM memories m
        WHERE m.type = 'strategic' AND m.status = 'active' AND m.embedding IS NOT NULL
        ORDER BY m.embedding <=> (SELECT emb FROM query)
        LIMIT ${limit}
      `;
      const ids = rows.map((row) => row.id).filter(Boolean);
      if (ids.length) {
        await prisma.$queryRaw`SELECT touch_memories(${ids}::uuid[])`;
      }
      return { topic, strategies: rows, count: rows.length };
    }
    case "list_recent_episodes": {
      const limit = Math.min(Math.max(Number(args.limit || 5), 1), 20);
      const rows = await prisma.$queryRaw<
        { id: string; started_at: string; ended_at: string | null; episode_type: string | null; summary: string | null; memory_count: number }[]
      >`
        SELECT * FROM list_recent_episodes(${limit})
      `;
      return { episodes: rows, count: rows.length };
    }
    case "create_goal": {
      const title = String(args.title || "").trim();
      const description = typeof args.description === "string" ? args.description.trim() : null;
      const priorityRaw = typeof args.priority === "string" ? args.priority : "queued";
      const sourceRaw = typeof args.source === "string" ? args.source : "curiosity";
      const priority = GOAL_PRIORITIES.has(priorityRaw) ? priorityRaw : "queued";
      const source = GOAL_SOURCES.has(sourceRaw) ? sourceRaw : "curiosity";
      const rows = await prisma.$queryRaw<{ id: string }[]>`
        SELECT create_goal(${title}, ${description}, ${source}::goal_source, ${priority}::goal_priority, NULL, NULL) as id
      `;
      return { goal_id: rows[0]?.id ?? null };
    }
    case "queue_user_message": {
      const message = String(args.message || args.content || "").trim();
      const intent = typeof args.intent === "string" ? args.intent.trim() : null;
      return { queued: true, message, intent };
    }
    default:
      return { error: `Unknown tool: ${name}` };
  }
}

async function loadFilePrompt(name: string) {
  try {
    const promptPath = path.join(PROMPT_ROOT, name);
    return await readFile(promptPath, "utf-8");
  } catch {
    return "";
  }
}

export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({}))) as ChatRequest;
  const message = typeof body.message === "string" ? body.message.trim() : "";
  const history = Array.isArray(body.history) ? body.history : [];
  const promptAddenda = Array.isArray(body.prompt_addenda) ? body.prompt_addenda : [];

  if (!message) {
    return Response.json({ error: "Missing message" }, { status: 400 });
  }

  const encoder = new TextEncoder();

  const stream = new ReadableStream({
    start(controller) {
      const send = (event: string, payload: any) => {
        controller.enqueue(
          encoder.encode(`event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`)
        );
      };

      (async () => {
        try {
          const [chatConfig, subconsciousConfig] = await Promise.all([
            loadLlmConfig("llm.chat", "llm.heartbeat"),
            loadLlmConfig("llm.subconscious", "llm.chat"),
          ]);
          chatConfig.api_key = resolveApiKey(chatConfig);
          subconsciousConfig.api_key = resolveApiKey(subconsciousConfig);
          if (requiresApiKey(chatConfig.provider) && !chatConfig.api_key) {
            throw new Error(`Missing API key for ${chatConfig.provider} chat model.`);
          }
          if (requiresApiKey(subconsciousConfig.provider) && !subconsciousConfig.api_key) {
            throw new Error(`Missing API key for ${subconsciousConfig.provider} subconscious model.`);
          }

          const [chatContext, subconsciousContext] = await Promise.all([
            getChatContext(message),
            getSubconsciousContext(message),
          ]);
          send("log", {
            id: randomUUID(),
            kind: "memory_recall",
            title: "RAG recall",
            detail: `Retrieved ${Array.isArray(chatContext?.relevant_memories) ? chatContext.relevant_memories.length : 0} memories`,
          });

          const subconsciousPrompt = await loadFilePrompt("subconscious.md");
          send("phase_start", { phase: "subconscious" });
          const subconsciousText = await streamText({
            config: subconsciousConfig,
            system: subconsciousPrompt,
            user: JSON.stringify(subconsciousContext, null, 2),
            temperature: 0.3,
            maxTokens: 900,
            onToken: (text) => send("token", { phase: "subconscious", text }),
          });
          send("phase_end", { phase: "subconscious" });

          const subconsciousDoc = extractJsonPayload(subconsciousText);
          const subconsciousMemoryId = await recordSubconscious(message, subconsciousDoc);
          send("log", {
            id: randomUUID(),
            kind: "memory_write",
            title: "Subconscious memory stored",
            detail: subconsciousMemoryId || "(no id)",
          });

          const personhood = await composePersonhoodPrompt("conversation");
          const conversationPrompt = await loadConversationPrompt();
          const optionalPrompts = await loadOptionalPrompts(promptAddenda);
          const historyBlock = formatHistory(history);

          const rawTools = Array.isArray(chatContext?.agent?.tools)
            ? (chatContext.agent.tools as unknown[])
            : [];
          const toolNames = rawTools
            .map((tool) => {
              if (typeof tool === "string") return tool;
              if (tool && typeof tool === "object") {
                const name = (tool as Record<string, unknown>).name;
                if (typeof name === "string") return name;
              }
              return null;
            })
            .filter(Boolean) as string[];

          const baseContext = {
            chat_context: chatContext,
            subconscious: subconsciousDoc,
            conversation_history: historyBlock,
          };

          const systemBase = [
            conversationPrompt,
            personhood,
            ...optionalPrompts,
            toolCatalogBlock(toolNames),
            "---\nCONTEXT (JSON)",
            JSON.stringify(baseContext, null, 2),
          ]
            .filter(Boolean)
            .join("\n\n");

          const toolPlanPrompt =
            "You decide whether tools are needed before answering.\n" +
            "Return STRICT JSON only: {\"tool_calls\": [{\"name\": str, \"arguments\": object}]}.\n" +
            "Use only the allowed tool names. If no tools are needed, return {\"tool_calls\": []}.";

          send("phase_start", { phase: "conscious_plan" });
          const planText = await streamText({
            config: chatConfig,
            system: `${toolPlanPrompt}\n\n${systemBase}`,
            user: message,
            temperature: 0.2,
            maxTokens: 700,
            onToken: (text) => send("token", { phase: "conscious_plan", text }),
          });
          send("phase_end", { phase: "conscious_plan" });

          const planDoc = extractJsonPayload(planText) as any;
          const toolCalls = Array.isArray(planDoc?.tool_calls)
            ? planDoc.tool_calls.slice(0, 5)
            : [];

          const toolResults: any[] = [];
          for (const call of toolCalls) {
            const name = typeof call?.name === "string" ? call.name : "";
            if (!name || (toolNames.length && !toolNames.includes(name))) {
              toolResults.push({ name, error: "Tool not allowed" });
              continue;
            }
            send("log", {
              id: randomUUID(),
              kind: "tool_call",
              title: name,
              detail: JSON.stringify(call?.arguments ?? {}),
            });
            const result = await executeToolCall(name, call?.arguments ?? {});
            toolResults.push({ name, result });
            send("log", {
              id: randomUUID(),
              kind: "tool_result",
              title: name,
              detail: summarizeToolResult(result),
            });
          }

          const finalContext = {
            chat_context: chatContext,
            subconscious: subconsciousDoc,
            tool_results: toolResults,
            conversation_history: historyBlock,
          };

          const systemFinal = [
            conversationPrompt,
            personhood,
            ...optionalPrompts,
            toolCatalogBlock(toolNames),
            "---\nCONTEXT (JSON)",
            JSON.stringify(finalContext, null, 2),
          ]
            .filter(Boolean)
            .join("\n\n");

          send("phase_start", { phase: "conscious_final" });
          let assistantText = "";
          assistantText = await streamText({
            config: chatConfig,
            system: systemFinal,
            user: message,
            temperature: 0.7,
            maxTokens: 1400,
            onToken: (text) => {
              send("token", { phase: "conscious_final", text });
            },
          });
          send("phase_end", { phase: "conscious_final" });

          const memoryId = await recordChatTurn(message, assistantText, finalContext);
          send("log", {
            id: randomUUID(),
            kind: "memory_write",
            title: "Conversation memory stored",
            detail: memoryId || "(no id)",
          });

          send("done", { assistant: assistantText });
        } catch (err: any) {
          send("error", { message: err?.message || "Chat failed" });
        } finally {
          controller.close();
        }
      })();
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}
