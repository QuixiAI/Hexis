import { NextResponse } from "next/server";

import { errorMessage, hexisApiHeaders, jsonProxyResponse, resolveHexisApiUrl } from "@/lib/python-api";

export const runtime = "nodejs";

// The Python API owns model setup: typed keys go to the auth store under
// HEXIS_HOME, which every service mounts, so nothing here touches files.
export async function POST(request: Request) {
  const body = await request.json().catch(() => ({}));
  try {
    const res = await fetch(resolveHexisApiUrl("/api/init/llm"), {
      method: "POST",
      headers: hexisApiHeaders({ "content-type": "application/json" }),
      body: JSON.stringify(body ?? {}),
      cache: "no-store",
    });
    return jsonProxyResponse(res, await res.text());
  } catch (err: unknown) {
    return NextResponse.json({ error: errorMessage(err, "Model setup request failed") }, { status: 500 });
  }
}
