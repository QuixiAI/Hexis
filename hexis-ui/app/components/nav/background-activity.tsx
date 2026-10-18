"use client";

import { AlertTriangle, Loader2 } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

import type { BackgroundActivity } from "../../api/activity/route";
import { ProgressBar } from "../ui/progress-bar";

/**
 * Background work the user must not be able to miss: a connected service
 * being imported, documents being read into memory, failures. One poller
 * feeds both the sidebar panel and the page-top banner.
 */

const ACTIVE_POLL_MS = 5000;
const IDLE_POLL_MS = 30000;

const ActivityContext = createContext<BackgroundActivity | null>(null);

export function BackgroundActivityProvider({ children }: { children: React.ReactNode }) {
  const [activity, setActivity] = useState<BackgroundActivity | null>(null);
  const activeRef = useRef(false);

  const load = useCallback(async () => {
    if (typeof document !== "undefined" && document.visibilityState !== "visible") return;
    try {
      const response = await fetch("/api/activity", { cache: "no-store" });
      if (!response.ok) return;
      const payload = (await response.json()) as BackgroundActivity;
      activeRef.current = Boolean(payload.active);
      setActivity(payload);
    } catch {
      // The sidebar is not the place to report a flaky connection.
    }
  }, []);

  useEffect(() => {
    let timer: number | undefined;
    let cancelled = false;
    const tick = async () => {
      await load();
      if (cancelled) return;
      timer = window.setTimeout(tick, activeRef.current ? ACTIVE_POLL_MS : IDLE_POLL_MS);
    };
    void tick();
    const wake = () => {
      if (document.visibilityState === "visible") void load();
    };
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("focus", wake);
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", wake);
      window.removeEventListener("focus", wake);
    };
  }, [load]);

  return <ActivityContext.Provider value={activity}>{children}</ActivityContext.Provider>;
}

export function useBackgroundActivity(): BackgroundActivity | null {
  return useContext(ActivityContext);
}

function connectorName(id: string): string {
  const names: Record<string, string> = {
    gmail: "Gmail",
    slack: "Slack",
    discord: "Discord",
    telegram: "Telegram",
    twitter_x: "X",
    google_drive: "Google Drive",
  };
  return names[id] || id;
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

function summarize(activity: BackgroundActivity) {
  const lines: { text: string; tone: "normal" | "error" }[] = [];
  const download = activity.download;
  if (download && ["pending", "in_progress", "paused"].includes(download.status)) {
    const progress =
      download.total > 0
        ? `${download.done} / ${download.total}`
        : download.done > 0
          ? String(download.done)
          : "";
    lines.push({
      tone: "normal",
      text: `Importing from ${connectorName(download.connector_id)}${progress ? ` · ${progress}` : ""}`,
    });
  } else if (download && download.status === "failed") {
    lines.push({ tone: "error", text: `${connectorName(download.connector_id)} import stopped` });
  }
  const reading = activity.reading;
  if (reading && reading.active + reading.pending > 0) {
    lines.push({
      tone: "normal",
      text: `Reading into memory · ${reading.done + reading.failed} / ${reading.total} documents · ${plural(reading.memories, "memory", "memories")}`,
    });
  }
  if (reading && reading.failed > 0) {
    lines.push({
      tone: "error",
      text: `${plural(reading.failed, "document", "documents")} could not be read`,
    });
  }
  return lines;
}

/** The sidebar panel: always present while work runs, quiet when it is done. */
export function ActivityPanel() {
  const activity = useBackgroundActivity();
  if (!activity) return null;
  const lines = summarize(activity);
  const reading = activity.reading;
  // Measured against the server's clock at fetch time, so render stays pure.
  const finishedRecently =
    !activity.active &&
    reading &&
    reading.finished_at &&
    new Date(activity.generated_at).getTime() - new Date(reading.finished_at).getTime() < 60 * 60 * 1000;
  if (!activity.active && !finishedRecently && lines.length === 0) return null;

  return (
    <Link
      href="/connections"
      className="block rounded-md border border-[var(--outline)] bg-[var(--surface-strong)] px-3 py-2 text-xs hover:border-[var(--teal)]"
      aria-live="polite"
    >
      <div className="flex items-center gap-2 font-semibold text-[var(--foreground)]">
        {activity.active ? (
          <Loader2 size={14} className="animate-spin text-[var(--teal)]" aria-hidden="true" />
        ) : null}
        {activity.active ? "Working in the background" : "Background work finished"}
      </div>
      {activity.active ? (
        <ul className="mt-1 space-y-1">
          {lines.map((line) => (
            <li
              key={line.text}
              className={`flex items-start gap-1 ${line.tone === "error" ? "text-[var(--danger,#b42318)]" : "text-[var(--ink-soft)]"}`}
            >
              {line.tone === "error" ? (
                <AlertTriangle size={12} className="mt-0.5 flex-none" aria-hidden="true" />
              ) : null}
              <span>{line.text}</span>
            </li>
          ))}
        </ul>
      ) : reading ? (
        <p className="mt-1 text-[var(--ink-soft)]">
          {plural(reading.done, "document", "documents")} read · {plural(reading.memories, "memory", "memories")}
        </p>
      ) : null}
      {activity.active && reading && reading.total > 0 ? (
        <ProgressBar
          value={reading.done + reading.failed}
          max={reading.total}
          showValue={false}
          color="teal"
          className="mt-2"
        />
      ) : null}
    </Link>
  );
}

/** The page-top strip: shown on every page except Connections while work runs. */
export function ActivityBanner() {
  const pathname = usePathname();
  const activity = useBackgroundActivity();
  if (!activity?.active || pathname.startsWith("/connections")) return null;
  const lines = summarize(activity);
  const hasError = lines.some((line) => line.tone === "error");
  return (
    <Link
      href="/connections"
      role="status"
      className={`flex items-center gap-3 border-b px-4 py-2 text-sm ${
        hasError
          ? "border-[var(--danger,#b42318)] bg-[var(--danger-soft,rgba(180,35,24,0.08))]"
          : "border-[var(--teal)] bg-[var(--surface-strong)]"
      }`}
    >
      {hasError ? (
        <AlertTriangle size={16} className="flex-none text-[var(--danger,#b42318)]" aria-hidden="true" />
      ) : (
        <Loader2 size={16} className="flex-none animate-spin text-[var(--teal)]" aria-hidden="true" />
      )}
      <span className="min-w-0 flex-1 truncate">
        <span className="font-semibold">Working in the background</span>
        {" · "}
        {lines.map((line) => line.text).join(" · ")}
      </span>
      <span className="flex-none text-xs font-medium text-[var(--teal)]">Details</span>
    </Link>
  );
}
