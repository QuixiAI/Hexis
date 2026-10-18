import { NextResponse } from "next/server";

import { normalizeJsonValue } from "@/lib/db";
import { prisma } from "@/lib/prisma";

export const runtime = "nodejs";

type Row = Record<string, unknown>;

/**
 * What Hexis is doing in the background right now, in the user's terms:
 * importing from a connected service, reading documents into memory, and
 * indexing new memories. One query set, shared by every page.
 */
export type BackgroundActivity = {
  active: boolean;
  download: {
    connector_id: string;
    status: string;
    done: number;
    total: number;
    items: number;
    current: string | null;
    error: string | null;
  } | null;
  reading: {
    total: number;
    done: number;
    active: number;
    pending: number;
    failed: number;
    memories: number;
    last_error: string | null;
    finished_at: string | null;
  } | null;
  indexing: { pending: number };
  generated_at: string;
};

function num(value: unknown): number {
  const parsed = typeof value === "bigint" ? Number(value) : Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

export async function GET(): Promise<Response> {
  try {
    const [downloadRows, readingRows, indexingRows] = await Promise.all([
      prisma.$queryRawUnsafe<Row[]>(
        `SELECT connector_id, status, progress, error, completed_at
           FROM connector_backfill_jobs
          WHERE status IN ('pending', 'in_progress', 'paused')
             OR completed_at > CURRENT_TIMESTAMP - INTERVAL '1 hour'
          ORDER BY (status IN ('pending', 'in_progress', 'paused')) DESC, updated_at DESC
          LIMIT 1`,
      ),
      prisma.$queryRawUnsafe<Row[]>(
        `SELECT count(*)::int AS total,
                count(*) FILTER (WHERE status = 'completed')::int AS done,
                count(*) FILTER (WHERE status = 'in_progress')::int AS active,
                count(*) FILTER (WHERE status = 'pending')::int AS pending,
                count(*) FILTER (WHERE status IN ('failed', 'cancelled'))::int AS failed,
                COALESCE(sum((result->>'memories_created')::int) FILTER (WHERE status = 'completed'), 0)::int AS memories,
                (SELECT error FROM ingestion_jobs
                  WHERE status = 'failed' AND updated_at > CURRENT_TIMESTAMP - INTERVAL '24 hours'
                  ORDER BY updated_at DESC LIMIT 1) AS last_error,
                max(completed_at) AS finished_at
           FROM ingestion_jobs
          WHERE status IN ('pending', 'in_progress')
             OR updated_at > CURRENT_TIMESTAMP - INTERVAL '24 hours'`,
      ),
      prisma.$queryRawUnsafe<Row[]>(
        `SELECT count(*)::int AS pending FROM memories WHERE embedding_status = 'pending'`,
      ),
    ]);

    const downloadRow = downloadRows[0];
    let download: BackgroundActivity["download"] = null;
    if (downloadRow) {
      const progress = (normalizeJsonValue(downloadRow.progress) ?? {}) as Row;
      // Connectors report progress in their own units: Gmail counts
      // messages seen and stored; a conversation-based import counts
      // conversations. Show whichever the job reports.
      const done = num(progress.conversations_done ?? progress.items_seen);
      const total = num(progress.conversations_total ?? progress.max_messages);
      download = {
        connector_id: String(downloadRow.connector_id ?? ""),
        status: String(downloadRow.status ?? ""),
        done,
        total,
        items: num(progress.items_stored),
        current: text(progress.current),
        error: text(downloadRow.error),
      };
    }

    const readingRow = readingRows[0];
    const reading: BackgroundActivity["reading"] =
      readingRow && num(readingRow.total) > 0
        ? {
            total: num(readingRow.total),
            done: num(readingRow.done),
            active: num(readingRow.active),
            pending: num(readingRow.pending),
            failed: num(readingRow.failed),
            memories: num(readingRow.memories),
            last_error: text(readingRow.last_error),
            finished_at:
              readingRow.finished_at instanceof Date
                ? readingRow.finished_at.toISOString()
                : text(readingRow.finished_at),
          }
        : null;

    const indexingPending = num(indexingRows[0]?.pending);
    const downloadActive = Boolean(
      download && ["pending", "in_progress", "paused"].includes(download.status),
    );
    const readingActive = Boolean(reading && reading.active + reading.pending > 0);

    const payload: BackgroundActivity = {
      active: downloadActive || readingActive,
      download,
      reading,
      indexing: { pending: indexingPending },
      generated_at: new Date().toISOString(),
    };
    return NextResponse.json(payload, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Failed to read background activity" },
      { status: 500 },
    );
  }
}
