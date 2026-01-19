import { prisma } from "@/lib/prisma";
import { normalizeJsonValue } from "@/lib/db";

export const runtime = "nodejs";

export async function GET() {
  const statusRows =
    await prisma.$queryRaw<{ status: unknown }[]>`SELECT get_init_status() as status`;
  const modeRows =
    await prisma.$queryRaw<{ mode: unknown }[]>`SELECT get_config('agent.mode') as mode`;
  const profileRows =
    await prisma.$queryRaw<{ profile: unknown }[]>`SELECT get_config('agent.init_profile') as profile`;
  const consentRows =
    await prisma.$queryRaw<{ consent: string | null }[]>`SELECT get_agent_consent_status() as consent`;
  const configuredRows =
    await prisma.$queryRaw<{ configured: boolean | null }[]>`SELECT is_agent_configured() as configured`;
  const llmRows =
    await prisma.$queryRaw<{ llm: unknown }[]>`SELECT get_config('llm.heartbeat') as llm`;
  const llmConfig = normalizeJsonValue(llmRows[0]?.llm) as any;
  const provider = typeof llmConfig?.provider === "string" ? llmConfig.provider : null;
  const model = typeof llmConfig?.model === "string" ? llmConfig.model : null;
  const endpoint = typeof llmConfig?.endpoint === "string" ? llmConfig.endpoint : null;
  const consentRecordRows = await prisma.$queryRaw<
    {
      decision: string;
      signature: string | null;
      provider: string | null;
      model: string | null;
      endpoint: string | null;
      decided_at: string;
    }[]
  >`SELECT decision, signature, provider, model, endpoint, decided_at
    FROM consent_log
    WHERE (${provider}::text IS NULL OR provider = ${provider}::text)
      AND (${model}::text IS NULL OR model = ${model}::text)
      AND (${endpoint}::text IS NULL OR endpoint = ${endpoint}::text)
    ORDER BY decided_at DESC
    LIMIT 1`;

  const status = normalizeJsonValue(statusRows[0]?.status) ?? {};
  const mode = normalizeJsonValue(modeRows[0]?.mode);
  const profile = normalizeJsonValue(profileRows[0]?.profile) ?? {};
  const consentStatus = consentRows[0]?.consent ?? null;
  const configured = Boolean(configuredRows[0]?.configured);
  const consentRecord = consentRecordRows[0] ?? null;

  return Response.json({
    status,
    mode,
    profile,
    consent_status: consentStatus,
    configured,
    consent_record: consentRecord,
  });
}
