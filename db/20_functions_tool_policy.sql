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

    RETURN FOUND;
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
