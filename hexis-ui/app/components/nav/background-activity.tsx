"use client";

import { AlertTriangle, Loader2 } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import type { BackgroundActivity } from "../../api/activity/route";
import { ProgressBar } from "../ui/progress-bar";

/**
 * Background work the user must not be able to miss: a connected service
 * being imported, documents being read into memory, failures, and the file
 * upload queue, which lives here so uploads keep going when the user leaves
 * the Ingest page. One poller feeds both the sidebar panel and the page-top
 * banner.
 */

const ACTIVE_POLL_MS = 5000;
const IDLE_POLL_MS = 30000;

export type IngestMode = "fast" | "slow" | "hybrid";

export type QueuedUploadItem = {
  id: string;
  file: File;
  name: string;
  size: number;
  mode: IngestMode;
  /** "private" keeps the memories out of group-channel recall and exports. */
  sensitivity: "private" | null;
  state: "queued" | "uploading" | "accepted" | "failed";
  jobId?: string;
  detail?: string;
};

export type BackgroundUploadController = {
  uploads: QueuedUploadItem[];
  uploading: boolean;
  enqueueFiles: (
    files: File[],
    options?: { mode?: IngestMode; sensitivity?: "private" | null; autoStart?: boolean },
  ) => QueuedUploadItem[];
  /** Uploads everything queued, one file at a time; resolves to the accepted count. */
  startQueuedUploads: (modeOverride?: IngestMode) => Promise<number>;
  toggleSensitivity: (id: string) => void;
  removeUpload: (id: string) => void;
  clearAccepted: () => void;
  refreshActivity: () => Promise<void>;
};

const ActivityContext = createContext<BackgroundActivity | null>(null);
const UploadQueueContext = createContext<BackgroundUploadController | null>(null);

function inFlight(item: QueuedUploadItem): boolean {
  return item.state === "queued" || item.state === "uploading";
}

export function BackgroundActivityProvider({ children }: { children: React.ReactNode }) {
  const [activity, setActivity] = useState<BackgroundActivity | null>(null);
  const [uploads, setUploads] = useState<QueuedUploadItem[]>([]);
  const [uploading, setUploading] = useState(false);
  const activeRef = useRef(false);
  // The queue's source of truth: every change goes through updateUploads so
  // the upload loop and new drops never overwrite each other's changes.
  const uploadsRef = useRef<QueuedUploadItem[]>([]);
  const uploadingLockRef = useRef(false);

  const updateUploads = useCallback((change: (prev: QueuedUploadItem[]) => QueuedUploadItem[]) => {
    uploadsRef.current = change(uploadsRef.current);
    setUploads(uploadsRef.current);
  }, []);

  const hasUnsentUploads = uploads.some(inFlight);
  useEffect(() => {
    if (!hasUnsentUploads) return;
    // Uploads run in this tab; closing or reloading it drops the rest.
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [hasUnsentUploads]);

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
      const uploadsInFlight = uploadsRef.current.some(inFlight);
      timer = window.setTimeout(
        tick,
        activeRef.current || uploadsInFlight ? ACTIVE_POLL_MS : IDLE_POLL_MS,
      );
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

  const startQueuedUploads = useCallback(
    async (modeOverride?: IngestMode): Promise<number> => {
      if (uploadingLockRef.current) return 0;
      if (!uploadsRef.current.some((u) => u.state === "queued")) return 0;

      uploadingLockRef.current = true;
      setUploading(true);
      let acceptedCount = 0;
      try {
        while (true) {
          const nextItem = uploadsRef.current.find((u) => u.state === "queued");
          if (!nextItem) break;
          const effectiveMode = modeOverride || nextItem.mode;
          updateUploads((prev) =>
            prev.map((p) =>
              p.id === nextItem.id ? { ...p, state: "uploading", mode: effectiveMode } : p,
            ),
          );
          try {
            const form = new FormData();
            form.append("file", nextItem.file, nextItem.name);
            form.append("mode", effectiveMode);
            if (nextItem.sensitivity) form.append("sensitivity", nextItem.sensitivity);
            const res = await fetch("/api/ingest/file", { method: "POST", body: form });
            if (res.ok) {
              const body = await res.json().catch(() => ({}));
              acceptedCount += 1;
              updateUploads((prev) =>
                prev.map((p) =>
                  p.id === nextItem.id
                    ? { ...p, state: "accepted", jobId: body?.job_id ? String(body.job_id) : undefined }
                    : p,
                ),
              );
              activeRef.current = true;
              void load();
            } else {
              let detail = `${res.status}`;
              try {
                const body = await res.json();
                detail = body?.detail || body?.error || detail;
              } catch {
                // non-JSON error body
              }
              updateUploads((prev) =>
                prev.map((p) => (p.id === nextItem.id ? { ...p, state: "failed", detail } : p)),
              );
            }
          } catch (err) {
            updateUploads((prev) =>
              prev.map((p) =>
                p.id === nextItem.id
                  ? { ...p, state: "failed", detail: err instanceof Error ? err.message : "network error" }
                  : p,
              ),
            );
          }
        }
      } finally {
        uploadingLockRef.current = false;
        setUploading(false);
        void load();
      }
      return acceptedCount;
    },
    [load, updateUploads],
  );

  const enqueueFiles = useCallback(
    (
      files: File[],
      options?: { mode?: IngestMode; sensitivity?: "private" | null; autoStart?: boolean },
    ): QueuedUploadItem[] => {
      const valid = files.filter((file) => file.size > 0);
      if (valid.length === 0) return [];
      const mode = options?.mode ?? "fast";
      const sensitivity = options?.sensitivity ?? null;
      const newItems: QueuedUploadItem[] = valid.map((file) => ({
        id: crypto.randomUUID(),
        file,
        name: file.name,
        size: file.size,
        mode,
        sensitivity,
        state: "queued" as const,
      }));
      updateUploads((prev) => {
        // A new batch starts the count over; accepted ones from earlier batches go.
        const busy = prev.some(inFlight);
        return [...(busy ? prev : prev.filter((u) => u.state !== "accepted")), ...newItems];
      });
      if (options?.autoStart) void startQueuedUploads(mode);
      return newItems;
    },
    [startQueuedUploads, updateUploads],
  );

  const toggleSensitivity = useCallback(
    (id: string) =>
      updateUploads((prev) =>
        prev.map((item) =>
          item.id === id
            ? { ...item, sensitivity: item.sensitivity === "private" ? null : "private" }
            : item,
        ),
      ),
    [updateUploads],
  );

  const removeUpload = useCallback(
    (id: string) => updateUploads((prev) => prev.filter((item) => item.id !== id)),
    [updateUploads],
  );

  const clearAccepted = useCallback(
    () => updateUploads((prev) => prev.filter((item) => item.state !== "accepted")),
    [updateUploads],
  );

  // Stable unless the queue changes, so pages that depend on it don't re-run
  // their effects on every activity poll.
  const controller = useMemo<BackgroundUploadController>(
    () => ({
      uploads,
      uploading,
      enqueueFiles,
      startQueuedUploads,
      toggleSensitivity,
      removeUpload,
      clearAccepted,
      refreshActivity: load,
    }),
    [uploads, uploading, enqueueFiles, startQueuedUploads, toggleSensitivity, removeUpload, clearAccepted, load],
  );

  return (
    <ActivityContext.Provider value={activity}>
      <UploadQueueContext.Provider value={controller}>{children}</UploadQueueContext.Provider>
    </ActivityContext.Provider>
  );
}

export function useBackgroundActivity(): BackgroundActivity | null {
  return useContext(ActivityContext);
}

/** The app-wide upload queue; null outside the provider (tests, isolated pages). */
export function useBackgroundUploadQueue(): BackgroundUploadController | null {
  return useContext(UploadQueueContext);
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

function summarize(activity: BackgroundActivity | null, uploads: QueuedUploadItem[]) {
  const lines: { text: string; tone: "normal" | "error" }[] = [];
  if (uploads.some(inFlight)) {
    const accepted = uploads.filter((u) => u.state === "accepted").length;
    lines.push({ tone: "normal", text: `Uploading · ${accepted} / ${uploads.length} files` });
  }
  const failedUploads = uploads.filter((u) => u.state === "failed").length;
  if (failedUploads > 0) {
    lines.push({ tone: "error", text: `${plural(failedUploads, "file", "files")} could not be uploaded` });
  }
  if (!activity) return lines;
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

/** Where the panel and banner lead: the Ingest page while uploads or reading
 * run on their own, Connections once an import is involved. */
function activityHref(activity: BackgroundActivity | null, uploads: QueuedUploadItem[]): string {
  const uploadingOrReading =
    uploads.some(inFlight) ||
    Boolean(activity?.reading && activity.reading.active + activity.reading.pending > 0);
  const importing = Boolean(
    activity?.download && ["pending", "in_progress", "paused"].includes(activity.download.status),
  );
  return uploadingOrReading && !importing ? "/ingest" : "/connections";
}

/** The sidebar panel: always present while work runs, quiet when it is done. */
export function ActivityPanel() {
  const activity = useBackgroundActivity();
  const uploads = useBackgroundUploadQueue()?.uploads ?? [];
  const uploadsInFlight = uploads.some(inFlight);
  if (!activity && !uploadsInFlight) return null;
  const lines = summarize(activity, uploads);
  const reading = activity?.reading ?? null;
  const isActive = Boolean(activity?.active) || uploadsInFlight;
  // Measured against the server's clock at fetch time, so render stays pure.
  const finishedRecently =
    !isActive &&
    activity &&
    reading &&
    reading.finished_at &&
    new Date(activity.generated_at).getTime() - new Date(reading.finished_at).getTime() < 60 * 60 * 1000;
  if (!isActive && !finishedRecently && lines.length === 0) return null;

  return (
    <Link
      href={activityHref(activity, uploads)}
      className="block rounded-md border border-[var(--outline)] bg-[var(--surface-strong)] px-3 py-2 text-xs hover:border-[var(--teal)]"
      aria-live="polite"
    >
      <div className="flex items-center gap-2 font-semibold text-[var(--foreground)]">
        {isActive ? (
          <Loader2 size={14} className="animate-spin text-[var(--teal)]" aria-hidden="true" />
        ) : null}
        {isActive ? "Working in the background" : "Background work finished"}
      </div>
      {isActive ? (
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
      {isActive && reading && reading.total > 0 ? (
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

/** The page-top strip: shown on every page except the one it leads to, while work runs. */
export function ActivityBanner() {
  const pathname = usePathname();
  const activity = useBackgroundActivity();
  const uploads = useBackgroundUploadQueue()?.uploads ?? [];
  const isActive = Boolean(activity?.active) || uploads.some(inFlight);
  if (!isActive) return null;
  const href = activityHref(activity, uploads);
  if (pathname.startsWith(href)) return null;
  const lines = summarize(activity, uploads);
  const hasError = lines.some((line) => line.tone === "error");
  return (
    <Link
      href={href}
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
