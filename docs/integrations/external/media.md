<!--
title: Media
summary: YouTube, Twitter/X, Fathom, and image/video generation integrations
read_when:
  - "You want to enable YouTube, Twitter, or media generation"
section: integrations
-->

# Media

YouTube analytics, Twitter/X search, Fathom transcripts, and image/video generation.

## YouTube

### Setup

```bash
hexis tools set-api-key youtube env:YOUTUBE_API_KEY
hexis tools enable youtube_search
```

| Tool | Energy | Description |
|------|--------|-------------|
| `youtube_*` | 1 | YouTube Data API operations (search, channel info, video details) |

Get your API key from [Google Cloud Console](https://console.cloud.google.com/) (YouTube Data API v3).

## Twitter/X

### Setup

```bash
hexis tools set-api-key xquik env:XQUIK_API_KEY
hexis tools enable twitter_search
```

| Tool | Energy | Description |
|------|--------|-------------|
| `twitter_search` | 2 | Search tweets and trends |

`twitter_search` uses FxTwitter first, then falls back to configured API providers:
TwitterAPI.io (`twitterapi_io`), Xquik (`xquik`), TwittrAPI (`twittrapi`), X API v2 (`x_api_bearer`),
and xAI Search (`xai`). Use the provider key name that matches your account.

## Fathom

### Setup

```bash
hexis tools set-api-key fathom env:FATHOM_API_KEY
hexis tools enable fathom_transcripts
```

| Tool | Energy | Description |
|------|--------|-------------|
| `fathom_transcripts` | 2 | Retrieve meeting transcripts from Fathom |
| `fathom_ingest` | 4 | Ingest Fathom transcripts into memory |

## Image Generation

### DALL-E (OpenAI)

Uses your existing OpenAI API key:

```bash
hexis tools enable generate_image
```

### Stability AI

```bash
hexis tools set-api-key stability env:STABILITY_API_KEY
hexis tools enable generate_image
```

| Tool | Energy | Description |
|------|--------|-------------|
| `generate_image` | 3 | Generate images via DALL-E or Stability AI |

## Video Generation

### Runway ML

```bash
hexis tools set-api-key runway env:RUNWAY_API_KEY
hexis tools enable generate_video
```

| Tool | Energy | Description |
|------|--------|-------------|
| `generate_video` | 8 | Generate video via Runway ML |

## Related

- [Tools Configuration](../../guides/tools-configuration.md) -- enabling tools
- [Skills](../../guides/skills.md) -- youtube-analytics and twitter-research skills
