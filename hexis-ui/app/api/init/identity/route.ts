import { prisma } from "@/lib/prisma";
import { normalizeJsonValue } from "@/lib/db";

export const runtime = "nodejs";

export async function POST(request: Request) {
  const body = await request.json().catch(() => ({}));
  const name = typeof body.name === "string" ? body.name : null;
  const pronouns = typeof body.pronouns === "string" ? body.pronouns : null;
  const voice = typeof body.voice === "string" ? body.voice : null;
  const description = typeof body.description === "string" ? body.description : null;
  const purpose = typeof body.purpose === "string" ? body.purpose : null;
  const creatorName = typeof body.creator_name === "string" ? body.creator_name : null;

  const rows = await prisma.$queryRaw<{ result: unknown }[]>`
    SELECT init_identity(
      ${name}::text,
      ${pronouns}::text,
      ${voice}::text,
      ${description}::text,
      ${purpose}::text,
      ${creatorName}::text
    ) as result
  `;
  const statusRows =
    await prisma.$queryRaw<{ status: unknown }[]>`SELECT get_init_status() as status`;

  return Response.json({
    result: normalizeJsonValue(rows[0]?.result),
    status: normalizeJsonValue(statusRows[0]?.status),
  });
}
