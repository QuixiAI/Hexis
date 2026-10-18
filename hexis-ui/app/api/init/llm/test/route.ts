import { NextResponse } from "next/server";

import { errorMessage, hexisApiHeaders, jsonProxyResponse, resolveHexisApiUrl } from "@/lib/python-api";

export const runtime = "nodejs";

// One tiny real call with the form's provider, model and credential. The
// Python API resolves saved keys and sign-ins; the form never sees them.
export async function POST(request: Request) {
  const body = await request.json().catch(() => ({}));
  try {
    const res = await fetch(resolveHexisApiUrl("/api/init/llm/test"), {
      method: "POST",
      headers: hexisApiHeaders({ "content-type": "application/json" }),
      body: JSON.stringify(body ?? {}),
      cache: "no-store",
    });
    return jsonProxyResponse(res, await res.text());
  } catch (err: unknown) {
    return NextResponse.json({ error: errorMessage(err, "Connection test failed") }, { status: 500 });
  }
}
