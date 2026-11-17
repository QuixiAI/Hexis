import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const queryRawUnsafe = vi.fn();
vi.mock("@/lib/prisma", () => ({
  prisma: { $queryRawUnsafe: (...args: unknown[]) => queryRawUnsafe(...args) },
}));

import { GET, POST } from "./route";
import { SEARCH_PROVIDERS, searchConfigPlaceholder } from "@/lib/search-providers";

function request(body: Record<string, unknown>) {
  return new NextRequest("http://localhost/api/settings/tools/search", {
    method: "POST",
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json" },
  });
}

function savedConfig() {
  const call = queryRawUnsafe.mock.calls.find((args) => String(args[0]).includes("'Tool system configuration'"));
  expect(call).toBeDefined();
  return JSON.parse(call![1]);
}

beforeEach(() => {
  queryRawUnsafe.mockReset();
  queryRawUnsafe.mockResolvedValue([]);
});

describe("web search setup", () => {
  it("offers Keenable in the same provider list the chat form uses", async () => {
    const response = await GET();
    const payload = await response.json();
    expect(payload.providers).toEqual(SEARCH_PROVIDERS);
    expect(payload.providers).toContainEqual(expect.objectContaining({
      id: "keenable", requires_credential: false,
    }));
    expect(searchConfigPlaceholder("keenable")).toContain("Optional");
  });

  it("selects and enables keyless Keenable without requiring or inventing credentials", async () => {
    queryRawUnsafe.mockResolvedValueOnce([{ value: { disabled: ["web_search", "other"], enabled: ["recall"] } }]);
    const response = await POST(request({ provider: "keenable", enable: true }));
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ provider: "keenable", key_source: "none" });
    expect(savedConfig()).toMatchObject({
      api_keys: {}, web_search: { provider: "keenable" }, disabled: ["other"], enabled: ["recall", "web_search"],
    });
  });

  it.each([
    { key_ref: "env:KEENABLE_API_KEY" },
    { api_key: "test-key" },
  ])("routes an optional Keenable key without overwriting Tavily", async (credential) => {
    queryRawUnsafe.mockResolvedValueOnce([{ value: { api_keys: { tavily: "env:TAVILY_API_KEY" } } }]);
    const response = await POST(request({ provider: "keenable", ...credential }));
    expect(response.status).toBe(200);
    expect(savedConfig().api_keys).toEqual({
      tavily: "env:TAVILY_API_KEY", keenable: Object.values(credential)[0],
    });
  });

  it.each(["tavily", "brave"])("still requires credentials for %s", async (provider) => {
    const response = await POST(request({ provider }));
    expect(response.status).toBe(400);
    expect(queryRawUnsafe).not.toHaveBeenCalled();
  });

  it("requires a URL for SearXNG and rejects unknown providers", async () => {
    expect((await POST(request({ provider: "searxng" }))).status).toBe(400);
    expect((await POST(request({ provider: "unknown" }))).status).toBe(400);
  });
});
