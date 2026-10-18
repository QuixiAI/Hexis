"use client";

import { ExternalLink, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import { Button } from "../components/ui/button";

type DriveFolder = { id: string; name: string; url?: string | null };
type ImportState = {
  status: "pending" | "in_progress" | "paused" | "completed" | "failed" | "cancelled";
  progress: Partial<Record<"files_seen" | "files_read" | "unchanged" | "skipped" | "failed", number>>;
  error?: string | null;
};
export type DriveStatus = {
  connected: boolean;
  account?: string | null;
  folders: DriveFolder[];
  import: ImportState | null;
  files_remembered: number;
};

const ERRORS: Record<string, string> = {
  invalid_link: "That isn't a Drive folder link. Open the folder in Drive and copy the address.",
  not_a_folder: "That link is to a file, not a folder.",
  not_found: "The signed-in Google account can't find that folder. Ask its owner to share it with that account.",
  no_access: "The signed-in Google account doesn't have access to that folder. Ask its owner to share it with that account.",
  drive_api_disabled:
    "The Google Drive API is turned off in the Google Cloud project Hexis signs in with. Turn it on there, then try again.",
  not_connected: "Sign in to Google first.",
  no_folders: "Add a folder first.",
};

async function call<T>(path: string, body?: object): Promise<T> {
  const res = await fetch(`/api/integrations/google_drive${path}`, {
    method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(payload.error || `HTTP ${res.status}`), { code: payload.error_code });
  return payload as T;
}

const running = (state: ImportState | null) => state?.status === "pending" || state?.status === "in_progress";

function importText(job: ImportState): string {
  const c = {
    seen: job.progress.files_seen ?? 0,
    read: job.progress.files_read ?? 0,
    unchanged: job.progress.unchanged ?? 0,
    skipped: job.progress.skipped ?? 0,
    failed: job.progress.failed ?? 0,
  };
  switch (job.status) {
    case "pending":
      return "Waiting for the maintenance worker to start…";
    case "in_progress":
      return `Reading: ${c.seen} files so far, ${c.read} read.`;
    case "paused":
      return "Paused.";
    case "completed":
      return `Done: ${c.read} files read, ${c.unchanged} unchanged, ${c.skipped} skipped, ${c.failed} couldn't be read.`;
    case "failed":
      return `Stopped: ${job.error ?? "unknown error"}`;
    case "cancelled":
      return "Cancelled.";
  }
}

/**
 * Google Drive: sign in with read-only access, choose folders, and have the
 * agent read them. Sign-in uses the Google setup the Gmail card makes.
 */
export function DriveControls() {
  const [status, setStatus] = useState<DriveStatus | null>(null);
  const [link, setLink] = useState("");
  const [busy, setBusy] = useState<"signIn" | "add" | "import" | "signOut" | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [needsSetup, setNeedsSetup] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setStatus(await call<DriveStatus>(""));
    } catch (err) {
      setError(`Something went wrong: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // While Google is open in another tab, and while an import runs, keep checking.
  const polling = (waiting && !status?.connected) || running(status?.import ?? null);
  useEffect(() => {
    if (!polling) return;
    const timer = setInterval(() => void refresh(), 3000);
    return () => clearInterval(timer);
  }, [polling, refresh]);

  const connected = Boolean(status?.connected);
  useEffect(() => {
    if (connected && waiting) setWaiting(false);
  }, [connected, waiting]);

  const explain = (err: unknown) => {
    const code = (err as { code?: unknown })?.code;
    const message = err instanceof Error ? err.message : String(err);
    setError(
      typeof code === "string" && ERRORS[code]
        ? ERRORS[code]
        : code === "drive_error"
          ? `Google Drive returned an error: ${message}`
          : `Something went wrong: ${message}`,
    );
  };

  const run = async (kind: NonNullable<typeof busy>, action: () => Promise<void>) => {
    setBusy(kind);
    setError(null);
    try {
      await action();
    } catch (err) {
      explain(err);
    } finally {
      setBusy(null);
    }
  };

  const signIn = () => {
    // Opened now, while the click still counts, so the browser doesn't block it.
    const tab = window.open("", "_blank");
    void run("signIn", async () => {
      try {
        const { authorization_url } = await call<{ authorization_url: string }>("/connect", {});
        if (tab) tab.location.href = authorization_url;
        else window.open(authorization_url, "_blank");
        setNeedsSetup(false);
        setWaiting(true);
      } catch (err) {
        tab?.close();
        if ((err as { code?: unknown })?.code === "needs_google_setup") {
          setNeedsSetup(true);
          return;
        }
        throw err;
      }
    });
  };

  const addFolder = () =>
    run("add", async () => {
      setStatus(await call<DriveStatus>("/folders", { link }));
      setLink("");
    });
  const removeFolder = (id: string) => run("add", async () => setStatus(await call<DriveStatus>("/folders/remove", { id })));
  const startImport = () => run("import", async () => setStatus(await call<DriveStatus>("/import", {})));
  const signOut = () => {
    if (!window.confirm("Sign out of Google Drive? What it has already remembered stays, but it stops reading these folders.")) {
      return;
    }
    void run("signOut", async () => setStatus(await call<DriveStatus>("/disconnect", {})));
  };

  const note = (text: string) => <p className="text-xs leading-relaxed text-[var(--ink-soft)]">{text}</p>;
  const block = (title: string, children: ReactNode) => (
    <div className="space-y-2">
      <p className="text-sm font-medium text-[var(--foreground)]">{title}</p>
      {children}
    </div>
  );
  const alert = error ? <p role="alert" className="text-sm text-red-700">{error}</p> : null;

  if (!status) return <p className="text-sm text-[var(--ink-soft)]">Checking Google Drive…</p>;

  if (!status.connected) {
    return (
      <div className="space-y-3 border-t border-[var(--outline)] pt-4">
        {block("Sign in to Google", note("Google opens in a new tab. Approve read-only access to Drive, then come back here."))}
        <Button type="button" onClick={signIn} disabled={busy !== null} className="px-3 py-1.5 text-xs">
          Start Google sign-in
        </Button>
        {waiting ? <p role="status" className="text-xs text-[var(--ink-soft)]">Waiting for Google… Approve access in the other tab.</p> : null}
        {needsSetup ? (
          <p role="alert" className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs leading-relaxed">
            Google sign-in isn&apos;t set up yet. Upload the Google setup file on the Gmail card; Drive uses the same
            setup. If that setup was made for Gmail, also turn on the Google Drive API in the same Google Cloud project.
          </p>
        ) : null}
        {alert}
      </div>
    );
  }

  const job = status.import;
  return (
    <div className="space-y-4 border-t border-[var(--outline)] pt-4">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="font-medium">Signed in as {status.account ?? "Google"}.</span>
        <button type="button" onClick={signOut} disabled={busy !== null} className="text-xs text-[var(--ink-soft)] underline disabled:opacity-50">
          Sign out of Google Drive
        </button>
      </div>

      {block(
        "Folders to read",
        <>
          {note("Paste the link of a Drive folder, from the address bar or from Share → Copy link. Subfolders are included.")}
          {status.folders.length === 0 ? (
            <p className="text-xs text-[var(--ink-soft)]">No folders yet.</p>
          ) : (
            <ul className="divide-y divide-[var(--outline)] rounded-md border border-[var(--outline)]">
              {status.folders.map((folder) => (
                <li key={folder.id} className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
                  <span className="min-w-0 truncate">{folder.name}</span>
                  <span className="flex shrink-0 items-center gap-2">
                    {folder.url ? (
                      <a href={folder.url} target="_blank" rel="noreferrer" aria-label="Open in Drive" className="text-[var(--ink-soft)] hover:text-[var(--foreground)]">
                        <ExternalLink size={14} aria-hidden />
                      </a>
                    ) : null}
                    <button type="button" onClick={() => void removeFolder(folder.id)} disabled={busy !== null} aria-label={`Remove ${folder.name}`} className="text-[var(--ink-soft)] hover:text-red-700 disabled:opacity-50">
                      <Trash2 size={14} aria-hidden />
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          )}
          <form
            className="flex flex-col gap-2 sm:flex-row"
            onSubmit={(e) => {
              e.preventDefault();
              void addFolder();
            }}
          >
            <label className="sr-only" htmlFor="drive-folder-link">Drive folder link</label>
            <input id="drive-folder-link" value={link} onChange={(e) => setLink(e.target.value)} placeholder="https://drive.google.com/drive/folders/…" spellCheck={false} className="min-w-0 flex-1 rounded-md border border-[var(--outline)] bg-white px-3 py-1.5 text-sm" />
            <Button type="submit" variant="ghost" disabled={busy !== null || !link.trim()} className="px-3 py-1.5 text-xs">
              {busy === "add" ? "Checking the folder…" : "Add folder"}
            </Button>
          </form>
        </>,
      )}

      {block(
        "Read the folders",
        <>
          {note("The folders are read in the background. Later imports read only files that are new or changed.")}
          {job ? (
            <p role="status" className={`text-sm ${job.status === "failed" ? "text-red-700" : "text-[var(--foreground)]"}`}>
              {importText(job)}
            </p>
          ) : null}
          {job?.status === "completed" && (job.progress.skipped ?? 0) > 0
            ? note("Skipped files are types it doesn't read, such as images and video.")
            : null}
          {note(
            status.files_remembered
              ? `${status.files_remembered} ${status.files_remembered === 1 ? "file" : "files"} from Drive ${status.files_remembered === 1 ? "is" : "are"} in its memory or on the way.`
              : "Nothing from Drive remembered yet.",
          )}
          <Button type="button" onClick={() => void startImport()} disabled={busy !== null || status.folders.length === 0 || running(job)} className="px-3 py-1.5 text-xs">
            {busy === "import" ? "Starting…" : job ? "Read the folders again" : "Read the folders now"}
          </Button>
        </>,
      )}
      {alert}
    </div>
  );
}
