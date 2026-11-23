-- Register github_issues as a first-class integration connector (#102).
-- Secrets stay in ~/.hexis/auth (never in Postgres or chat transcripts).
SET search_path = public, ag_catalog, "$user";

INSERT INTO integration_connectors (
    id,
    display_name,
    category,
    auth_type,
    status,
    capability_manifest,
    setup_manifest,
    docs_url,
    metadata
) VALUES (
    'github_issues',
    'GitHub Issues',
    'productivity',
    'api_key',
    'available',
    '{
      "search_issues": {"label": "Search issues", "scope_kind": "read", "status": "available", "scopes": []},
      "read_issues": {"label": "Read and list issues", "scope_kind": "read", "status": "available", "scopes": []},
      "create_issue": {"label": "Create issues", "scope_kind": "write", "status": "available", "scopes": []},
      "comment_issue": {"label": "Comment on issues", "scope_kind": "write", "status": "available", "scopes": []},
      "update_issue": {"label": "Update issue state, labels, or assignees", "scope_kind": "modify", "status": "available", "scopes": []}
    }'::jsonb,
    '{
      "flow": "fine_grained_pat",
      "setup_label": "GitHub Issues token",
      "secret_storage": "~/.hexis/auth",
      "never_paste_in_chat": true,
      "supported_surfaces": ["chat", "cli", "web", "channels"],
      "default_capabilities": ["search_issues", "read_issues", "create_issue"],
      "capability_order": ["search_issues", "read_issues", "create_issue", "comment_issue", "update_issue"],
      "required_scopes": [],
      "scope_order": [],
      "token_create_url": "https://github.com/settings/personal-access-tokens/new",
      "cli_command": "hexis auth github setup-token",
      "capability_aliases": {
        "search": "search_issues",
        "read": "read_issues",
        "list": "read_issues",
        "create": "create_issue",
        "file": "create_issue",
        "open": "create_issue",
        "write": "create_issue",
        "comment": "comment_issue",
        "reply": "comment_issue",
        "update": "update_issue",
        "edit": "update_issue"
      },
      "setup_steps": [
        "Open https://github.com/settings/personal-access-tokens/new and create a fine-grained personal access token scoped to Issues (Read and write) on the target repository or repositories.",
        "Connect it on the Connections page in the web dashboard or run `hexis auth github setup-token` in a terminal.",
        "Never paste your token into chat. Hexis verifies the token against GitHub and stores it privately in ~/.hexis/auth.",
        "Return to your conversation to continue the pending GitHub action."
      ],
      "user_next_step": "Create a fine-grained GitHub token scoped to Issues (Read and write) at https://github.com/settings/personal-access-tokens/new, then save it on the Connections page or run `hexis auth github setup-token` in a terminal. Never paste the token into chat.",
      "notes": [
        "Public repositories can be read publicly, but creating, commenting on, or updating an issue requires authenticating as a specific GitHub account.",
        "Hexis stores the token privately in ~/.hexis/auth and never silently consumes ambient gh auth or GITHUB_TOKEN environment variables."
      ]
    }'::jsonb,
    'https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#creating-a-fine-grained-personal-access-token',
    '{"provider": "github", "skill": "github-issues", "seeded_by": "db/migrations/0267_github_issues_connector.sql"}'::jsonb
)
ON CONFLICT (id) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    category = EXCLUDED.category,
    auth_type = EXCLUDED.auth_type,
    status = EXCLUDED.status,
    capability_manifest = EXCLUDED.capability_manifest,
    setup_manifest = EXCLUDED.setup_manifest,
    docs_url = EXCLUDED.docs_url,
    metadata = integration_connectors.metadata || EXCLUDED.metadata,
    updated_at = CURRENT_TIMESTAMP;
