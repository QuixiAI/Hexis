import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { normalizeJsonValue } from "@/lib/db";

type ToolsConfigDoc = {
  enabled?: string[] | null;
  disabled?: string[];
  api_keys?: Record<string, string>;
  web_search?: Record<string, unknown>;
  [key: string]: unknown;
};

const PROVIDERS = [
  {
    id: "auto",
    label: "Automatic",
    requires_credential: false,
    hint: "Use the best available configured provider, then keyless fallbacks.",
  },
  {
    id: "tavily",
    label: "Tavily",
    requires_credential: true,
    hint: "High-quality search API. Provide a key or env reference.",
  },
  {
    id: "brave",
    label: "Brave Search",
    requires_credential: true,
    hint: "Brave Search API. Provide BRAVE_SEARCH_API_KEY or a direct key.",
  },
  {
    id: "keenable",
    label: "Keenable",
    requires_credential: false,
    hint: "Web search with no key required. Optionally provide KEENABLE_API_KEY for higher limits.",
  },
  {
    id: "searxng",
    label: "SearXNG",
    requires_credential: false,
    hint: "Self-hosted no-key metasearch. Provide a SearXNG base URL.",
  },
  {
    id: "duckduckgo_lite",
    label: "DuckDuckGo Lite",
    requires_credential: false,
    hint: "Keyless fallback provider.",
  },
  {
    id: "bing_rss",
    label: "Bing RSS",
    requires_credential: false,
    hint: "Keyless secondary fallback provider.",
  },
] as const;

const PROVIDER_IDS = new Set(PROVIDERS.map((provider) => provider.id));

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter(Boolean);
}

function parseToolsConfig(value: unknown): ToolsConfigDoc {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  return { ...(value as Record<string, unknown>) };
}

function enableTool(config: ToolsConfigDoc, toolName: string): void {
  const disabled = toStringArray(config.disabled).filter((name) => name !== toolName);
  config.disabled = disabled;

  if (Array.isArray(config.enabled)) {
    const enabled = toStringArray(config.enabled);
    if (!enabled.includes(toolName)) {
      enabled.push(toolName);
    }
    config.enabled = enabled;
  }
}

export async function GET() {
  try {
    const rows = await prisma.$queryRawUnsafe<{ value: unknown }[]>(
      "SELECT value FROM config WHERE key = 'tools' LIMIT 1"
    );
    const current = normalizeJsonValue(rows[0]?.value);
    const config = parseToolsConfig(current);
    const webSearch =
      config.web_search && typeof config.web_search === "object"
        ? config.web_search
        : {};
    return NextResponse.json({
      configured_provider:
        typeof webSearch.provider === "string" && webSearch.provider.trim()
          ? webSearch.provider
          : "auto",
      searxng_url:
        typeof webSearch.searxng_url === "string" ? webSearch.searxng_url : "",
      providers: PROVIDERS,
    });
  } catch (error: unknown) {
    console.error("Search tool config read failed:", error);
    return NextResponse.json(
      {
        error:
          error instanceof Error ? error.message : "Failed to read search tool config",
      },
      { status: 500 }
    );
  }
}

export async function POST(req: NextRequest) {
  let body: unknown = {};
  try {
    body = await req.json();
  } catch {
    body = {};
  }

  const payload = (body ?? {}) as Record<string, unknown>;
  const apiKey = typeof payload.api_key === "string" ? payload.api_key.trim() : "";
  const keyRef = typeof payload.key_ref === "string" ? payload.key_ref.trim() : "";
  const providerRaw =
    typeof payload.provider === "string" ? payload.provider.trim().toLowerCase() : "";
  const provider = providerRaw || (apiKey || keyRef ? "tavily" : "auto");
  const searxngUrl =
    typeof payload.searxng_url === "string" ? payload.searxng_url.trim().replace(/\/+$/, "") : "";
  const requestedEnable = payload.enable !== false;

  if (!PROVIDER_IDS.has(provider as (typeof PROVIDERS)[number]["id"])) {
    return NextResponse.json(
      { error: `Unknown web search provider: ${provider}` },
      { status: 400 }
    );
  }

  if ((provider === "tavily" || provider === "brave") && !apiKey && !keyRef) {
    const envName = provider === "tavily" ? "TAVILY_API_KEY" : "BRAVE_SEARCH_API_KEY";
    return NextResponse.json(
      { error: `Provide api_key or key_ref (for example: env:${envName}).` },
      { status: 400 }
    );
  }

  if (provider === "searxng" && !searxngUrl) {
    return NextResponse.json(
      { error: "Provide searxng_url for the SearXNG provider." },
      { status: 400 }
    );
  }

  if (searxngUrl && !searxngUrl.startsWith("http://") && !searxngUrl.startsWith("https://")) {
    return NextResponse.json(
      { error: "searxng_url must start with http:// or https://." },
      { status: 400 }
    );
  }

  const resolver = keyRef || apiKey;
  try {
    const rows = await prisma.$queryRawUnsafe<{ value: unknown }[]>(
      "SELECT value FROM config WHERE key = 'tools' LIMIT 1"
    );
    const current = normalizeJsonValue(rows[0]?.value);
    const nextConfig = parseToolsConfig(current);

    if (!nextConfig.api_keys || typeof nextConfig.api_keys !== "object") {
      nextConfig.api_keys = {};
    }
    if (!nextConfig.web_search || typeof nextConfig.web_search !== "object") {
      nextConfig.web_search = {};
    }

    if (provider === "auto") {
      delete nextConfig.web_search.provider;
    } else {
      nextConfig.web_search.provider = provider;
    }
    if (provider === "searxng") {
      nextConfig.web_search.searxng_url = searxngUrl;
    }
    if (resolver) {
      if (provider === "brave") {
        nextConfig.api_keys.brave_search = resolver;
      } else if (provider === "keenable") {
        nextConfig.api_keys.keenable = resolver;
      } else {
        nextConfig.api_keys.tavily = resolver;
      }
    }

    if (requestedEnable) {
      enableTool(nextConfig, "web_search");
    }

    await prisma.$queryRawUnsafe(
      `
      INSERT INTO config (key, value, description, updated_at)
      VALUES ('tools', $1::jsonb, 'Tool system configuration', NOW())
      ON CONFLICT (key) DO UPDATE SET value = $1::jsonb, updated_at = NOW()
      `,
      JSON.stringify(nextConfig)
    );

    // Also write legacy flat key so the existing settings table can display toggle state.
    await prisma.$queryRawUnsafe(
      `
      INSERT INTO config (key, value, updated_at)
      VALUES ('tools.web_search.enabled', 'true'::jsonb, NOW())
      ON CONFLICT (key) DO UPDATE SET value = 'true'::jsonb, updated_at = NOW()
      `
    );

    return NextResponse.json({
      ok: true,
      tool: "web_search",
      enabled: true,
      provider,
      key_source: keyRef ? "reference" : "direct",
    });
  } catch (error: unknown) {
    console.error("Search tool config update failed:", error);
    return NextResponse.json(
      {
        error:
          error instanceof Error ? error.message : "Failed to configure search tool",
      },
      { status: 500 }
    );
  }
}
