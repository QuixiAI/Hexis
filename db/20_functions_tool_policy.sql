-- Hexis schema: tool policy helpers.
SET search_path = public, ag_catalog, "$user";
SET check_function_bodies = off;

CREATE OR REPLACE FUNCTION tool_boundary_violation(
    p_tool_name TEXT,
    p_category TEXT
) RETURNS TEXT AS $$
DECLARE
    boundary_text TEXT;
BEGIN
    IF p_tool_name IS NOT NULL AND btrim(p_tool_name) <> '' THEN
        SELECT content INTO boundary_text
        FROM memories
        WHERE type = 'worldview'
          AND metadata->>'category' = 'boundary'
          AND metadata->'restricts_tools' ? p_tool_name
          AND status = 'active'
        LIMIT 1;

        IF boundary_text IS NOT NULL THEN
            RETURN boundary_text;
        END IF;
    END IF;

    IF p_category IS NOT NULL AND btrim(p_category) <> '' THEN
        SELECT content INTO boundary_text
        FROM memories
        WHERE type = 'worldview'
          AND metadata->>'category' = 'boundary'
          AND metadata->'restricts_categories' ? p_category
          AND status = 'active'
        LIMIT 1;

        IF boundary_text IS NOT NULL THEN
            RETURN boundary_text;
        END IF;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION is_tool_approved(p_tool_name TEXT)
RETURNS BOOLEAN AS $$
    SELECT COALESCE(
        (SELECT value ? p_tool_name FROM config WHERE key = 'tools.approvals'),
        FALSE
    );
$$ LANGUAGE sql STABLE;

-- Pre-approving a tool (Settings: "Without asking") changed only config, so
-- the agent kept acting on what it remembered: refused once for lack of
-- approval, later heartbeats recorded "blocked on approval" and never tried
-- again. Granting or revoking an approval now also leaves it an episodic
-- memory of the change.
CREATE OR REPLACE FUNCTION note_tool_approval_change(p_tool_name TEXT, p_granted BOOLEAN)
RETURNS VOID AS $$
BEGIN
    PERFORM create_episodic_memory(
        CASE WHEN p_granted
            THEN format('The user now lets me use the %s tool without asking for approval, in chat and heartbeats. Anything I set aside because %s needed approval can go ahead now.', p_tool_name, p_tool_name)
            ELSE format('The user now requires approval again before I use the %s tool; each use waits for them to approve it.', p_tool_name)
        END,
        jsonb_build_object('kind', 'tool_approval', 'tool', p_tool_name, 'granted', p_granted),
        jsonb_build_object('source', 'user_settings'),
        NULL,
        0.0,
        CURRENT_TIMESTAMP,
        0.8
    );
EXCEPTION WHEN OTHERS THEN
    -- The setting itself must never fail because a memory couldn't be written.
    RAISE WARNING 'could not record tool approval change for %: %', p_tool_name, SQLERRM;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION grant_tool_approval(p_tool_name TEXT)
RETURNS BOOLEAN AS $$
DECLARE
    already BOOLEAN;
BEGIN
    IF p_tool_name IS NULL OR btrim(p_tool_name) = '' THEN
        RAISE EXCEPTION 'tool name is required';
    END IF;

    SELECT value ? p_tool_name INTO already
    FROM config
    WHERE key = 'tools.approvals';

    IF already THEN
        RETURN FALSE;
    END IF;

    INSERT INTO config (key, value, description, updated_at)
    VALUES ('tools.approvals', jsonb_build_array(p_tool_name), 'Approved tools for autonomous use', NOW())
    ON CONFLICT (key) DO UPDATE SET
        value = config.value || jsonb_build_array(p_tool_name),
        updated_at = NOW()
    WHERE NOT config.value ? p_tool_name;

    PERFORM note_tool_approval_change(p_tool_name, TRUE);
    RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION revoke_tool_approval(p_tool_name TEXT)
RETURNS BOOLEAN AS $$
BEGIN
    IF p_tool_name IS NULL OR btrim(p_tool_name) = '' THEN
        RAISE EXCEPTION 'tool name is required';
    END IF;

    UPDATE config
    SET value = value - p_tool_name,
        updated_at = NOW()
    WHERE key = 'tools.approvals'
      AND value ? p_tool_name;

    IF FOUND THEN
        PERFORM note_tool_approval_change(p_tool_name, FALSE);
        RETURN TRUE;
    END IF;
    RETURN FALSE;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION list_tool_approvals()
RETURNS TEXT[] AS $$
    SELECT COALESCE(
        ARRAY(SELECT jsonb_array_elements_text(value) FROM config WHERE key = 'tools.approvals'),
        ARRAY[]::TEXT[]
    );
$$ LANGUAGE sql STABLE;

SET check_function_bodies = on;
