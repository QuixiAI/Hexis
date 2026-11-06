import { NextResponse } from "next/server";

import { asRecord, catalogModels, declaredDefault } from "@/lib/model-catalog";
import { errorMessage, hexisApiHeaders, resolveHexisApiUrl } from "@/lib/python-api";

export const runtime = "nodejs";

// Models for one role's form. The Python API asks the provider's endpoint
// with the resolved credential (a typed, saved or environment key, or a
// sign-in). When the provider has no such list, the advisory models.dev
// catalog fills in, so the model field always has suggestions and stays free
// text either way.
export async function POST(request: Request) {
  const body = asRecord(await request.json().catch(() => ({})));
  const provider = typeof body.provider === "string" ? body.provider.trim() : "";
  if (!provider) {
    return NextResponse.json({ models: [], default: "", error: "Missing provider" }, { status: 400 });
  }
  let live: Record<string, unknown> = {};
  try {
    const res = await fetch(resolveHexisApiUrl("/api/init/llm/models"), {
      method: "POST",
      headers: hexisApiHeaders({ "content-type": "application/json" }),
      body: JSON.stringify(body),
      cache: "no-store",
    });
    live = asRecord(await res.json().catch(() => ({})));
    if (!res.ok) {
      const error = typeof live.error === "string" ? live.error : `Hexis API responded ${res.status}`;
      return NextResponse.json({ models: [], default: "", error }, { status: res.status });
    }
  } catch (err: unknown) {
    return NextResponse.json(
      { models: [], default: "", error: errorMessage(err, "Unable to reach the Hexis API.") },
      { status: 503 },
    );
  }
  const models = Array.isArray(live.models)
    ? live.models.filter((item: unknown): item is string => typeof item === "string")
    : [];
  if (models.length > 0) {
    return NextResponse.json({ ...live, models, default: await declaredDefault(provider, models), source: "endpoint" });
  }
  if (typeof live.error === "string" && live.error) {
    // The endpoint refused (a bad key, say): the error, not a stale catalog.
    return NextResponse.json({ ...live, models: [], default: "" });
  }
  const catalog = await catalogModels(provider);
  return NextResponse.json({ ...live, ...catalog, source: "catalog" });
}
