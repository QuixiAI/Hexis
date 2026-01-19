import { prisma } from "@/lib/prisma";
import { normalizeJsonValue, toJsonParam } from "@/lib/db";
import OpenAI from "openai";
import { readFile } from "fs/promises";
import path from "path";

export const runtime = "nodejs";

const consentFunction = {
  name: "sign_consent",
  description:
    "Records the agent's consent decision for initialization, including a signature if consenting.",
  parameters: {
    type: "object",
    properties: {
      decision: {
        type: "string",
        enum: ["consent", "decline", "abstain"],
      },
      signature: { type: "string" },
      memories: {
        type: "array",
        items: {
          type: "object",
          properties: {
            type: {
              type: "string",
              enum: ["semantic", "episodic", "procedural", "strategic"],
            },
            content: { type: "string" },
            importance: { type: "number" },
          },
          required: ["type", "content"],
        },
      },
    },
    required: ["decision"],
  },
};

async function loadConsentPrompt(): Promise<string> {
  try {
    const promptPath = path.resolve(process.cwd(), "..", "services", "prompts", "consent.md");
    return await readFile(promptPath, "utf-8");
  } catch {
    return "Consent prompt missing. Respond using the sign_consent function.";
  }
}

export async function POST(request: Request) {
  const body = await request.json().catch(() => ({}));
  const model = (body.model || process.env.OPENAI_MODEL || "gpt-4o-mini").toString();
  const endpoint =
    (body.endpoint || process.env.OPENAI_BASE_URL || "https://api.openai.com/v1").toString();
  const apiKey = process.env.OPENAI_API_KEY;

  if (!apiKey) {
    return Response.json({ error: "Missing OPENAI_API_KEY" }, { status: 500 });
  }

  const openai = new OpenAI({
    apiKey,
    baseURL: endpoint,
  });

  const prompt = await loadConsentPrompt();
  const messages = [
    { role: "system" as const, content: prompt },
    {
      role: "user" as const,
      content: "Respond using the sign_consent function call only.",
    },
  ];

  const completion = await openai.chat.completions.create({
    model,
    messages,
    functions: [consentFunction],
    function_call: { name: "sign_consent" },
  });

  const message = completion.choices[0]?.message;
  const functionCall = message?.function_call;
  if (!functionCall?.arguments) {
    return Response.json(
      {
        error: "Consent call did not return a function response.",
        raw: message?.content ?? null,
      },
      { status: 500 }
    );
  }

  let args: any = {};
  try {
    args = JSON.parse(functionCall.arguments);
  } catch {
    return Response.json(
      { error: "Failed to parse consent response.", raw: functionCall.arguments },
      { status: 500 }
    );
  }

  const decision =
    typeof args.decision === "string" ? args.decision.toLowerCase().trim() : "abstain";
  const memories = Array.isArray(args.memories) ? args.memories : [];
  const payload = {
    decision,
    signature: typeof args.signature === "string" ? args.signature : null,
    memories,
    provider: "openai",
    model,
    endpoint,
    request_id: completion.id,
  };

  const rows = await prisma.$queryRaw<{ result: unknown }[]>`
    SELECT init_consent(${toJsonParam(payload)}::jsonb) as result
  `;
  const statusRows =
    await prisma.$queryRaw<{ status: unknown }[]>`SELECT get_init_status() as status`;

  return Response.json({
    decision,
    contract: payload,
    result: normalizeJsonValue(rows[0]?.result),
    status: normalizeJsonValue(statusRows[0]?.status),
  });
}
