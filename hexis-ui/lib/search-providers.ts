/** Search setup metadata shared by the chat form and settings API. */
export type SearchProviderDefinition = {
  id: string;
  label: string;
  requires_credential: boolean;
  hint: string;
  credential_key?: string;
  credential_env?: string;
};

export const SEARCH_PROVIDERS = [
  {
    id: "auto",
    label: "Automatic",
    requires_credential: false,
    hint: "Use the best available configured provider, then keyless fallbacks.",
  },
  {
    id: "tavily",
    credential_key: "tavily",
    credential_env: "TAVILY_API_KEY",
    label: "Tavily",
    requires_credential: true,
    hint: "High-quality search API. Provide a key or env reference.",
  },
  {
    id: "brave",
    credential_key: "brave_search",
    credential_env: "BRAVE_SEARCH_API_KEY",
    label: "Brave Search",
    requires_credential: true,
    hint: "Brave Search API. Provide BRAVE_SEARCH_API_KEY or a direct key.",
  },
  {
    id: "keenable",
    credential_key: "keenable",
    credential_env: "KEENABLE_API_KEY",
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

export type SearchConfigProvider = (typeof SEARCH_PROVIDERS)[number]["id"];

export function searchProvider(id: string): SearchProviderDefinition | undefined {
  return SEARCH_PROVIDERS.find((provider) => provider.id === id);
}

export function searchConfigPlaceholder(id: string): string {
  if (id === "searxng") return "https://searxng.example";
  const provider = searchProvider(id);
  if (provider?.credential_env) {
    const prefix = provider.requires_credential ? "API key" : "Optional API key";
    return `${prefix} or env:${provider.credential_env}`;
  }
  return "No key needed";
}
