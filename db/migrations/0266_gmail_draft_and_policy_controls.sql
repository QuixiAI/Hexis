-- Gmail draft capability + mechanical enforcement of Gmail heartbeat autonomy
-- and memory policies inside evaluate_tool_call (#100).
--
-- 1. Adds the "draft" capability (gmail.compose) to the Gmail connector manifest.
-- 2. Maps gmail_draft -> (gmail, draft) in connector_action_tool_map so drafts
--    are governed by connector_action_policies and audited in connector_action_audit.
-- 3. Mechanically enforces integrations.gmail.heartbeat_digest_enabled for
--    heartbeat inbox read tools and integrations.gmail.memory_policy='remember'
--    for ingest_emails inside evaluate_tool_call().

SET search_path = public, ag_catalog, "$user";
SET check_function_bodies = off;

UPDATE integration_connectors
SET capability_manifest = capability_manifest || jsonb_build_object(
        'draft',
        jsonb_build_object(
            'label', 'Create draft messages and replies',
            'scope_kind', 'write',
            'status', 'available',
            'scopes', jsonb_build_array('https://www.googleapis.com/auth/gmail.compose')
        )
    ),
    setup_manifest = setup_manifest
        || jsonb_build_object(
            'capability_order',
            '["read","search","ingest","label","spam_triage","draft","send","reply","delete"]'::jsonb,
            'scope_order',
            '["https://www.googleapis.com/auth/userinfo.email","https://www.googleapis.com/auth/gmail.readonly","https://www.googleapis.com/auth/gmail.compose","https://www.googleapis.com/auth/gmail.modify","https://www.googleapis.com/auth/gmail.send"]'::jsonb,
            'capability_aliases',
            COALESCE(setup_manifest->'capability_aliases', '{}'::jsonb)
                || jsonb_build_object('compose', 'draft', 'drafts', 'draft')
        ),
    updated_at = CURRENT_TIMESTAMP
WHERE id = 'gmail';

INSERT INTO connector_action_tool_map (
    tool_name,
    connector_id,
    action_kind,
    target_argument,
    account_argument,
    sensitivity,
    metadata
) VALUES (
    'gmail_draft',
    'gmail',
    'draft',
    'to',
    'account_key',
    'provider_state_change',
    '{"tool_module": "core.tools.gmail_actions"}'::jsonb
)
ON CONFLICT (tool_name) DO UPDATE SET
    connector_id = EXCLUDED.connector_id,
    action_kind = EXCLUDED.action_kind,
    target_argument = EXCLUDED.target_argument,
    account_argument = EXCLUDED.account_argument,
    sensitivity = EXCLUDED.sensitivity,
    enabled = TRUE,
    metadata = connector_action_tool_map.metadata || EXCLUDED.metadata,
    updated_at = CURRENT_TIMESTAMP;

CREATE OR REPLACE FUNCTION evaluate_tool_call(
    p_tool_name TEXT,
    p_arguments JSONB DEFAULT '{}'::jsonb,
    p_context JSONB DEFAULT '{}'::jsonb
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    tool tool_definitions%ROWTYPE;
    ctx TEXT := lower(COALESCE(p_context->>'tool_context', p_context->>'context', 'chat'));
    energy_available INT;
    cfg JSONB := COALESCE(get_config('tools'), '{}'::jsonb);
    ctx_cfg JSONB;
    cost INT;
    max_per_tool INT;
    boundary TEXT;
    action_policy JSONB;
    approval_ok BOOLEAN := FALSE;
    approval_request UUID;
BEGIN
    SELECT * INTO tool FROM tool_definitions WHERE name = p_tool_name;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('allowed', false, 'reason', 'Unknown tool: ' || p_tool_name, 'error_type', 'unknown_tool');
    END IF;

    IF NOT tool_config_enabled(tool.name, tool.category, ctx, COALESCE((tool.metadata->>'optional')::boolean, false)) THEN
        RETURN jsonb_build_object('allowed', false, 'reason', format('Tool %L is disabled', tool.name), 'error_type', 'disabled');
    END IF;
    IF COALESCE(array_length(tool.allowed_contexts, 1), 0) > 0 AND NOT (ctx = ANY(tool.allowed_contexts)) THEN
        RETURN jsonb_build_object('allowed', false, 'reason', format('Tool %L not allowed in %s context', tool.name, ctx), 'error_type', 'context_denied');
    END IF;

    -- Mechanical enforcement of Gmail autonomy and memory policies (#100):
    -- heartbeat inbox reading requires integrations.gmail.heartbeat_digest_enabled,
    -- and ingest_emails requires integrations.gmail.memory_policy = 'remember'.
    IF ctx = 'heartbeat'
       AND tool.name IN ('email_list', 'email_read', 'email_search', 'ingest_emails')
       AND NOT COALESCE(get_config_bool('integrations.gmail.heartbeat_digest_enabled'), FALSE) THEN
        RETURN jsonb_build_object(
            'allowed', false,
            'reason', 'Autonomous Gmail checks during heartbeat are disabled (integrations.gmail.heartbeat_digest_enabled is false). Enable heartbeat email checks in Connections → Gmail to allow background inbox reading.',
            'error_type', 'context_denied'
        );
    END IF;

    IF tool.name = 'ingest_emails'
       AND lower(COALESCE(get_config_text('integrations.gmail.memory_policy'), 'ask')) <> 'remember' THEN
        RETURN jsonb_build_object(
            'allowed', false,
            'reason', format(
                'Email memory ingestion is blocked because integrations.gmail.memory_policy is %L (must be ''remember''). Update the Gmail memory policy in Connections → Gmail first.',
                COALESCE(get_config_text('integrations.gmail.memory_policy'), 'ask')
            ),
            'error_type', 'context_denied'
        );
    END IF;

    cost := COALESCE(NULLIF(cfg #>> ARRAY['costs', tool.name], '')::int, tool.default_energy_cost);
    IF ctx = 'heartbeat' AND p_context ? 'energy_available' THEN
        BEGIN energy_available := NULLIF(p_context->>'energy_available', '')::int;
        EXCEPTION WHEN OTHERS THEN energy_available := NULL; END;
        ctx_cfg := COALESCE(cfg #> '{context_overrides,heartbeat}', '{}'::jsonb);
        BEGIN max_per_tool := NULLIF(ctx_cfg->>'max_energy_per_tool', '')::int;
        EXCEPTION WHEN OTHERS THEN max_per_tool := NULL; END;
        IF max_per_tool IS NOT NULL AND cost > max_per_tool THEN
            RETURN jsonb_build_object('allowed', false, 'reason', format('Tool %L cost (%s) exceeds max per tool (%s)', tool.name, cost, max_per_tool), 'error_type', 'insufficient_energy', 'energy_cost', cost);
        END IF;
        IF energy_available IS NOT NULL AND cost > energy_available THEN
            RETURN jsonb_build_object('allowed', false, 'reason', format('Insufficient energy: need %s, have %s', cost, energy_available), 'error_type', 'insufficient_energy', 'energy_cost', cost);
        END IF;
    END IF;

    boundary := tool_boundary_violation(tool.name, tool.category);
    IF boundary IS NOT NULL THEN
        RETURN jsonb_build_object('allowed', false, 'reason', 'Boundary restriction: ' || boundary, 'error_type', 'boundary_violation', 'energy_cost', cost);
    END IF;

    IF tool.requires_approval AND NULLIF(p_context->>'approval_request_id', '') IS NOT NULL THEN
        BEGIN
            approval_request := (p_context->>'approval_request_id')::uuid;
            approval_ok := consume_operator_tool_approval(
                approval_request, tool.name, p_arguments, p_context
            );
        EXCEPTION WHEN OTHERS THEN
            approval_ok := FALSE;
        END;
    END IF;

    IF tool.requires_approval
       AND NULLIF(p_context->>'approval_request_id', '') IS NOT NULL
       AND NOT approval_ok THEN
        RETURN jsonb_build_object(
            'allowed', false,
            'reason', format('The operator approval proof for tool %L is invalid, expired, mismatched, or already consumed', tool.name),
            'error_type', 'approval_required',
            'energy_cost', cost
        );
    END IF;

    IF tool.requires_approval
       AND ctx <> 'chat'
       AND NOT is_tool_approved(tool.name)
       AND NOT approval_ok THEN
        RETURN jsonb_build_object('allowed', false, 'reason', format('Tool %L requires approval for autonomous use', tool.name), 'error_type', 'approval_required', 'energy_cost', cost);
    END IF;

    action_policy := evaluate_connector_action_call(
        p_tool_name,
        COALESCE(p_arguments, '{}'::jsonb),
        p_context || jsonb_build_object('action_approved', approval_ok)
    );
    IF NOT COALESCE((action_policy->>'allowed')::boolean, FALSE) THEN
        RETURN jsonb_build_object(
            'allowed', false,
            'reason', action_policy->>'reason',
            'error_type', COALESCE(action_policy->>'error_type', 'approval_required'),
            'energy_cost', cost,
            'connector_action', action_policy
        );
    END IF;

    RETURN jsonb_build_object(
        'allowed', true,
        'energy_cost', cost,
        'supports_parallel', tool.supports_parallel,
        'execution_kind', tool.execution_kind,
        'driver', tool.driver,
        'connector_action', action_policy,
        'operator_approval_request_id', CASE WHEN approval_ok THEN approval_request::text ELSE NULL END
    );
END;
$$;
