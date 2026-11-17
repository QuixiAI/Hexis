import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ActivityBanner,
  BackgroundActivityProvider,
  useBackgroundUploadQueue,
} from "./background-activity";

vi.mock("next/navigation", () => ({ usePathname: () => "/chat" }));

/** A page-like consumer: drops files into the queue and starts the upload. */
function Consumer() {
  const queue = useBackgroundUploadQueue();
  if (!queue) return <p>no queue</p>;
  return (
    <div>
      <button
        type="button"
        onClick={() =>
          queue.enqueueFiles(
            [
              new File(["one"], "one.txt", { type: "text/plain" }),
              new File(["two"], "two.txt", { type: "text/plain" }),
              new File([], "empty.txt", { type: "text/plain" }),
            ],
            { mode: "slow" },
          )
        }
      >
        add
      </button>
      <button type="button" onClick={() => queue.toggleSensitivity(queue.uploads[0]?.id ?? "")}>
        private
      </button>
      <button type="button" onClick={() => void queue.startQueuedUploads()}>
        start
      </button>
      <button type="button" onClick={() => queue.clearAccepted()}>
        clear
      </button>
      <ul>
        {queue.uploads.map((item) => (
          <li key={item.id}>
            {item.name} {item.state} {item.sensitivity ?? "shareable"} {item.jobId ?? ""} {item.detail ?? ""}
          </li>
        ))}
      </ul>
      <p>{queue.uploading ? "uploading" : "idle"}</p>
    </div>
  );
}

describe("background upload queue", () => {
  const posted: FormData[] = [];

  beforeEach(() => {
    posted.length = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/activity")) {
          return Response.json({ active: false, generated_at: new Date().toISOString(), download: null, reading: null });
        }
        if (url.endsWith("/api/ingest/file")) {
          const form = init?.body as FormData;
          posted.push(form);
          const file = form.get("file") as File;
          if (file.name === "two.txt") return Response.json({ detail: "unsupported type" }, { status: 415 });
          return Response.json({ job_id: `job-${file.name}` });
        }
        return Response.json({});
      }) as unknown as typeof fetch,
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uploads queued files one by one, sends the private flag, and keeps results", async () => {
    render(
      <BackgroundActivityProvider>
        <Consumer />
      </BackgroundActivityProvider>,
    );
    await act(async () => {
      fireEvent.click(screen.getByText("add"));
    });
    // The empty file is dropped; the other two wait in order.
    expect(screen.getByText(/one.txt queued shareable/)).toBeInTheDocument();
    expect(screen.getByText(/two.txt queued shareable/)).toBeInTheDocument();
    expect(screen.queryByText(/empty.txt/)).toBeNull();

    await act(async () => {
      fireEvent.click(screen.getByText("private"));
    });
    expect(screen.getByText(/one.txt queued private/)).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByText("start"));
    });
    await waitFor(() => expect(screen.getByText("idle")).toBeInTheDocument());
    expect(screen.getByText(/one.txt accepted private job-one.txt/)).toBeInTheDocument();
    expect(screen.getByText(/two.txt failed shareable unsupported type/)).toBeInTheDocument();

    expect(posted).toHaveLength(2);
    expect(posted[0].get("mode")).toBe("slow");
    expect(posted[0].get("sensitivity")).toBe("private");
    expect(posted[1].get("sensitivity")).toBeNull();

    await act(async () => {
      fireEvent.click(screen.getByText("clear"));
    });
    expect(screen.queryByText(/one.txt/)).toBeNull();
    expect(screen.getByText(/two.txt failed/)).toBeInTheDocument();
  });

  it("shows the queue on other pages and warns before the tab closes while files are unsent", async () => {
    // Uploads never finish here, so the queue stays in flight.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/ingest/file")) return new Promise<Response>(() => {});
        return Response.json({ active: false, generated_at: new Date().toISOString(), download: null, reading: null });
      }) as unknown as typeof fetch,
    );
    render(
      <BackgroundActivityProvider>
        <ActivityBanner />
        <Consumer />
      </BackgroundActivityProvider>,
    );
    expect(screen.queryByRole("status")).toBeNull();
    await act(async () => {
      fireEvent.click(screen.getByText("add"));
    });
    await act(async () => {
      fireEvent.click(screen.getByText("start"));
    });
    const banner = screen.getByRole("status");
    expect(banner).toHaveTextContent("Uploading · 0 / 2 files");
    expect(banner).toHaveAttribute("href", "/ingest");

    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });
});
