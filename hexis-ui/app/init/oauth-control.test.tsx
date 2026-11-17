import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OAuthControl, SetupTokenControl } from "./oauth-control";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("OAuthControl", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("logs in with a one-time code and hands the account to the form once approved", async () => {
    const onUse = vi.fn();
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      calls.push(url);
      if (url.includes("/auth/status")) return jsonResponse({ provider: "openai-codex", configured: false });
      if (url.endsWith("/auth/start"))
        return jsonResponse({
          session_id: "s1", provider: "openai-codex", flow: "device_code", status: "waiting_for_user",
          expires_in_seconds: 900, verification_uri: "https://auth.openai.com/codex/device", user_code: "ABCD-1234",
        });
      if (url.includes("/auth/session/s1"))
        return jsonResponse({
          session_id: "s1", provider: "openai-codex", flow: "device_code", status: "complete",
          expires_in_seconds: 800, credential: { provider: "openai-codex", configured: true, email: "person@example.test" },
        });
      throw new Error(`unexpected ${url}`);
    });
    const opened = vi.fn();
    vi.stubGlobal("open", opened);

    render(<OAuthControl provider="openai-codex" active={false} onUse={onUse} onLeave={() => undefined} />);
    fireEvent.click(await screen.findByRole("button", { name: "Log in with ChatGPT" }));
    expect(await screen.findByText("ABCD-1234")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Copy code and open OpenAI" }));
    expect(opened).toHaveBeenCalledWith("https://auth.openai.com/codex/device", "_blank", "noopener,noreferrer");
    await waitFor(() => expect(onUse).toHaveBeenCalledOnce(), { timeout: 4000 });
    expect(calls.some((url) => url.includes("/auth/session/s1"))).toBe(true);
  });

  it("completes a browser sign-in from a pasted redirect URL", async () => {
    const onUse = vi.fn();
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ provider: "google-gemini-cli", configured: false }))
      .mockResolvedValueOnce(jsonResponse({
        session_id: "s2", provider: "google-gemini-cli", flow: "authorization_code", status: "awaiting_code",
        expires_in_seconds: 600, authorization_url: "https://example.test/authorize", callback_active: false,
      }))
      .mockResolvedValueOnce(jsonResponse({
        session_id: "s2", provider: "google-gemini-cli", flow: "authorization_code", status: "complete",
        expires_in_seconds: 590, credential: { provider: "google-gemini-cli", configured: true, email: "person@example.test" },
      }));

    render(<OAuthControl provider="google-gemini-cli" active={false} onUse={onUse} onLeave={() => undefined} />);
    fireEvent.click(await screen.findByRole("button", { name: "Log in with Google (Gemini CLI)" }));
    const link = await screen.findByRole("link", { name: "Continue to Google" });
    expect(link).toHaveAttribute("href", "https://example.test/authorize");
    fireEvent.change(screen.getByLabelText("Authorization code or redirect URL"), { target: { value: "code#state" } });
    fireEvent.click(screen.getByRole("button", { name: "Complete sign-in" }));
    await waitFor(() => expect(onUse).toHaveBeenCalledOnce());
  });

  it("reuses an existing login without a new sign-in and can go back to an API key", async () => {
    const onUse = vi.fn();
    const onLeave = vi.fn();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ provider: "xai-oauth", configured: true, expires_in_seconds: 3600 }),
    );
    const { rerender } = render(<OAuthControl provider="xai-oauth" active={false} onUse={onUse} onLeave={onLeave} />);
    fireEvent.click(await screen.findByRole("button", { name: "Use your xAI login" }));
    expect(onUse).toHaveBeenCalledOnce();
    rerender(<OAuthControl provider="xai-oauth" active onUse={onUse} onLeave={onLeave} />);
    expect(await screen.findByText("Logged in with your xAI account")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Use an API key instead" }));
    expect(onLeave).toHaveBeenCalledOnce();
  });

  it("surfaces an actionable provider error in place", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ provider: "chutes", configured: false }))
      .mockResolvedValueOnce(jsonResponse({ detail: "Enter the OAuth client ID from your Chutes developer settings." }, 400));

    render(<OAuthControl provider="chutes" active={false} onUse={() => undefined} />);
    fireEvent.click(await screen.findByRole("button", { name: "Log in with Chutes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter the OAuth client ID from your Chutes developer settings.",
    );
    // Login-only providers offer no API key alternative.
    expect(screen.queryByRole("button", { name: "Use an API key instead" })).toBeNull();
  });
});

describe("SetupTokenControl", () => {
  afterEach(() => vi.restoreAllMocks());

  it("saves a pasted Claude Code setup token and switches the form to it", async () => {
    const onUse = vi.fn();
    const bodies: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/auth/status")) return jsonResponse({ provider: "anthropic", configured: false });
      if (url.endsWith("/auth/setup-token")) {
        bodies.push(String(init?.body));
        return jsonResponse({ provider: "anthropic", configured: true });
      }
      throw new Error(`unexpected ${url}`);
    });
    render(<SetupTokenControl active={false} onUse={onUse} onLeave={() => undefined} />);
    fireEvent.click(await screen.findByRole("button", { name: "Use a Claude subscription" }));
    fireEvent.change(screen.getByLabelText("Claude Code setup token"), {
      target: { value: "sk-ant-oat01-" + "x".repeat(80) },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save token" }));
    await waitFor(() => expect(onUse).toHaveBeenCalledOnce());
    expect(JSON.parse(bodies[0])).toMatchObject({ provider: "anthropic" });
  });
});
