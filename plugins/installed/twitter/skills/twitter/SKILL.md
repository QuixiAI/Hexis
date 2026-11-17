---
name: twitter
description: X/Twitter research with automatic search-provider fallback
category: research
requires:
  tools: [twitter_search]
contexts: [heartbeat, chat]
bound_tools: [twitter_search]
---

# Twitter

Use `twitter_search` for X/Twitter research. The tool tries a keyless provider
first, then configured fallback providers. It resolves the relevant credentials
itself; an absent xAI key alone does not mean search is unavailable.

Use the returned provider and results as the evidence. If the tool reports that
all providers failed, explain the reported cause and next step, then continue
without inventing search results.
