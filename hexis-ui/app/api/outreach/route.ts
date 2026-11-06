import { NextResponse } from "next/server";
import { errorMessage, hexisApiHeaders, jsonProxyResponse, resolveHexisApiUrl } from "@/lib/python-api";

export const runtime = "nodejs";

// Where the agent's messages to the user go (core/outreach_destination.py).
async function proxy(request: Request) {
  try {
    const res = await fetch(resolveHexisApiUrl("/api/outreach"), {
      method: request.method,
      headers: hexisApiHeaders({ "content-type": "application/json" }),
      ...(request.method === "POST" ? { body: JSON.stringify(await request.json().catch(() => ({}))) } : {}),
      cache: "no-store",
    });
    return jsonProxyResponse(res, await res.text());
  } catch (err) {
    return NextResponse.json({ error: errorMessage(err, "Outreach request failed") }, { status: 500 });
  }
}

export const GET = proxy;
export const POST = proxy;
