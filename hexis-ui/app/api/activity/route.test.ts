import { afterEach, describe, expect, it, vi } from "vitest";

const queryRawUnsafe = vi.fn();
vi.mock("@/lib/prisma", () => ({
  prisma: { $queryRawUnsafe: (...args: unknown[]) => queryRawUnsafe(...args) },
}));

import { GET } from "./route";

describe("/api/activity", () => {
  afterEach(() => {
    queryRawUnsafe.mockReset();
  });

  it("reports a running import as active, with download, reading and failures", async () => {
    queryRawUnsafe
      .mockResolvedValueOnce([
        {
          connector_id: "gmail",
          status: "in_progress",
          progress: JSON.stringify({ items_seen: 120, max_messages: 500, items_stored: 80 }),
          error: null,
        },
      ])
      .mockResolvedValueOnce([
        {
          total: 518,
          done: 238,
          active: 8,
          pending: 270,
          failed: 2,
          memories: 4925,
          last_error: "boom",
          finished_at: null,
        },
      ])
      .mockResolvedValueOnce([{ pending: 14 }]);

    const response = await GET();
    expect(response.status).toBe(200);
    const payload = await response.json();
    expect(payload.active).toBe(true);
    expect(payload.download).toMatchObject({
      connector_id: "gmail",
      done: 120,
      total: 500,
      items: 80,
    });
    expect(payload.reading).toMatchObject({
      total: 518,
      done: 238,
      active: 8,
      pending: 270,
      failed: 2,
      memories: 4925,
      last_error: "boom",
    });
    expect(payload.indexing).toEqual({ pending: 14 });
  });

  it("is quiet when nothing is queued", async () => {
    queryRawUnsafe
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([
        { total: 0, done: 0, active: 0, pending: 0, failed: 0, memories: 0, last_error: null, finished_at: null },
      ])
      .mockResolvedValueOnce([{ pending: 0 }]);

    const payload = await (await GET()).json();
    expect(payload).toMatchObject({ active: false, download: null, reading: null, indexing: { pending: 0 } });
  });

  it("stays active while documents are still queued after the download finished", async () => {
    queryRawUnsafe
      .mockResolvedValueOnce([{ connector_id: "gmail", status: "completed", progress: "{}", error: null }])
      .mockResolvedValueOnce([
        {
          total: 10,
          done: 4,
          active: 2,
          pending: 4,
          failed: 0,
          memories: 40,
          last_error: null,
          finished_at: new Date("2026-10-14T22:00:00Z"),
        },
      ])
      .mockResolvedValueOnce([{ pending: 3 }]);

    const payload = await (await GET()).json();
    expect(payload.active).toBe(true);
    expect(payload.download.status).toBe("completed");
    expect(payload.reading.finished_at).toBe("2026-10-14T22:00:00.000Z");
  });
});
