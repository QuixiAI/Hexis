import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { normalizeJsonValue } from "@/lib/db";

export const runtime = "nodejs";

/**
 * How long raw transcripts are kept (db: run_transcript_retention, run daily by
 * the maintenance worker). Memories have their own fading rules and are not
 * affected. A limit of 0 days keeps that kind forever.
 */
export type RetentionSettings = {
  enabled: boolean;
  turn_trace_days: number;
  conversation_days: number;
  raw_turn_days: number;
  last_run: string | null;
};

const DAY_KEYS = ["turn_trace_days", "conversation_days", "raw_turn_days"] as const;
const DEFAULTS = { turn_trace_days: 30, conversation_days: 365, raw_turn_days: 365 };
const MAX_DAYS = 36500;

async function load(): Promise<RetentionSettings> {
  const rows = await prisma.$queryRawUnsafe<{ key: string; value: unknown }[]>(
    `SELECT key, value FROM config WHERE key IN (
       'retention.transcripts_enabled', 'retention.turn_trace_days', 'retention.conversation_days',
       'retention.raw_turn_days', 'retention.transcripts_last_run')`,
  );
  const values = new Map(rows.map((row) => [row.key, normalizeJsonValue(row.value)]));
  const days = (key: (typeof DAY_KEYS)[number]) => {
    const value = Number(values.get(`retention.${key}`));
    return Number.isFinite(value) && value >= 0 ? Math.floor(value) : DEFAULTS[key];
  };
  const lastRun = values.get("retention.transcripts_last_run");
  return {
    enabled: values.get("retention.transcripts_enabled") !== false,
    turn_trace_days: days("turn_trace_days"),
    conversation_days: days("conversation_days"),
    raw_turn_days: days("raw_turn_days"),
    last_run: typeof lastRun === "string" ? lastRun : null,
  };
}

async function setConfig(key: string, value: unknown) {
  await prisma.$queryRawUnsafe(
    `INSERT INTO config (key, value, updated_at) VALUES ($1, $2::jsonb, CURRENT_TIMESTAMP)
     ON CONFLICT (key) DO UPDATE SET value = $2::jsonb, updated_at = CURRENT_TIMESTAMP`,
    key,
    JSON.stringify(value),
  );
}

export async function GET(): Promise<Response> {
  try {
    return NextResponse.json(await load());
  } catch (error: unknown) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "Failed to load" }, { status: 500 });
  }
}

export async function POST(req: NextRequest): Promise<Response> {
  try {
    const body = (await req.json().catch(() => ({}))) as Partial<RetentionSettings>;
    if (body.enabled !== undefined) {
      if (typeof body.enabled !== "boolean") return NextResponse.json({ error: "enabled must be true or false" }, { status: 400 });
      await setConfig("retention.transcripts_enabled", body.enabled);
    }
    for (const key of DAY_KEYS) {
      const value = body[key];
      if (value === undefined) continue;
      if (typeof value !== "number" || !Number.isInteger(value) || value < 0 || value > MAX_DAYS) {
        return NextResponse.json({ error: `${key} must be a whole number of days from 0 to ${MAX_DAYS}` }, { status: 400 });
      }
      await setConfig(`retention.${key}`, value);
    }
    return NextResponse.json(await load());
  } catch (error: unknown) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "Failed to save" }, { status: 500 });
  }
}
