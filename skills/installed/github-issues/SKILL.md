---
name: github-issues
description: Search, read, create, and comment on GitHub issues through the GitHub MCP server.
category: productivity
contexts: [heartbeat, chat]
mcp:
  server: github
  command: npx
  args: ["-y", "@modelcontextprotocol/server-github"]
  env_requires: [GITHUB_PERSONAL_ACCESS_TOKEN]
bound_tools:
  - github_setup_status
  - start_github_setup
  - mcp_github_search_issues
  - mcp_github_list_issues
  - mcp_github_get_issue
  - mcp_github_create_issue
  - mcp_github_add_issue_comment
  - mcp_github_update_issue
---

# GitHub Issues

Work with GitHub issues on repositories the configured token can reach. This skill
binds the GitHub MCP server: activating it starts the server (first use only) and
unlocks the bound tools for this turn.

## Workflow

1. **Search before creating.** Use `mcp_github_search_issues` to check whether the
   issue already exists; duplicate issues waste maintainer attention. Comment on the
   existing issue instead when appropriate.
2. **Creating an issue is an outward-facing action.** Confirm with the user before
   filing unless they explicitly asked you to file it. Show the title and body you
   intend to submit.
3. Reference concrete evidence (file paths, line numbers, reproduction steps) that
   you actually verified this turn.
4. After creating, report the issue URL from the tool result — never invent one.

## If activation reports needs_setup

1. **Call `start_github_setup`** with a non-secret `pending_action` capturing what the
   user asked to do (`action`, `repo`, `query`, `title`, `body`, `labels`,
   `issue_number`, `authorized`). Never ask for or pass a token in chat or tool args.
2. **Guide the user cleanly (#102):** explain that public repositories can be read
   publicly, but creating, commenting on, or updating an issue requires authenticating
   as a specific GitHub account. Give them both productized setup paths:
   - The **Connections** page in the web dashboard (**GitHub Issues** card), or
   - Running `hexis auth github setup-token` in a terminal.
   Do not claim GitHub capability is missing — it is installed and one step away.
3. **Resume immediately after setup:** once the user says they connected GitHub, check
   `github_setup_status` (or activate `github-issues` directly) and resume the saved
   `pending_action` without asking them to restate the repository, title, or body.
   If `authorized` was false, confirm the title and body before filing.
