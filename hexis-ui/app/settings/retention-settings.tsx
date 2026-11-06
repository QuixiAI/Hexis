"use client";

import { useEffect, useState } from "react";
import type { RetentionSettings as Settings } from "../api/settings/retention/route";

const LIMITS = [
  {
    key: "turn_trace_days",
    title: "Turn traces",
    hint: "The full input and output of each reply: the prompt, the memories it was given, and tool calls with their results. Used for debugging and audits.",
  },
  {
    key: "conversation_days",
    title: "Conversations",
    hint: "Web chat and channel transcripts (Slack, Telegram, Discord, …), counted from a conversation's last message, with each message's copy on the Documents page.",
  },
  {
    key: "raw_turn_days",
    title: "Raw turns",
    hint: "The verbatim turns the memory system works from, once it has finished with them. Memories formed from them stay, but can no longer show the exact words.",
  },
] as const;
type Limit = (typeof LIMITS)[number]["key"];

/** How long raw transcripts are kept. Memories fade on their own rules. */
export function RetentionSettings() {
  const [saved, setSaved] = useState<Settings | null>(null);
  const [draft, setDraft] = useState<Settings | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/settings/retention", { cache: "no-store" })
      .then(async (res) => {
        const payload = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(payload.error || `HTTP ${res.status}`);
        setSaved(payload);
        setDraft(payload);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  if (!draft || !saved) return error ? <p role="alert" className="text-sm text-red-700">{error}</p> : null;

  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  const setDays = (key: Limit, value: number) => setDraft({ ...draft, [key]: value });

  const save = async () => {
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const res = await fetch("/api/settings/retention", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled: draft.enabled,
          turn_trace_days: draft.turn_trace_days,
          conversation_days: draft.conversation_days,
          raw_turn_days: draft.raw_turn_days,
        }),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(payload.error || `HTTP ${res.status}`);
      setSaved(payload);
      setDraft(payload);
      setNotice("Saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const lastRun = saved.last_run ? new Date(saved.last_run) : null;

  return (
    <div className="space-y-5">
      <div className="rounded-lg border border-[var(--outline)] bg-white p-5">
        <h2 className="text-sm font-semibold">Data retention</h2>
        <p className="mt-1 text-xs leading-relaxed text-[var(--ink-soft)]">
          How long the raw record of conversations is kept. Memories are not affected: they fade on their own rules, and important ones are kept.
        </p>
        <label className="mt-4 flex items-start gap-3 text-sm">
          <input type="checkbox" className="mt-1" checked={draft.enabled}
            onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} />
          <span>
            <span className="block font-medium">Delete raw transcripts after these limits</span>
            <span className="block text-xs text-[var(--ink-soft)]">Checked once a day. Turn this off to keep everything.</span>
          </span>
        </label>

        <div className={`mt-4 space-y-3 ${draft.enabled ? "" : "opacity-50"}`}>
          {LIMITS.map(({ key, title, hint }) => {
            const forever = draft[key] === 0;
            return (
              <div key={key} className="rounded-lg border border-[var(--outline)] px-3 py-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <label htmlFor={`retention-${key}`} className="text-sm font-medium">{title}</label>
                  <div className="flex items-center gap-2 text-sm">
                    <input id={`retention-${key}`} type="number" min={1} max={36500}
                      disabled={!draft.enabled || forever}
                      value={forever ? "" : draft[key]}
                      onChange={(e) => setDays(key, Math.max(1, Math.floor(Number(e.target.value) || 1)))}
                      className="w-24 rounded-lg border border-[var(--outline)] bg-white px-2 py-1 text-right tabular-nums disabled:bg-[var(--surface-strong)]" />
                    <span className="text-xs text-[var(--ink-soft)]">days</span>
                    <label className="ml-2 flex items-center gap-1.5 text-xs">
                      <input type="checkbox" disabled={!draft.enabled} checked={forever}
                        onChange={(e) => setDays(key, e.target.checked ? 0 : saved[key] || 365)} />
                      Keep forever
                    </label>
                  </div>
                </div>
                <p className="mt-1.5 text-xs leading-relaxed text-[var(--ink-soft)]">{hint}</p>
              </div>
            );
          })}
        </div>

        <p className="mt-4 text-xs text-[var(--ink-soft)]">Never deleted here: memories, ingested files, and imported history.</p>
        <p className="mt-1 text-xs text-[var(--ink-soft)]">
          {lastRun
            ? `Last checked ${lastRun.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}.`
            : "Not checked yet; the first check runs within a day."}
        </p>
        <div className="mt-4 flex items-center gap-3">
          <button type="button" onClick={() => void save()} disabled={!dirty || saving}
            className="rounded-full bg-[var(--foreground)] px-5 py-2 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)] disabled:opacity-50">
            {saving ? "Saving…" : "Save"}
          </button>
          {notice ? <span role="status" className="text-xs text-[var(--ink-soft)]">{notice}</span> : null}
        </div>
      </div>
      {error ? <p role="alert" className="text-sm text-red-700">{error}</p> : null}
    </div>
  );
}
