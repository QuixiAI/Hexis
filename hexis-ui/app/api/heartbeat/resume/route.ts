import { errorMessage, hexisApiHeaders, jsonProxyResponse, resolveHexisApiUrl } from "@/lib/python-api";

export const runtime = "nodejs";

export async function POST(): Promise<Response> {
  try {
    const upstream = await fetch(resolveHexisApiUrl("/api/heartbeat/resume"), {
      method: "POST",
      headers: hexisApiHeaders(),
      cache: "no-store",
    });
    return jsonProxyResponse(upstream, await upstream.text());
  } catch (error: unknown) {
    return Response.json({ error: errorMessage(error, "Unable to reach the Hexis API.") }, { status: 502 });
  }
}
