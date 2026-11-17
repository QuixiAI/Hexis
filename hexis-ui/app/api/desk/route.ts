import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { normalizeJsonValue } from "@/lib/db";

type Row = Record<string, unknown>;

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Desk request failed";
}

export async function GET(req: NextRequest) {
  try {
    const url = new URL(req.url);
    const limit = Math.min(parseInt(url.searchParams.get("limit") || "50", 10), 200);
    const offset = parseInt(url.searchParams.get("offset") || "0", 10);
    const pinnedOnly = url.searchParams.get("pinned") === "true";
    const documentId = url.searchParams.get("document_id") || null;
    const unitId = url.searchParams.get("open") || null;

    if (unitId) {
      const openOffset = parseInt(url.searchParams.get("open_offset") || "0", 10);
      const chars = Math.min(parseInt(url.searchParams.get("chars") || "6000", 10), 40000);
      const rows = await prisma.$queryRawUnsafe<Row[]>(
        `SELECT open_recmem_desk_item($1::uuid, $2::int, $3::int) AS payload`,
        unitId, openOffset, chars
      );
      return NextResponse.json(normalizeJsonValue(rows[0]?.payload));
    }

    const items = await prisma.$queryRawUnsafe<Row[]>(
      `SELECT * FROM list_recmem_desk($1::int, $2::int, $3::uuid, $4::boolean)`,
      limit, offset, documentId, pinnedOnly
    );
    return NextResponse.json({
      // total_count is a bigint, which JSON cannot carry; it is sent as total.
      items: items.map(({ total_count, ...i }) => ({
        ...i,
        locator: normalizeJsonValue(i.locator),
        char_count: i.char_count != null ? Number(i.char_count) : null,
        access_count: i.access_count != null ? Number(i.access_count) : null,
        total: total_count != null ? Number(total_count) : null,
      })),
      total: items.length ? Number(items[0].total_count || 0) : 0,
      limit,
      offset,
    });
  } catch (error: unknown) {
    console.error("Desk API error:", error);
    return NextResponse.json({ error: errorMessage(error) }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const action = String(body.action || "");

    if (action === "load") {
      const rows = await prisma.$queryRawUnsafe<Row[]>(
        `SELECT load_source_chunks_to_recmem(
             $1::uuid[], $2::uuid, NULL, NULL, $3::int, $4::int,
             50, false, $5::text, NULL, 'ui', NULL, $6::boolean
         ) AS payload`,
        body.chunk_ids?.length ? body.chunk_ids : null,
        body.document_id || null,
        body.page_start ?? null,
        body.page_end ?? null,
        body.reason || null,
        Boolean(body.pin)
      );
      const payload = normalizeJsonValue(rows[0]?.payload) as Record<string, unknown>;
      // Whole-document fallback when no chunks exist for it yet.
      if (
        body.document_id &&
        !body.chunk_ids?.length &&
        Number(payload?.count || 0) === 0
      ) {
        const docRows = await prisma.$queryRawUnsafe<Row[]>(
          `SELECT load_source_documents_to_recmem($1::uuid[]) AS payload`,
          [body.document_id]
        );
        return NextResponse.json(normalizeJsonValue(docRows[0]?.payload));
      }
      return NextResponse.json(payload);
    }

    if (action === "pin" || action === "unpin") {
      const rows = await prisma.$queryRawUnsafe<Row[]>(
        `SELECT pin_recmem_desk_item($1::uuid, $2::boolean, 'ui') AS payload`,
        body.desk_unit_id,
        action === "pin"
      );
      return NextResponse.json(normalizeJsonValue(rows[0]?.payload));
    }

    if (action === "clear") {
      const rows = await prisma.$queryRawUnsafe<Row[]>(
        `SELECT clear_recmem_desk($1::uuid[], $2::uuid, NULL, NULL, $3::boolean, $4::boolean) AS payload`,
        body.desk_unit_ids?.length ? body.desk_unit_ids : null,
        body.document_id || null,
        Boolean(body.all),
        Boolean(body.include_pinned)
      );
      return NextResponse.json(normalizeJsonValue(rows[0]?.payload));
    }

    return NextResponse.json({ error: `unknown action: ${action}` }, { status: 422 });
  } catch (error: unknown) {
    console.error("Desk API error:", error);
    return NextResponse.json({ error: errorMessage(error) }, { status: 500 });
  }
}
