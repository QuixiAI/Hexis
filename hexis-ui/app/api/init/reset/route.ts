import { prisma } from "@/lib/prisma";
import { normalizeJsonValue } from "@/lib/db";

export const runtime = "nodejs";

export async function POST() {
  const rows =
    await prisma.$queryRaw<{ result: unknown }[]>`SELECT reset_initialization() as result`;

  return Response.json({
    result: normalizeJsonValue(rows[0]?.result),
  });
}
