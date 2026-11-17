import { NextResponse } from "next/server";
import { errorMessage, hexisApiHeaders, jsonProxyResponse, resolveHexisApiUrl } from "@/lib/python-api";

export const runtime = "nodejs";
type Context = { params: Promise<{ action?: string[] }> };

// GET: Drive sign-in, folders and import status. POST: the actions below.
const POST_ACTIONS = new Set(["connect", "disconnect", "folders", "folders/remove", "import"]);

async function proxy(request: Request, context: Context) {
  const action = (await context.params).action?.join("/") ?? "";
  const allowed = request.method === "GET" ? action === "" : POST_ACTIONS.has(action);
  if (!allowed) return NextResponse.json({ error: "Not found" }, { status: 404 });
  try {
    const res = await fetch(resolveHexisApiUrl(`/api/integrations/google_drive${action ? `/${action}` : ""}`), {
      method: request.method,
      headers: hexisApiHeaders({ "content-type": "application/json" }),
      ...(request.method === "POST" ? { body: JSON.stringify(await request.json().catch(() => ({}))) } : {}),
      cache: "no-store",
    });
    return jsonProxyResponse(res, await res.text());
  } catch (err) {
    return NextResponse.json({ error: errorMessage(err, "Google Drive request failed") }, { status: 500 });
  }
}

export const GET = proxy;
export const POST = proxy;
