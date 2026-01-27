-- Hexis schema: scheduling functions.
SET search_path = public, ag_catalog, "$user";
SET check_function_bodies = off;

CREATE OR REPLACE FUNCTION normalize_timezone(p_timezone TEXT)
RETURNS TEXT AS $$
DECLARE
    tz TEXT;
BEGIN
    tz := NULLIF(btrim(p_timezone), '');
    IF tz IS NULL THEN
        RETURN 'UTC';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_timezone_names WHERE name = tz) THEN
        RETURN tz;
    END IF;
    RAISE EXCEPTION 'Unknown timezone: %', tz;
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION normalize_weekday(p_weekday TEXT)
RETURNS INT AS $$
DECLARE
    wd TEXT;
    wd_int INT;
BEGIN
    wd := NULLIF(lower(btrim(p_weekday)), '');
    IF wd IS NULL THEN
        RAISE EXCEPTION 'Weekday is required';
    END IF;

    BEGIN
        wd_int := wd::int;
        IF wd_int = 0 THEN
            wd_int := 7;
        END IF;
        IF wd_int < 1 OR wd_int > 7 THEN
            RAISE EXCEPTION 'Weekday out of range: %', wd_int;
        END IF;
        RETURN wd_int;
    EXCEPTION
        WHEN invalid_text_representation THEN
            NULL;
    END;

    IF wd IN ('mon', 'monday') THEN RETURN 1; END IF;
    IF wd IN ('tue', 'tues', 'tuesday') THEN RETURN 2; END IF;
    IF wd IN ('wed', 'weds', 'wednesday') THEN RETURN 3; END IF;
    IF wd IN ('thu', 'thur', 'thurs', 'thursday') THEN RETURN 4; END IF;
    IF wd IN ('fri', 'friday') THEN RETURN 5; END IF;
    IF wd IN ('sat', 'saturday') THEN RETURN 6; END IF;
    IF wd IN ('sun', 'sunday') THEN RETURN 7; END IF;

    RAISE EXCEPTION 'Invalid weekday: %', p_weekday;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION parse_time_of_day(p_time TEXT)
RETURNS TIME AS $$
DECLARE
    t TIME;
BEGIN
    t := NULLIF(btrim(p_time), '')::time;
    IF t IS NULL THEN
        RAISE EXCEPTION 'Time of day is required';
    END IF;
    RETURN t;
EXCEPTION
    WHEN OTHERS THEN
        RAISE EXCEPTION 'Invalid time of day: %', p_time;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION compute_next_run_at(
    p_schedule_kind TEXT,
    p_schedule JSONB,
    p_timezone TEXT DEFAULT 'UTC',
    p_after TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
)
RETURNS TIMESTAMPTZ AS $$
DECLARE
    schedule_kind TEXT;
    tz TEXT;
    after_ts TIMESTAMPTZ;
    after_local TIMESTAMP;
    run_at TIMESTAMPTZ;
    target_time TIME;
    weekday INT;
    days_ahead INT;
    interval_seconds INT;
    anchor_ts TIMESTAMPTZ;
    elapsed_seconds DOUBLE PRECISION;
    next_ts TIMESTAMPTZ;
BEGIN
    schedule_kind := NULLIF(lower(btrim(p_schedule_kind)), '');
    IF schedule_kind IS NULL THEN
        RAISE EXCEPTION 'schedule_kind is required';
    END IF;

    tz := normalize_timezone(p_timezone);
    after_ts := COALESCE(p_after, CURRENT_TIMESTAMP);

    CASE schedule_kind
        WHEN 'once' THEN
            run_at := NULLIF(p_schedule->>'run_at', '')::timestamptz;
            IF run_at IS NULL THEN
                RAISE EXCEPTION 'schedule.run_at is required for once schedules';
            END IF;
            IF run_at <= after_ts THEN
                RETURN NULL;
            END IF;
            RETURN run_at;
        WHEN 'interval' THEN
            interval_seconds := COALESCE(
                NULLIF((p_schedule->>'every_seconds')::int, 0),
                NULLIF((p_schedule->>'every_minutes')::int, 0) * 60,
                NULLIF((p_schedule->>'every_hours')::int, 0) * 3600
            );
            IF interval_seconds IS NULL OR interval_seconds <= 0 THEN
                RAISE EXCEPTION 'schedule.every_seconds/every_minutes/every_hours required for interval schedules';
            END IF;
            anchor_ts := NULLIF(p_schedule->>'anchor_at', '')::timestamptz;
            IF anchor_ts IS NULL THEN
                anchor_ts := after_ts;
            END IF;
            IF after_ts < anchor_ts THEN
                RETURN anchor_ts;
            END IF;
            elapsed_seconds := EXTRACT(EPOCH FROM (after_ts - anchor_ts));
            next_ts := anchor_ts + ((floor(elapsed_seconds / interval_seconds) + 1) * interval_seconds) * INTERVAL '1 second';
            RETURN next_ts;
        WHEN 'daily' THEN
            target_time := parse_time_of_day(p_schedule->>'time');
            after_local := after_ts AT TIME ZONE tz;
            run_at := (date_trunc('day', after_local) + target_time) AT TIME ZONE tz;
            IF run_at <= after_ts THEN
                run_at := ((date_trunc('day', after_local) + target_time) + INTERVAL '1 day') AT TIME ZONE tz;
            END IF;
            RETURN run_at;
        WHEN 'weekly' THEN
            target_time := parse_time_of_day(p_schedule->>'time');
            weekday := normalize_weekday(p_schedule->>'weekday');
            after_local := after_ts AT TIME ZONE tz;
            days_ahead := (weekday - EXTRACT(ISODOW FROM after_local)::int + 7) % 7;
            run_at := (date_trunc('day', after_local) + (days_ahead || ' days')::interval + target_time) AT TIME ZONE tz;
            IF run_at <= after_ts THEN
                run_at := run_at + INTERVAL '7 days';
            END IF;
            RETURN run_at;
        ELSE
            RAISE EXCEPTION 'Unsupported schedule_kind: %', schedule_kind;
    END CASE;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION create_scheduled_task(
    p_name TEXT,
    p_schedule_kind TEXT,
    p_schedule JSONB,
    p_action_kind TEXT,
    p_action_payload JSONB DEFAULT '{}'::jsonb,
    p_timezone TEXT DEFAULT 'UTC',
    p_description TEXT DEFAULT NULL,
    p_status TEXT DEFAULT 'active',
    p_max_runs INT DEFAULT NULL,
    p_created_by TEXT DEFAULT 'agent'
)
RETURNS UUID AS $$
DECLARE
    task_id UUID;
    next_run TIMESTAMPTZ;
    status_value TEXT;
    action_kind TEXT;
BEGIN
    IF p_name IS NULL OR btrim(p_name) = '' THEN
        RAISE EXCEPTION 'Scheduled task name is required';
    END IF;
    status_value := COALESCE(NULLIF(p_status, ''), 'active');
    IF status_value NOT IN ('active', 'paused', 'disabled') THEN
        RAISE EXCEPTION 'Invalid status: %', status_value;
    END IF;
    action_kind := COALESCE(NULLIF(p_action_kind, ''), '');
    IF action_kind NOT IN ('queue_user_message', 'create_goal') THEN
        RAISE EXCEPTION 'Invalid action_kind: %', action_kind;
    END IF;
    IF action_kind = 'queue_user_message' THEN
        IF p_action_payload IS NULL OR NULLIF(p_action_payload->>'message', '') IS NULL THEN
            RAISE EXCEPTION 'queue_user_message requires action_payload.message';
        END IF;
    ELSIF action_kind = 'create_goal' THEN
        IF p_action_payload IS NULL OR NULLIF(p_action_payload->>'title', '') IS NULL THEN
            RAISE EXCEPTION 'create_goal requires action_payload.title';
        END IF;
    END IF;

    next_run := compute_next_run_at(p_schedule_kind, p_schedule, p_timezone, CURRENT_TIMESTAMP);
    IF next_run IS NULL THEN
        RAISE EXCEPTION 'Schedule does not produce a future run';
    END IF;

    INSERT INTO scheduled_tasks (
        name,
        description,
        schedule_kind,
        schedule,
        timezone,
        action_kind,
        action_payload,
        status,
        next_run_at,
        max_runs,
        created_by,
        created_at,
        updated_at
    ) VALUES (
        p_name,
        p_description,
        lower(btrim(p_schedule_kind)),
        COALESCE(p_schedule, '{}'::jsonb),
        normalize_timezone(p_timezone),
        action_kind,
        COALESCE(p_action_payload, '{}'::jsonb),
        status_value,
        next_run,
        p_max_runs,
        NULLIF(p_created_by, ''),
        CURRENT_TIMESTAMP,
        CURRENT_TIMESTAMP
    )
    RETURNING id INTO task_id;

    RETURN task_id;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION set_scheduled_task_status(
    p_task_id UUID,
    p_status TEXT,
    p_reason TEXT DEFAULT NULL
)
RETURNS BOOLEAN AS $$
DECLARE
    status_value TEXT;
BEGIN
    status_value := COALESCE(NULLIF(p_status, ''), 'active');
    IF status_value NOT IN ('active', 'paused', 'disabled') THEN
        RAISE EXCEPTION 'Invalid status: %', status_value;
    END IF;

    UPDATE scheduled_tasks
    SET status = status_value,
        last_error = CASE WHEN p_reason IS NULL OR btrim(p_reason) = '' THEN last_error ELSE p_reason END,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_task_id;

    RETURN FOUND;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION run_scheduled_tasks(p_limit INT DEFAULT 25)
RETURNS JSONB AS $$
DECLARE
    task RECORD;
    now_ts TIMESTAMPTZ := CURRENT_TIMESTAMP;
    outbox_messages JSONB := '[]'::jsonb;
    ran_count INT := 0;
    next_run TIMESTAMPTZ;
    action_payload JSONB;
    goal_id UUID;
    task_status TEXT;
BEGIN
    FOR task IN
        SELECT *
        FROM scheduled_tasks
        WHERE status = 'active'
          AND next_run_at <= now_ts
        ORDER BY next_run_at ASC
        LIMIT p_limit
        FOR UPDATE SKIP LOCKED
    LOOP
        BEGIN
            action_payload := COALESCE(task.action_payload, '{}'::jsonb);
            IF task.action_kind = 'queue_user_message' THEN
                outbox_messages := outbox_messages || jsonb_build_array(
                    build_user_message(
                        NULLIF(action_payload->>'message', ''),
                        NULLIF(action_payload->>'intent', ''),
                        action_payload->'context'
                    )
                );
            ELSIF task.action_kind = 'create_goal' THEN
                goal_id := create_goal(
                    NULLIF(action_payload->>'title', ''),
                    NULLIF(action_payload->>'description', ''),
                    COALESCE(NULLIF(action_payload->>'source', ''), 'user_request')::goal_source,
                    COALESCE(NULLIF(action_payload->>'priority', ''), 'queued')::goal_priority,
                    NULLIF(action_payload->>'parent_id', '')::uuid,
                    COALESCE(NULLIF(action_payload->>'due_at', '')::timestamptz, task.next_run_at)
                );
                IF COALESCE((action_payload->>'notify')::boolean, false) THEN
                    outbox_messages := outbox_messages || jsonb_build_array(
                        build_user_message(
                            format('Created goal: %s', COALESCE(action_payload->>'title', goal_id::text)),
                            'goal_created',
                            jsonb_build_object('goal_id', goal_id::text, 'task_id', task.id::text)
                        )
                    );
                END IF;
            END IF;

            ran_count := ran_count + 1;

            next_run := compute_next_run_at(task.schedule_kind, task.schedule, task.timezone, now_ts);
            IF task.max_runs IS NOT NULL AND (task.run_count + 1) >= task.max_runs THEN
                task_status := 'disabled';
            ELSIF next_run IS NULL THEN
                task_status := 'disabled';
            ELSE
                task_status := task.status;
            END IF;

            UPDATE scheduled_tasks
            SET last_run_at = now_ts,
                run_count = run_count + 1,
                next_run_at = COALESCE(next_run, next_run_at),
                status = task_status,
                last_error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = task.id;
        EXCEPTION
            WHEN OTHERS THEN
                UPDATE scheduled_tasks
                SET last_error = SQLERRM,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = task.id;
        END;
    END LOOP;

    RETURN jsonb_build_object(
        'ran', ran_count,
        'outbox_messages', outbox_messages
    );
END;
$$ LANGUAGE plpgsql;

SET check_function_bodies = on;
