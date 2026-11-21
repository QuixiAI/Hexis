-- Infrastructure failures remain available until a successful reply in the
-- same conversation. This is causal continuity, with no age-based expiry.
CREATE OR REPLACE FUNCTION render_chat_runtime_context(p_session_id TEXT)
RETURNS TEXT LANGUAGE sql STABLE AS $$
    SELECT CASE WHEN COUNT(*) = 0 THEN '' ELSE
        E'## Interrupted Responses\n' || COUNT(*)::text ||
        ' previous response attempt(s) in this conversation failed after the last successful reply. ' ||
        'These were infrastructure failures, not user actions. A failed attempt may have shown partial text; ' ||
        'do not assume it completed or that any attempted action succeeded. ' ||
        'Briefly acknowledge the interruption in conversation, then continue the user request. ' ||
        'Do not invent a technical cause or interpret this interruption as the user ignoring or rejecting you.'
    END
    FROM agent_turns t
    WHERE t.session_id::text = p_session_id
      AND t.mode = 'chat'
      AND (t.status = 'failed' OR t.stopped_reason = 'timeout')
      AND t.created_at > COALESCE((
          SELECT MAX(ok.created_at) FROM agent_turns ok
          WHERE ok.session_id = t.session_id AND ok.mode = 'chat'
            AND ok.status = 'completed'
            AND COALESCE(ok.stopped_reason, '') <> 'timeout'
      ), '-infinity'::timestamptz);
$$;

INSERT INTO config (key, value, description)
VALUES ('llm.fallbacks', '[]'::jsonb,
        'Ordered explicitly configured inference fallback routes.')
ON CONFLICT (key) DO NOTHING;
