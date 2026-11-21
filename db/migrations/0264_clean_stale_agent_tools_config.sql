-- Cleans stale unregistered tool names out of existing config rows for
-- agent.tools (#108).
--
-- Migration 0245 updated config_defaults.agent.tools, but get_config('agent.tools')
-- returns COALESCE(config.value, config_defaults.value). Any existing database
-- where init_heartbeat_config() or apply_agent_config() wrote agent.tools into
-- config still shadowed config_defaults with the 9 dead tool names:
--   - Removed with no direct replacement:
--     recall_recent, recall_episode, explore_cluster, list_recent_episodes
--   - Superseded by manage_goals:
--     create_goal
--   - Superseded by manage_schedule:
--     schedule_task, list_scheduled_tasks, update_scheduled_task, delete_scheduled_task
--
-- Preserves both string-array and object-array ({"name": ..., "enabled": ...})
-- shapes, keeps custom entries in order, deduplicates upgraded names, and leaves
-- already-clean rows untouched.

SET search_path = public, ag_catalog, "$user";
SET check_function_bodies = off;

UPDATE config
SET value = COALESCE(
        (
            WITH raw_items AS (
                SELECT
                    elem,
                    ord,
                    CASE
                        WHEN jsonb_typeof(elem) = 'string' THEN elem #>> '{}'
                        WHEN jsonb_typeof(elem) = 'object' THEN elem->>'name'
                        ELSE NULL
                    END AS raw_name
                FROM jsonb_array_elements(config.value) WITH ORDINALITY AS t(elem, ord)
            ),
            mapped AS (
                SELECT
                    elem,
                    ord,
                    raw_name,
                    CASE
                        WHEN raw_name = 'create_goal' THEN 'manage_goals'
                        WHEN raw_name IN (
                            'schedule_task',
                            'list_scheduled_tasks',
                            'update_scheduled_task',
                            'delete_scheduled_task'
                        ) THEN 'manage_schedule'
                        ELSE raw_name
                    END AS mapped_name
                FROM raw_items
                WHERE raw_name IS NULL
                   OR raw_name NOT IN (
                        'recall_recent',
                        'recall_episode',
                        'explore_cluster',
                        'list_recent_episodes'
                    )
            ),
            rewritten AS (
                SELECT
                    ord,
                    CASE
                        WHEN mapped_name IS NOT NULL AND mapped_name <> raw_name THEN
                            CASE
                                WHEN jsonb_typeof(elem) = 'string' THEN to_jsonb(mapped_name)
                                WHEN jsonb_typeof(elem) = 'object' THEN jsonb_set(elem, '{name}', to_jsonb(mapped_name), true)
                                ELSE elem
                            END
                        ELSE elem
                    END AS new_elem,
                    ROW_NUMBER() OVER (
                        PARTITION BY COALESCE('name:' || mapped_name, 'ord:' || ord::text)
                        ORDER BY ord
                    ) AS rn
                FROM mapped
            )
            SELECT jsonb_agg(new_elem ORDER BY ord)
            FROM rewritten
            WHERE rn = 1
        ),
        '[]'::jsonb
    ),
    updated_at = CURRENT_TIMESTAMP
WHERE key = 'agent.tools'
  AND jsonb_typeof(value) = 'array'
  AND EXISTS (
      SELECT 1
      FROM jsonb_array_elements(config.value) AS e
      WHERE (
          CASE
              WHEN jsonb_typeof(e) = 'string' THEN e #>> '{}'
              WHEN jsonb_typeof(e) = 'object' THEN e->>'name'
              ELSE NULL
          END
      ) IN (
          'recall_recent',
          'recall_episode',
          'explore_cluster',
          'list_recent_episodes',
          'create_goal',
          'schedule_task',
          'list_scheduled_tasks',
          'update_scheduled_task',
          'delete_scheduled_task'
      )
  );
