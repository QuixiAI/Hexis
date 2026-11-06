-- 0258: Gmail imports keep only mail worth reading and say when they lack
-- permission, reminders accept a date and time, unbacked reminder claims are
-- caught, and a changed tool approval is remembered.
--
-- - A Google sign-in that granted only the email address was recorded as
--   connected, so every import failed with "insufficient scopes" while the
--   page spun. Such a connection is needs_reauth, which puts Google sign-in
--   back on the Connections page; new imports without mail access fail at
--   once with the reason.
-- - "remind me on November 10 at 9am" arrives as an ISO date-time, which
--   parse_schedule_input rejected; it is accepted now, read in the
--   schedule's timezone when it has no offset.
-- - "Scheduled." / "I'll remind you" with no reminder behind it is caught by
--   the action-claim guardrail.
-- - Pre-approving a tool (Settings: "Without asking") changed only config, so
--   the agent kept acting on an old refusal; granting or revoking an approval
--   now leaves it an episodic memory of the change.
-- Idempotent.
SET search_path = public, ag_catalog;

-- A Google connection without the service's permission needs sign-in again.
UPDATE integration_connections c
SET status = 'needs_reauth', updated_at = CURRENT_TIMESTAMP
WHERE c.status = 'connected'
  AND (
      (c.connector_id = 'gmail' AND NOT EXISTS (
          SELECT 1 FROM unnest(c.granted_scopes) s
          WHERE s LIKE 'https://www.googleapis.com/auth/gmail.%' OR s = 'https://mail.google.com/'))
   OR (c.connector_id = 'google_drive' AND NOT EXISTS (
          SELECT 1 FROM unnest(c.granted_scopes) s
          WHERE s LIKE 'https://www.googleapis.com/auth/drive%'))
  );

-- A reminder can be set for a date and time.
CREATE OR REPLACE FUNCTION parse_schedule_input(
    p_input JSONB
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    schedule_str TEXT := NULLIF(btrim(COALESCE(p_input->>'schedule', '')), '');
    schedule_kind TEXT := NULLIF(lower(btrim(COALESCE(p_input->>'schedule_kind', ''))), '');
    timezone_value TEXT := normalize_timezone(COALESCE(p_input->>'timezone', 'UTC'));
    parts TEXT[];
    schedule JSONB := '{}'::jsonb;
    offset_text TEXT;
    offset_value INT;
    offset_unit TEXT;
    run_at TIMESTAMPTZ;
BEGIN
    IF schedule_str IS NOT NULL THEN
        -- An absolute date-time, the most natural way to say "on November 10
        -- at 9am": ISO 8601 with or without an offset (or after "once:").
        -- Without an offset it is read in the schedule's timezone.
        IF regexp_replace(schedule_str, '^once:', '', 'i') ~ '^\d{4}-\d{2}-\d{2}([T ]\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?)?(Z|[+-]\d{2}(:?\d{2})?)?$' THEN
            schedule_str := regexp_replace(schedule_str, '^once:', '', 'i');
            IF schedule_str ~ '(Z|[+-]\d{2}(:?\d{2})?)$' THEN
                run_at := schedule_str::timestamptz;
            ELSE
                run_at := (schedule_str::timestamp) AT TIME ZONE timezone_value;
            END IF;
            schedule_kind := 'once';
            schedule := jsonb_build_object('run_at', run_at);
        ELSIF db_brain_is_cron_expression(schedule_str) THEN
            schedule_kind := 'cron';
            schedule := jsonb_build_object('cron', schedule_str);
        ELSIF schedule_str LIKE '{%' THEN
            schedule := schedule_str::jsonb;
        ELSIF position(':' in schedule_str) > 0 THEN
            parts := string_to_array(schedule_str, ':');
            CASE lower(parts[1])
                WHEN 'once' THEN
                    offset_text := regexp_replace(COALESCE(parts[2], ''), '^\+', '');
                    IF offset_text !~ '^\d+[hmd]$' THEN
                        RAISE EXCEPTION 'Invalid offset format: %', offset_text;
                    END IF;
                    offset_value := left(offset_text, length(offset_text) - 1)::int;
                    offset_unit := right(offset_text, 1);
                    run_at := CURRENT_TIMESTAMP
                        + CASE offset_unit
                            WHEN 'h' THEN offset_value * INTERVAL '1 hour'
                            WHEN 'm' THEN offset_value * INTERVAL '1 minute'
                            WHEN 'd' THEN offset_value * INTERVAL '1 day'
                          END;
                    schedule_kind := 'once';
                    schedule := jsonb_build_object('run_at', run_at);
                WHEN 'daily' THEN
                    schedule_kind := 'daily';
                    schedule := jsonb_build_object('time', parts[2] || ':' || COALESCE(parts[3], '00'));
                WHEN 'weekly' THEN
                    schedule_kind := 'weekly';
                    schedule := jsonb_build_object('weekday', parts[2], 'time', parts[3] || ':' || COALESCE(parts[4], '00'));
                WHEN 'every' THEN
                    offset_text := COALESCE(parts[2], '');
                    IF offset_text !~ '^\d+[hms]$' THEN
                        RAISE EXCEPTION 'Invalid interval format: %', offset_text;
                    END IF;
                    schedule_kind := 'interval';
                    offset_value := left(offset_text, length(offset_text) - 1)::int;
                    offset_unit := right(offset_text, 1);
                    schedule := CASE offset_unit
                        WHEN 'h' THEN jsonb_build_object('every_hours', offset_value)
                        WHEN 'm' THEN jsonb_build_object('every_minutes', offset_value)
                        ELSE jsonb_build_object('every_seconds', offset_value)
                    END;
                ELSE
                    IF length(schedule_str) <= 5 THEN
                        schedule_kind := COALESCE(schedule_kind, 'daily');
                        schedule := jsonb_build_object('time', schedule_str);
                    ELSE
                        RAISE EXCEPTION 'Could not parse schedule: %', schedule_str;
                    END IF;
            END CASE;
        ELSIF length(schedule_str) <= 5 THEN
            schedule_kind := COALESCE(schedule_kind, 'daily');
            schedule := jsonb_build_object('time', schedule_str);
        ELSE
            RAISE EXCEPTION 'Could not parse schedule: %', schedule_str;
        END IF;
    END IF;

    IF schedule_kind IS NULL THEN
        RAISE EXCEPTION 'schedule_kind is required';
    END IF;

    IF schedule_kind = 'cron' THEN
        IF NULLIF(schedule->>'cron', '') IS NULL THEN
            RAISE EXCEPTION 'cron schedule requires a "cron" expression';
        END IF;
        -- cron_next_fire validates the expression (raises on bad syntax,
        -- out-of-range values, or a never-firing schedule).
        schedule := schedule || jsonb_build_object(
            '_next_run', cron_next_fire(schedule->>'cron', timezone_value, CURRENT_TIMESTAMP)::text);
    END IF;

    IF schedule_kind = 'once' AND schedule ? '_offset' THEN
        offset_text := regexp_replace(schedule->>'_offset', '^\+', '');
        IF offset_text !~ '^\d+[hmd]$' THEN
            RAISE EXCEPTION 'Invalid offset format: %', offset_text;
        END IF;
        offset_value := left(offset_text, length(offset_text) - 1)::int;
        offset_unit := right(offset_text, 1);
        run_at := CURRENT_TIMESTAMP
            + CASE offset_unit
                WHEN 'h' THEN offset_value * INTERVAL '1 hour'
                WHEN 'm' THEN offset_value * INTERVAL '1 minute'
                WHEN 'd' THEN offset_value * INTERVAL '1 day'
              END;
        schedule := (schedule - '_offset') || jsonb_build_object('run_at', run_at);
    END IF;

    RETURN jsonb_build_object(
        'schedule_kind', schedule_kind,
        'schedule', schedule,
        'timezone', timezone_value,
        'next_run_at', compute_next_run_at(schedule_kind, schedule, timezone_value, CURRENT_TIMESTAMP)
    );
END;
$$;


-- "Scheduled. I'll remind you…" with no reminder behind it is caught.
INSERT INTO action_claim_patterns (claim_kind, pattern, satisfied_by_tools, notes)
SELECT 'scheduled', v.pattern,
       ARRAY['schedule_task','update_scheduled_task','manage_schedule','calendar_create','calendar_update','manage_responsibility'],
       v.notes
FROM (VALUES
    ('^\s*(scheduled|reminder (is |has been )?set|all set)\M', 'Bare confirmation: "Scheduled." / "Reminder set."'),
    ('\mI(''|’)ll (remind|ping|nudge) you\M', 'A promise to remind claims a reminder exists'),
    ('\mI(''ve| have) set (a|the|your|that) reminder\M', 'Set a reminder')
) AS v(pattern, notes)
WHERE NOT EXISTS (SELECT 1 FROM action_claim_patterns p WHERE p.pattern = v.pattern);

-- A changed tool approval is remembered.
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

