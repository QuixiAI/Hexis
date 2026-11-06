"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardPaste,
  FilePlus2,
  FolderUp,
  Globe,
  Loader2,
  Lock,
  LockOpen,
  MessageSquare,
  UploadCloud,
  X,
} from "lucide-react";
import Link from "next/link";
import { type IngestMode, useBackgroundUploadQueue } from "../components/nav/background-activity";
import { Card } from "../components/ui/card";
import { PageHeader } from "../components/ui/page-header";
import { Spinner } from "../components/ui/spinner";
import {
  IngestJob,
  isActiveIngestJob,
  mergeIngestJobs,
  normalizeIngestJob,
  retainActiveTrackedJobIds,
} from "./jobs";

type PendingFileState = "queued" | "uploading" | "accepted" | "failed";

type PendingFile = {
  id: string;
  file: File;
  name: string;
  size: number;
  // "private" keeps the ingested memories out of group-channel recall and
  // exports; toggled per file before upload.
  sensitivity: "private" | null;
  state: PendingFileState;
  detail?: string;
  jobId?: string;
};

const PRIVATE_HINT = "Private: kept out of group conversations and exports.";
const SHAREABLE_HINT = "Shareable. Click to mark private.";

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

const MODES: readonly IngestMode[] = ["fast", "slow", "hybrid"];

export default function IngestPage() {
  // The queue lives in the app shell so uploads keep going after the user
  // leaves this page; the local state is only for a page rendered without it.
  const bgQueue = useBackgroundUploadQueue();
  const [localPending, setLocalPending] = useState<PendingFile[]>([]);
  const [mode, setMode] = useState<IngestMode>("fast");
  const [localUploading, setLocalUploading] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [pasteTitle, setPasteTitle] = useState("");
  const [pastePrivate, setPastePrivate] = useState(false);
  const [pasteBusy, setPasteBusy] = useState(false);
  const [url, setUrl] = useState("");
  const [urlBusy, setUrlBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [errorNotice, setErrorNotice] = useState<string | null>(null);
  const [jobs, setJobs] = useState<IngestJob[]>([]);
  const [trackedJobIds, setTrackedJobIds] = useState<string[]>([]);
  const [jobsLoading, setJobsLoading] = useState(true);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const trackedJobIdsRef = useRef(trackedJobIds);
  const jobsFetchInFlightRef = useRef(false);
  const pending: PendingFile[] = bgQueue
    ? bgQueue.uploads.map((item) => ({
        id: item.id,
        file: item.file,
        name: item.name,
        size: item.size,
        sensitivity: item.sensitivity,
        state: item.state,
        detail: item.detail,
        jobId: item.jobId,
      }))
    : localPending;
  const uploading = bgQueue ? bgQueue.uploading : localUploading;
  const hasActiveJobs = trackedJobIds.length > 0 || jobs.some(isActiveIngestJob) || uploading;

  useEffect(() => {
    trackedJobIdsRef.current = trackedJobIds;
  }, [trackedJobIds]);

  const trackAcceptedJob = useCallback((job: IngestJob) => {
    setJobs((prev) => mergeIngestJobs(prev, [job]));
    if (isActiveIngestJob(job)) {
      setTrackedJobIds((prev) => (prev.includes(job.id) ? prev : [job.id, ...prev]));
    }
  }, []);

  // Each accepted upload's job is added to the list once; after that, polling owns its status.
  const handedOffJobIds = useRef(new Set<string>());
  useEffect(() => {
    if (!bgQueue) return;
    for (const item of bgQueue.uploads) {
      if (item.state !== "accepted" || !item.jobId || handedOffJobIds.current.has(item.jobId)) continue;
      handedOffJobIds.current.add(item.jobId);
      const job = normalizeIngestJob(
        { id: item.jobId, kind: "artifact", status: "pending", title: item.name, result: null, payload: {} },
        { title: item.name }
      );
      if (job) trackAcceptedJob(job);
    }
  }, [bgQueue, trackAcceptedJob]);

  const fetchJobs = useCallback(async () => {
    if (jobsFetchInFlightRef.current) return;
    jobsFetchInFlightRef.current = true;
    const trackedIds = trackedJobIdsRef.current;
    const recentJobs: IngestJob[] = [];
    try {
      try {
        const res = await fetch("/api/ingest/jobs?limit=25", { cache: "no-store" });
        if (res.ok) {
          const data = await res.json();
          for (const item of Array.isArray(data.jobs) ? data.jobs : []) {
            const job = normalizeIngestJob(item);
            if (job) recentJobs.push(job);
          }
        }
      } catch {
        // Keep polling tracked jobs even when the recent-list proxy is unavailable.
      }

      const exactJobs: IngestJob[] = [];
      const missingIds = new Set<string>();
      await Promise.all(
        trackedIds.map(async (id) => {
          try {
            const exact = await fetch(`/api/ingest/jobs/${encodeURIComponent(id)}`, {
              cache: "no-store",
            });
            if (exact.status === 404) {
              missingIds.add(id);
              return;
            }
            if (!exact.ok) return;
            const job = normalizeIngestJob(await exact.json());
            if (job) exactJobs.push(job);
          } catch {
            // Keep the job tracked and visible; the next poll may succeed.
          }
        })
      );

      const exactById = new Map(exactJobs.map((job) => [job.id, job]));
      setTrackedJobIds((prev) => retainActiveTrackedJobIds(prev, missingIds, exactById));
      setJobs((prev) => {
        const unresolvedTracked = prev.filter(
          (job) =>
            trackedIds.includes(job.id) &&
            !missingIds.has(job.id) &&
            !exactById.has(job.id)
        );
        return mergeIngestJobs([...recentJobs, ...unresolvedTracked], exactJobs);
      });
    } finally {
      setJobsLoading(false);
      jobsFetchInFlightRef.current = false;
    }
  }, []);

  useEffect(() => {
    void fetchJobs();
  }, [fetchJobs]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void fetchJobs();
    }, hasActiveJobs ? 3000 : 15000);
    return () => window.clearInterval(timer);
  }, [fetchJobs, hasActiveJobs]);

  const addFiles = (files: FileList | File[] | null) => {
    if (!files) return;
    const items = Array.from(files).filter((file) => file.size > 0);
    if (!items.length) return;
    if (bgQueue) {
      bgQueue.enqueueFiles(items, { mode });
      return;
    }
    setLocalPending((prev) => [
      ...prev,
      ...items.map((file) => ({
        id: crypto.randomUUID(),
        file,
        name: file.name,
        size: file.size,
        sensitivity: null,
        state: "queued" as const,
      })),
    ]);
  };

  const togglePrivate = (id: string) => {
    if (bgQueue) {
      bgQueue.toggleSensitivity(id);
      return;
    }
    setLocalPending((prev) =>
      prev.map((p) =>
        p.id === id ? { ...p, sensitivity: p.sensitivity === "private" ? null : "private" } : p
      )
    );
  };

  const removePending = (id: string) => {
    if (bgQueue) {
      bgQueue.removeUpload(id);
      return;
    }
    setLocalPending((prev) => prev.filter((p) => p.id !== id));
  };

  const acceptedNotice = (accepted: number) =>
    `${accepted} file(s) accepted — originals preserved; extraction runs in the background below.`;

  const uploadAll = async () => {
    if (uploading || !pending.some((p) => p.state === "queued")) return;
    setNotice(null);
    setErrorNotice(null);
    if (bgQueue) {
      const accepted = await bgQueue.startQueuedUploads(mode);
      if (accepted) setNotice(acceptedNotice(accepted));
      return;
    }
    setLocalUploading(true);
    let accepted = 0;
    for (const item of localPending) {
      if (item.state !== "queued") continue;
      setLocalPending((prev) => prev.map((p) => (p.id === item.id ? { ...p, state: "uploading" } : p)));
      try {
        const form = new FormData();
        form.append("file", item.file, item.name);
        form.append("mode", mode);
        if (item.sensitivity) form.append("sensitivity", item.sensitivity);
        const res = await fetch("/api/ingest/file", { method: "POST", body: form });
        if (res.ok) {
          const body = await res.json();
          const job = normalizeIngestJob(
            {
              id: body.job_id,
              kind: "artifact",
              status: "pending",
              title: body.filename || item.name,
              result: null,
              payload: body,
            },
            { title: item.name }
          );
          if (job) trackAcceptedJob(job);
          accepted += 1;
          setLocalPending((prev) =>
            prev.map((p) =>
              p.id === item.id ? { ...p, state: "accepted", jobId: job?.id } : p
            )
          );
        } else {
          let detail = `${res.status}`;
          try {
            const body = await res.json();
            detail = body?.detail || body?.error || detail;
          } catch {
            // non-JSON error body
          }
          setLocalPending((prev) =>
            prev.map((p) => (p.id === item.id ? { ...p, state: "failed", detail } : p))
          );
        }
      } catch (err) {
        setLocalPending((prev) =>
          prev.map((p) =>
            p.id === item.id
              ? { ...p, state: "failed", detail: err instanceof Error ? err.message : "network error" }
              : p
          )
        );
      }
    }
    setLocalUploading(false);
    if (accepted) {
      setNotice(acceptedNotice(accepted));
      window.setTimeout(() => {
        setLocalPending((prev) => prev.filter((p) => p.state !== "accepted"));
      }, 2500);
    }
  };

  const submitPaste = async () => {
    const content = pasteText.trim();
    if (!content || pasteBusy) return;
    setPasteBusy(true);
    setNotice(null);
    setErrorNotice(null);
    try {
      const res = await fetch("/api/ingest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content,
          title: pasteTitle.trim() || undefined,
          mode,
          sensitivity: pastePrivate ? "private" : undefined,
        }),
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(`Paste ingestion failed (${res.status}): ${body.slice(0, 160)}`);
      }
      setPasteText("");
      setPasteTitle("");
      setPastePrivate(false);
      const body = await res.json();
      const job = normalizeIngestJob({
        id: body.job_id,
        kind: "text",
        status: "pending",
        title: body.title || pasteTitle.trim() || "Pasted text",
        result: null,
        payload: body,
      });
      if (job) trackAcceptedJob(job);
      setNotice("Text accepted — extraction runs in the background below.");
    } catch (err) {
      setErrorNotice(err instanceof Error ? err.message : "Paste ingestion failed.");
    } finally {
      setPasteBusy(false);
    }
  };

  const submitUrl = async () => {
    const target = url.trim();
    if (!target || urlBusy) return;
    if (!/^https?:\/\//i.test(target)) {
      setErrorNotice("URL must start with http:// or https://");
      return;
    }
    setUrlBusy(true);
    setNotice(null);
    setErrorNotice(null);
    try {
      // URL jobs ride the same durable queue; the worker fetches the page,
      // preserves the HTML artifact, and ingests the extracted text.
      const res = await fetch("/api/ingest/url", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: target, mode }),
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(`URL ingestion failed (${res.status}): ${body.slice(0, 160)}`);
      }
      const body = await res.json();
      const job = normalizeIngestJob({
        id: body.job_id,
        kind: "url",
        status: "pending",
        title: body.url || target,
        result: null,
        payload: body,
      });
      if (job) trackAcceptedJob(job);
      setUrl("");
      setNotice("URL accepted — fetch and extraction run in the background below.");
    } catch (err) {
      setErrorNotice(err instanceof Error ? err.message : "URL ingestion failed.");
    } finally {
      setUrlBusy(false);
    }
  };

  const statusBadge = (job: IngestJob) => {
    if (job.status === "completed")
      return (
        <span className="flex items-center gap-1 text-xs font-medium text-[var(--teal)]">
          <CheckCircle2 size={13} /> completed
          {job.result?.memories_created !== undefined
            ? ` · ${job.result.memories_created} memories`
            : ""}
        </span>
      );
    if (job.status === "failed" || job.status === "cancelled")
      return (
        <span className="flex items-center gap-1 text-xs font-medium text-red-600">
          <AlertTriangle size={13} /> {job.status}
        </span>
      );
    return (
      <span className="flex items-center gap-1 text-xs font-medium text-[var(--ink-soft)]">
        <Loader2 size={13} className="animate-spin" /> {job.status.replace("_", " ")}
      </span>
    );
  };

  return (
    <div className="space-y-6 p-6">
      <PageHeader
        title="Ingest"
        subtitle="Put files, text, and web pages into the filing cabinet — originals are preserved, memories are distilled in the background."
      />

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-[var(--teal)]/30 bg-[var(--teal)]/5 px-4 py-3 text-sm">
        <div>
          <p className="font-semibold text-[var(--foreground)]">Reading continues in the background</p>
          <p className="mt-0.5 text-xs text-[var(--ink-soft)]">
            Large batches take a while. Uploads keep going if you leave this page, and you can chat while the documents are read.
          </p>
        </div>
        <Link
          href="/chat"
          className="inline-flex items-center gap-1.5 rounded-md bg-[var(--foreground)] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[var(--teal)]"
        >
          <MessageSquare size={13} /> Go to chat
        </Link>
      </div>

      {notice ? (
        <div className="rounded-md border border-[var(--teal)]/40 bg-[var(--teal)]/5 px-3 py-2 text-sm text-[var(--foreground)]">
          {notice}
        </div>
      ) : null}
      {errorNotice ? (
        <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
          {errorNotice}
        </div>
      ) : null}

      <div className="flex items-center gap-2 text-sm">
        <span className="text-[var(--ink-soft)]">Mode</span>
        {MODES.map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => setMode(m)}
            className={`rounded-md border px-2.5 py-1 text-xs font-medium capitalize ${
              mode === m
                ? "border-[var(--teal)] bg-[var(--teal)]/10 text-[var(--foreground)]"
                : "border-[var(--outline)] text-[var(--ink-soft)] hover:border-[var(--teal)]"
            }`}
          >
            {m}
          </button>
        ))}
        <span className="text-xs text-[var(--ink-soft)]">
          fast = quick facts · slow = conscious reading · hybrid = triage then deep-read
        </span>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 className="flex items-center gap-2 text-sm font-semibold">
              <UploadCloud size={16} className="text-[var(--teal)]" /> Upload files
            </h2>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="inline-flex items-center gap-1 rounded-md border border-[var(--outline)] px-2.5 py-1 text-xs font-medium hover:border-[var(--teal)]"
              >
                <FilePlus2 size={13} /> Select files
              </button>
              <button
                type="button"
                onClick={() => folderInputRef.current?.click()}
                className="inline-flex items-center gap-1 rounded-md border border-[var(--outline)] px-2.5 py-1 text-xs font-medium hover:border-[var(--teal)]"
              >
                <FolderUp size={13} /> Select folder
              </button>
            </div>
          </div>
          <div
            role="button"
            tabIndex={0}
            aria-label="Drop files here or click to browse"
            onClick={() => fileInputRef.current?.click()}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") fileInputRef.current?.click();
            }}
            onDragOver={(event) => {
              if (event.dataTransfer?.types?.includes("Files")) {
                event.preventDefault();
                setDragActive(true);
              }
            }}
            onDragLeave={() => setDragActive(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragActive(false);
              addFiles(event.dataTransfer?.files ?? null);
            }}
            className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-4 py-8 text-center text-sm transition ${
              dragActive
                ? "border-[var(--teal)] bg-[var(--teal)]/5"
                : "border-[var(--outline)] text-[var(--ink-soft)] hover:border-[var(--teal)]"
            }`}
          >
            <FilePlus2 size={22} />
            <span>Drop files here, or click to browse</span>
            <span className="text-xs">PDF, DOCX, XLSX, Markdown, code, email, and more</span>
          </div>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            aria-hidden="true"
            tabIndex={-1}
            onChange={(event) => {
              addFiles(event.target.files);
              event.target.value = "";
            }}
          />
          <input
            ref={folderInputRef}
            type="file"
            multiple
            // @ts-expect-error non-standard folder selection attribute supported by modern browsers
            webkitdirectory=""
            className="hidden"
            aria-hidden="true"
            tabIndex={-1}
            onChange={(event) => {
              addFiles(event.target.files);
              event.target.value = "";
            }}
          />

          {pending.length > 0 ? (
            <div className="mt-3 space-y-2">
              {pending.map((item) => (
                <div
                  key={item.id}
                  className="flex items-center gap-2 rounded-md border border-[var(--outline)] px-2 py-1.5 text-xs"
                >
                  <span className="max-w-52 flex-none truncate font-medium">{item.name}</span>
                  <span className="flex-none text-[var(--ink-soft)]">{formatBytes(item.size)}</span>
                  <span className="min-w-0 flex-1 truncate text-[var(--ink-soft)]">
                    {item.state === "uploading" ? "uploading…" : null}
                    {item.state === "accepted"
                      ? `accepted${item.jobId ? ` · ${item.jobId.slice(0, 8)}` : ""}`
                      : null}
                    {item.state === "failed" ? `failed: ${item.detail}` : null}
                  </span>
                  <button
                    type="button"
                    aria-label={
                      item.sensitivity === "private"
                        ? `Make ${item.name} shareable`
                        : `Mark ${item.name} private`
                    }
                    title={item.sensitivity === "private" ? PRIVATE_HINT : SHAREABLE_HINT}
                    disabled={item.state !== "queued"}
                    onClick={() => togglePrivate(item.id)}
                    className={`flex-none rounded p-0.5 disabled:opacity-40 ${
                      item.sensitivity === "private"
                        ? "text-[var(--teal)]"
                        : "text-[var(--ink-soft)] hover:text-[var(--foreground)]"
                    }`}
                  >
                    {item.sensitivity === "private" ? <Lock size={12} /> : <LockOpen size={12} />}
                  </button>
                  <button
                    type="button"
                    aria-label={`Remove ${item.name}`}
                    title="Remove"
                    onClick={() => removePending(item.id)}
                    className="flex-none rounded p-0.5 text-[var(--ink-soft)] hover:text-[var(--foreground)]"
                  >
                    <X size={12} />
                  </button>
                </div>
              ))}
              <button
                type="button"
                onClick={uploadAll}
                disabled={uploading || !pending.some((p) => p.state === "queued")}
                className="mt-1 rounded-md bg-[var(--foreground)] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[var(--teal)] disabled:opacity-40"
              >
                {uploading ? "Uploading…" : `Ingest ${pending.filter((p) => p.state === "queued").length} file(s)`}
              </button>
            </div>
          ) : null}
        </Card>

        <div className="space-y-6">
          <Card className="p-4">
            <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold">
              <ClipboardPaste size={16} className="text-[var(--teal)]" /> Paste text
            </h2>
            <input
              type="text"
              value={pasteTitle}
              onChange={(event) => setPasteTitle(event.target.value)}
              placeholder="Title (optional)"
              className="mb-2 w-full rounded-md border border-[var(--outline)] px-2 py-1.5 text-sm outline-none focus:border-[var(--teal)]"
            />
            <textarea
              value={pasteText}
              onChange={(event) => setPasteText(event.target.value)}
              placeholder="Paste a document, notes, or any large text…"
              rows={5}
              className="w-full resize-y rounded-md border border-[var(--outline)] px-2 py-1.5 text-sm outline-none focus:border-[var(--teal)]"
            />
            <div className="mt-2 flex items-center gap-3">
              <button
                type="button"
                onClick={submitPaste}
                disabled={pasteBusy || !pasteText.trim()}
                className="rounded-md bg-[var(--foreground)] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[var(--teal)] disabled:opacity-40"
              >
                {pasteBusy ? "Submitting…" : "Ingest text"}
              </button>
              <label className="flex items-center gap-1.5 text-xs text-[var(--ink-soft)]">
                <input
                  type="checkbox"
                  checked={pastePrivate}
                  onChange={(event) => setPastePrivate(event.target.checked)}
                />
                Private (kept out of group conversations and exports)
              </label>
            </div>
          </Card>

          <Card className="p-4">
            <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold">
              <Globe size={16} className="text-[var(--teal)]" /> Ingest a web page
            </h2>
            <div className="flex gap-2">
              <input
                type="url"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") submitUrl();
                }}
                placeholder="https://example.com/article"
                className="min-w-0 flex-1 rounded-md border border-[var(--outline)] px-2 py-1.5 text-sm outline-none focus:border-[var(--teal)]"
              />
              <button
                type="button"
                onClick={submitUrl}
                disabled={urlBusy || !url.trim()}
                className="rounded-md bg-[var(--foreground)] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[var(--teal)] disabled:opacity-40"
              >
                {urlBusy ? "Submitting…" : "Fetch & ingest"}
              </button>
            </div>
          </Card>
        </div>
      </div>

      <Card className="p-4">
        <h2 className="mb-3 text-sm font-semibold">Recent ingestion jobs</h2>
        {jobsLoading ? (
          <div className="flex items-center gap-2 text-sm text-[var(--ink-soft)]">
            <Spinner /> Loading jobs…
          </div>
        ) : jobs.length === 0 ? (
          <p className="text-sm text-[var(--ink-soft)]">
            Nothing ingested through jobs yet — drop a file above to get started.
          </p>
        ) : (
          <div className="space-y-1.5">
            {jobs.map((job) => (
              <div
                key={job.id}
                className="flex items-center gap-3 rounded-md border border-[var(--outline)] px-3 py-1.5 text-sm"
              >
                <span className="w-14 flex-none text-xs uppercase text-[var(--ink-soft)]">{job.kind}</span>
                <span className="min-w-0 flex-1 truncate">{job.title || job.id.slice(0, 8)}</span>
                {statusBadge(job)}
                {job.error ? (
                  <span className="max-w-64 truncate text-xs text-red-600" title={job.error}>
                    {job.error}
                  </span>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
