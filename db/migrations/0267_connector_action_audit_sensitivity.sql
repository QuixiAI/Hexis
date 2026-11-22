-- Connector action audit: record the sensitivity of each audited action (#161).
--
-- The integrations-status endpoint (apps/hexis_api.py and the hexis-ui status
-- route) selects 'sensitivity' from connector_action_audit, but the column
-- never existed, so status requests failed with "column does not exist"
-- (HTTP 500) and the Connections page could not load integration status.
-- connector_action_for_tool() already returns the sensitivity from
-- connector_action_tool_map; the audit writer just never stored it.

SET search_path = public, ag_catalog, "$user";
SET check_function_bodies = off;

ALTER TABLE IF EXISTS connector_action_audit
    ADD COLUMN IF NOT EXISTS sensitivity TEXT;

CREATE OR REPLACE FUNCTION record_connector_action_audit_from_tool_execution(
    p_tool_execution_id UUID,
    p_record JSONB
) RETURNS UUID
LANGUAGE plpgsql
AS $$
DECLARE
    action JSONB;
    decision JSONB;
    audit_id UUID;
    tool_context TEXT := lower(COALESCE(p_record->>'tool_context', 'unknown'));
    success BOOLEAN := COALESCE((p_record->>'success')::boolean, FALSE);
    decision_label TEXT;
BEGIN
    action := connector_action_for_tool(
        p_record->>'tool_name',
        COALESCE(p_record->'arguments', '{}'::jsonb)
    );
    IF NOT COALESCE((action->>'action_required')::boolean, FALSE) THEN
        RETURN NULL;
    END IF;

    decision := evaluate_connector_action_call(
        p_record->>'tool_name',
        COALESCE(p_record->'arguments', '{}'::jsonb),
        jsonb_build_object(
            'tool_context', tool_context,
            'call_id', p_record->>'call_id',
            'session_id', p_record->>'session_id'
        )
    );
    IF NOT COALESCE((decision->>'allowed')::boolean, FALSE) THEN
        decision_label := 'denied';
    ELSIF success THEN
        decision_label := 'allowed';
    ELSE
        decision_label := 'failed';
    END IF;

    INSERT INTO connector_action_audit (
        policy_id,
        tool_execution_id,
        connector_id,
        account_key,
        action_kind,
        target,
        sensitivity,
        tool_name,
        tool_context,
        decision,
        reason,
        arguments,
        context,
        external_receipt
    )
    VALUES (
        NULLIF(decision->>'policy_id', '')::uuid,
        p_tool_execution_id,
        action->>'connector_id',
        NULLIF(action->>'account_key', ''),
        action->>'action_kind',
        NULLIF(action->>'target', ''),
        NULLIF(action->>'sensitivity', ''),
        p_record->>'tool_name',
        tool_context,
        decision_label,
        COALESCE(decision->>'reason', p_record->>'error'),
        COALESCE(p_record->'arguments', '{}'::jsonb),
        jsonb_build_object(
            'authorization_kind', decision->>'authorization_kind',
            'tool_context', tool_context,
            'call_id', p_record->>'call_id',
            'session_id', p_record->>'session_id'
        ),
        jsonb_build_object(
            'output', p_record->'output',
            'error', p_record->>'error',
            'error_type', p_record->>'error_type'
        )
    )
    RETURNING id INTO audit_id;

    RETURN audit_id;
END;
$$;
