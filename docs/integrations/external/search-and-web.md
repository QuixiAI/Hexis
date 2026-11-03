<!--
title: Search and Web
summary: Brave Search, Firecrawl, and web tool integrations
read_when:
  - "You want to enable web search"
  - "You want to scrape web pages"
section: integrations
-->

# Search and Web

Enable web search, content fetching, and page scraping capabilities.

## Built-in Web Tools

These tools are available in the default tool registry:

| Tool | Energy | Description |
|------|--------|-------------|
| `web_search` | 2 | Search the web through the configured provider |
| `web_fetch` | 2 | Fetch and extract content from a URL |
| `web_summarize` | 4 | Fetch a URL and generate a summary |

`web_search` is provider-neutral. It uses the highest-quality available provider
in this order:

1. `tavily` when `TAVILY_API_KEY` or `api_keys.tavily` is configured.
2. `brave` when `BRAVE_SEARCH_API_KEY` or `api_keys.brave_search` is configured.
3. `keenable` when `KEENABLE_API_KEY` or `api_keys.keenable` is configured.
4. `searxng` when `SEARXNG_URL` or `web_search.searxng_url` is configured.
5. `duckduckgo_lite`, keyless fallback.
6. `bing_rss`, keyless secondary fallback.

Choose a provider explicitly:

```bash
hexis tools web-search status
hexis tools web-search set-provider auto
hexis tools web-search set-provider tavily
hexis tools web-search set-searxng-url http://localhost:8080
```

## Keenable

Keenable works without an API key when selected explicitly; a key (optional)
switches to the keyed endpoint and adds it to automatic selection.

```bash
hexis tools web-search set-provider keenable
# optional, for higher rate limits:
hexis tools set-api-key keenable env:KEENABLE_API_KEY
```

## Brave Search

### Setup

```bash
hexis tools set-api-key brave_search env:BRAVE_SEARCH_API_KEY
hexis tools web-search set-provider brave
hexis tools enable brave_search
```

| Tool | Energy | Description |
|------|--------|-------------|
| `brave_search` | 2 | Search via Brave Search API |

Get your API key from [Brave Search API](https://brave.com/search/api/).

## Firecrawl

### Setup

```bash
hexis tools set-api-key firecrawl env:FIRECRAWL_API_KEY
hexis tools enable firecrawl_scrape
```

| Tool | Energy | Description |
|------|--------|-------------|
| `firecrawl_scrape` | 3 | Scrape and extract structured content from web pages |

Get your API key from [Firecrawl](https://firecrawl.dev/).

## Browser Automation

For interactive web automation (filling forms, clicking buttons, navigating):

| Tool | Energy | Description |
|------|--------|-------------|
| `browser` | 4 | Headless browser automation via Playwright/CDP |

Requires the `browser` Docker Compose profile:

```bash
docker compose --profile active --profile browser up -d
```

## Related

- [Tools Configuration](../../guides/tools-configuration.md) -- enabling tools
- [Docker Compose](../../operations/docker-compose.md) -- browser profile
