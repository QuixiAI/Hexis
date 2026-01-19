import { prisma } from "@/lib/prisma";
import { normalizeJsonValue, toJsonParam } from "@/lib/db";

export const runtime = "nodejs";

export async function POST(request: Request) {
  const body = await request.json().catch(() => ({}));
  const interests = body.interests ?? null;

  const rows = await prisma.$queryRaw<{ result: unknown }[]>`
    SELECT init_interests(${toJsonParam(interests)}::jsonb) as result
  `;
  const statusRows =
    await prisma.$queryRaw<{ status: unknown }[]>`SELECT get_init_status() as status`;

  return Response.json({
    result: normalizeJsonValue(rows[0]?.result),
    status: normalizeJsonValue(statusRows[0]?.status),
  });
}
