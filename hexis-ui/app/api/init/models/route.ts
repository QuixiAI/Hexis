// Advisory model catalog for a provider (models.dev, cached), used by the
// init wizard when the provider's own endpoint has no model list.

import { catalogModels } from "@/lib/model-catalog";

export const runtime = "nodejs";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const provider = (searchParams.get("provider") || "").trim();
  if (!provider) {
    return Response.json({ models: [], default: "", error: "Missing provider" }, { status: 400 });
  }
  return Response.json(await catalogModels(provider));
}
