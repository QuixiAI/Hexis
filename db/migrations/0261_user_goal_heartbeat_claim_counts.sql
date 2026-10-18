-- 0261: A heartbeat on the user's goal is not reclaimed while it may still
-- be running.
--
-- heartbeat_in_progress() counted a claim as live for the longest an ordinary
-- heartbeat may run plus ten minutes, but a heartbeat working on the user's
-- goal (0257) may run rlm.heartbeat.user_goal_timeout_seconds (30 minutes by
-- default), so the timer could start a second heartbeat on top of it. The
-- claim now also counts until that longer run plus ten minutes.
SET search_path = public, ag_catalog;

CREATE OR REPLACE FUNCTION heartbeat_in_progress() RETURNS BOOLEAN
    LANGUAGE sql STABLE AS $$
    SELECT COALESCE((
        SELECT active_heartbeat_id IS NOT NULL
           AND (last_heartbeat_at IS NULL
                OR last_heartbeat_at > CURRENT_TIMESTAMP - make_interval(secs => GREATEST(
                    COALESCE(get_config_int('heartbeat.claim_timeout_s'), 1800),
                    COALESCE(get_config_int('rlm.heartbeat.timeout_seconds'), 300) + 600,
                    COALESCE(get_config_int('rlm.heartbeat.user_goal_timeout_seconds'), 1800) + 600)))
        FROM heartbeat_state WHERE id = 1
    ), FALSE);
$$;
