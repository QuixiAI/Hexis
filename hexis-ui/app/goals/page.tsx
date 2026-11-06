"use client";

import { useCallback, useEffect, useState } from "react";
import { Ban, Check, MessageSquarePlus, Plus, X } from "lucide-react";
import { Card } from "../components/ui/card";
import { Badge, GoalPriorityBadge } from "../components/ui/badge";
import { PageHeader } from "../components/ui/page-header";
import { Spinner } from "../components/ui/spinner";

type Goal = {
  id: string;
  title: string;
  description: string | null;
  source: string | null;
  priority: string;
  last_touched: string | null;
  progress_count: number;
  is_blocked: boolean;
  created_at: string | null;
};

const PRIORITIES = ["active", "queued", "backburner"] as const;
const SOURCES = ["user_request", "curiosity", "identity", "derived", "external"] as const;

export default function GoalsPage() {
  const [goals, setGoals] = useState<Goal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showNew, setShowNew] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newPriority, setNewPriority] = useState<string>("queued");
  const [newSource, setNewSource] = useState<string>("user_request");
  const [creating, setCreating] = useState(false);
  const [progressGoalId, setProgressGoalId] = useState<string | null>(null);
  const [progressNote, setProgressNote] = useState("");

  const fetchGoals = useCallback(async () => {
    try {
      const res = await fetch("/api/goals", { cache: "no-store" });
      if (!res.ok) throw new Error(`Failed to load goals (${res.status})`);
      const data = await res.json();
      setGoals(data.goals || []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load goals.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchGoals();
  }, [fetchGoals]);

  const createGoal = async () => {
    if (!newTitle.trim() || creating) return;
    setCreating(true);
    try {
      const res = await fetch("/api/goals", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: newTitle.trim(),
          description: newDesc.trim() || null,
          priority: newPriority,
          source: newSource,
        }),
      });
      if (res.ok) {
        setNewTitle("");
        setNewDesc("");
        setShowNew(false);
        fetchGoals();
      }
    } finally {
      setCreating(false);
    }
  };

  const [updateError, setUpdateError] = useState<string | null>(null);

  const patchGoal = async (id: string, body: Record<string, unknown>): Promise<boolean> => {
    setUpdateError(null);
    try {
      const res = await fetch(`/api/goals/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`The goal could not be updated (HTTP ${res.status}).`);
      return true;
    } catch (err: unknown) {
      setUpdateError(err instanceof Error ? err.message : "The goal could not be updated.");
      return false;
    } finally {
      fetchGoals();
    }
  };

  const changePriority = async (id: string, priority: string, reason?: string) => {
    await patchGoal(id, { priority, reason });
  };

  const addProgress = async (id: string) => {
    if (!progressNote.trim()) return;
    if (await patchGoal(id, { progress_note: progressNote.trim() })) {
      setProgressGoalId(null);
      setProgressNote("");
    }
  };

  const grouped = {
    active: goals.filter((g) => g.priority === "active"),
    queued: goals.filter((g) => g.priority === "queued"),
    backburner: goals.filter((g) => g.priority === "backburner"),
  };

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner label="Loading goals..." />
      </div>
    );
  }

  return (
    <div className="app-shell">
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
        <div className="flex items-center justify-between gap-4 border-b border-[var(--outline)] pb-5">
          <PageHeader
            title="Goals"
            subtitle={`${goals.length} goals across ${Object.values(grouped).filter((g) => g.length > 0).length} priorities`}
          />
          <button
            onClick={() => setShowNew(!showNew)}
            className="flex items-center gap-2 rounded-lg bg-[var(--foreground)] px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-[var(--teal)]"
          >
            {showNew ? <X size={16} /> : <Plus size={16} />}
            {showNew ? "Cancel" : "New goal"}
          </button>
        </div>

        {/* New goal form */}
        {showNew && (
          <Card className="mt-6 fade-up max-w-3xl">
            <h3 className="text-base font-semibold">Create goal</h3>
            <div className="mt-4 space-y-3">
              <input
                type="text"
                className="w-full rounded-md border border-[var(--outline)] bg-white px-3 py-2.5 text-sm focus:border-[var(--teal)] focus:outline-none"
                placeholder="Goal title"
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
              />
              <textarea
                className="w-full rounded-md border border-[var(--outline)] bg-white px-3 py-2.5 text-sm focus:border-[var(--teal)] focus:outline-none"
                placeholder="Description"
                rows={2}
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
              />
              <div className="flex flex-col gap-3 sm:flex-row">
                <select
                  value={newPriority}
                  onChange={(e) => setNewPriority(e.target.value)}
                  className="rounded-md border border-[var(--outline)] bg-white px-3 py-2.5 text-sm"
                >
                  {PRIORITIES.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
                <select
                  value={newSource}
                  onChange={(e) => setNewSource(e.target.value)}
                  className="rounded-md border border-[var(--outline)] bg-white px-3 py-2.5 text-sm"
                >
                  {SOURCES.map((s) => (
                    <option key={s} value={s}>
                      {s.replace("_", " ")}
                    </option>
                  ))}
                </select>
                <button
                  onClick={createGoal}
                  disabled={creating || !newTitle.trim()}
                  className="rounded-md bg-[var(--foreground)] px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-[var(--teal)] disabled:opacity-50"
                >
                  {creating ? "Creating..." : "Create"}
                </button>
              </div>
            </div>
          </Card>
        )}

        {updateError && (
          <p role="alert" className="mt-6 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {updateError}
          </p>
        )}

        {/* Backend outage banner (distinct from "no goals") */}
        {error && (
          <Card className="mt-6 border-red-200 bg-red-50 fade-up">
            <div className="flex items-center justify-between gap-4">
              <p className="text-sm text-red-700">{error}</p>
              <button
                onClick={fetchGoals}
                className="rounded-full border border-red-300 px-4 py-2 text-sm font-medium text-red-700 transition hover:bg-red-100"
              >
                Retry
              </button>
            </div>
          </Card>
        )}

        {/* Three-column layout */}
        <div className="mt-6 grid gap-5 lg:grid-cols-3">
          {PRIORITIES.map((priority) => (
            <div key={priority}>
              <div className="mb-3 flex items-center gap-2 px-1">
                <GoalPriorityBadge priority={priority} />
                <span className="text-xs text-[var(--ink-soft)]">
                  {grouped[priority].length}
                </span>
              </div>
              <div className="space-y-2">
                {grouped[priority].length === 0 ? (
                  <div className="rounded-lg border border-dashed border-[var(--outline)] px-4 py-8 text-center text-sm text-[var(--ink-soft)]">No {priority} goals.</div>
                ) : (
                  grouped[priority].map((g) => (
                    <Card key={g.id} className="!p-4">
                      <p className="text-sm font-medium">{g.title}</p>
                      {g.description && (
                        <p className="mt-1 text-xs text-[var(--ink-soft)] line-clamp-2">
                          {g.description}
                        </p>
                      )}
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {g.source && (
                          <Badge variant="muted">{g.source.replace("_", " ")}</Badge>
                        )}
                        {g.progress_count > 0 && (
                          <Badge variant="teal">{g.progress_count} updates</Badge>
                        )}
                        {g.is_blocked && <Badge variant="warning">blocked</Badge>}
                      </div>

                      <div className="mt-4 flex items-center gap-2 border-t border-[var(--outline)] pt-3">
                        <select
                          aria-label={`Priority for ${g.title}`}
                          value={priority}
                          onChange={(event) => changePriority(g.id, event.target.value)}
                          className="min-w-0 flex-1 rounded-md border border-[var(--outline)] bg-white px-2 py-1.5 text-xs capitalize"
                        >
                          {PRIORITIES.map((value) => <option key={value} value={value}>{value}</option>)}
                        </select>
                        <button
                          type="button"
                          onClick={() => changePriority(g.id, "completed", "Marked complete via UI")}
                          title="Mark complete"
                          aria-label={`Complete ${g.title}`}
                          className="flex h-8 w-8 items-center justify-center rounded-md border border-emerald-200 text-emerald-700 hover:bg-emerald-50"
                        >
                          <Check size={15} />
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            // Abandoned is not completed: the goal is archived with
                            // its reason, kept in history, and no longer pursued.
                            const reason = window.prompt(`Why abandon "${g.title}"? (optional)`);
                            if (reason === null) return;
                            void changePriority(g.id, "abandoned", reason.trim() || "Abandoned via UI");
                          }}
                          title="Abandon goal"
                          aria-label={`Abandon ${g.title}`}
                          className="flex h-8 w-8 items-center justify-center rounded-md border border-[var(--outline)] text-[var(--ink-soft)] hover:bg-[var(--surface-strong)]"
                        >
                          <Ban size={15} />
                        </button>
                        <button
                          type="button"
                          onClick={() =>
                            setProgressGoalId(progressGoalId === g.id ? null : g.id)
                          }
                          title="Add progress"
                          aria-label={`Add progress to ${g.title}`}
                          className="flex h-8 w-8 items-center justify-center rounded-md border border-[var(--outline)] hover:bg-[var(--surface-strong)]"
                        >
                          <MessageSquarePlus size={15} />
                        </button>
                      </div>

                      {/* Progress note input */}
                      {progressGoalId === g.id && (
                        <div className="mt-2 flex gap-2 fade-up">
                          <input
                            type="text"
                            className="flex-1 rounded-lg border border-[var(--outline)] bg-white px-3 py-1.5 text-xs focus:border-[var(--accent)] focus:outline-none"
                            placeholder="Progress note"
                            value={progressNote}
                            onChange={(e) => setProgressNote(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") addProgress(g.id);
                            }}
                          />
                          <button
                            onClick={() => addProgress(g.id)}
                            className="rounded-lg bg-[var(--teal)] px-3 py-1.5 text-xs text-white"
                          >
                            Save
                          </button>
                        </div>
                      )}

                      {g.last_touched && (
                        <p className="mt-2 text-[10px] text-[var(--ink-soft)]">
                          Last: {new Date(g.last_touched).toLocaleDateString()}
                        </p>
                      )}
                    </Card>
                  ))
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
