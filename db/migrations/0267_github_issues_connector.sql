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

-- Keep complete_connection_attempt atomic when a single-slot github_issues
-- credential_ref (token.github_pat) is replaced by another GitHub login (#102),
-- preserving 0265's errored sync cursor recovery.
CREATE OR REPLACE FUNCTION complete_connection_attempt(
    p_attempt_id UUID,
    p_account_key TEXT,
    p_display_name TEXT DEFAULT NULL,
    p_credential_ref TEXT DEFAULT NULL,
    p_granted_scopes TEXT[] DEFAULT ARRAY[]::TEXT[],
    p_capabilities JSONB DEFAULT '[]'::jsonb,
    p_metadata JSONB DEFAULT '{}'::jsonb
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    row_attempt connection_attempts%ROWTYPE;
    row_connection integration_connections%ROWTYPE;
    normalized_account TEXT := NULLIF(btrim(COALESCE(p_account_key, '')), '');
    capability_input JSONB;
    prepared JSONB;
BEGIN
    IF normalized_account IS NULL THEN
        RAISE EXCEPTION 'account_key is required';
    END IF;

    SELECT * INTO row_attempt
    FROM connection_attempts
    WHERE id = p_attempt_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'connection attempt % not found', p_attempt_id;
    END IF;

    IF p_capabilities IS NULL
       OR p_capabilities = 'null'::jsonb
       OR (jsonb_typeof(p_capabilities) = 'array' AND jsonb_array_length(p_capabilities) = 0) THEN
        capability_input := row_attempt.requested_capabilities;
    ELSE
        capability_input := p_capabilities;
    END IF;
    prepared := prepare_connection_attempt(row_attempt.connector_id, capability_input);

    INSERT INTO integration_connections (
        connector_id,
        account_key,
        display_name,
        status,
        credential_ref,
        granted_scopes,
        capabilities,
        source_channel,
        source_session_id,
        metadata,
        connected_at,
        last_verified_at,
        revoked_at,
        last_error
    )
    VALUES (
        row_attempt.connector_id,
        normalized_account,
        NULLIF(btrim(COALESCE(p_display_name, '')), ''),
        'connected',
        NULLIF(btrim(COALESCE(p_credential_ref, '')), ''),
        COALESCE(p_granted_scopes, ARRAY[]::TEXT[]),
        prepared->'capabilities',
        row_attempt.source_channel,
        row_attempt.source_session_id,
        COALESCE(p_metadata, '{}'::jsonb),
        CURRENT_TIMESTAMP,
        CURRENT_TIMESTAMP,
        NULL,
        NULL
    )
    ON CONFLICT (connector_id, account_key) DO UPDATE SET
        display_name = COALESCE(EXCLUDED.display_name, integration_connections.display_name),
        status = 'connected',
        credential_ref = COALESCE(EXCLUDED.credential_ref, integration_connections.credential_ref),
        granted_scopes = EXCLUDED.granted_scopes,
        capabilities = EXCLUDED.capabilities,
        source_channel = COALESCE(EXCLUDED.source_channel, integration_connections.source_channel),
        source_session_id = COALESCE(EXCLUDED.source_session_id, integration_connections.source_session_id),
        metadata = integration_connections.metadata || EXCLUDED.metadata,
        connected_at = COALESCE(integration_connections.connected_at, CURRENT_TIMESTAMP),
        last_verified_at = CURRENT_TIMESTAMP,
        revoked_at = NULL,
        last_error = NULL,
        updated_at = CURRENT_TIMESTAMP
    RETURNING * INTO row_connection;

    IF row_attempt.connector_id = 'github_issues'
       AND row_connection.credential_ref IS NOT NULL THEN
        UPDATE integration_connections
        SET status = 'revoked',
            credential_ref = NULL,
            last_error = 'Replaced by another GitHub account',
            revoked_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE connector_id = row_attempt.connector_id
          AND account_key <> normalized_account
          AND credential_ref = row_connection.credential_ref
          AND status = 'connected';
    END IF;

    UPDATE connection_attempts
    SET account_key = normalized_account,
        status = 'complete',
        credential_ref = p_credential_ref,
        error = NULL,
        completed_at = CURRENT_TIMESTAMP,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_attempt_id;

    IF to_regclass('public.connector_sync_cursors') IS NOT NULL THEN
        UPDATE connector_sync_cursors
        SET status = 'active',
            last_error = NULL,
            last_started_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE connection_id = row_connection.id
          AND status = 'error';
    END IF;

    RETURN jsonb_build_object(
        'attempt_id', row_attempt.id::text,
        'connector_id', row_connection.connector_id,
        'connection_id', row_connection.id::text,
        'account_key', row_connection.account_key,
        'display_name', row_connection.display_name,
        'status', row_connection.status,
        'credential_ref', row_connection.credential_ref,
        'granted_scopes', to_jsonb(row_connection.granted_scopes),
        'capabilities', row_connection.capabilities,
        'connected_at', row_connection.connected_at
    );
END;
$$;
