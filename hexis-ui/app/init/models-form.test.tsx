import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { defaultLlmConfig, llmConfigFromSaved, ModelsForm, providerOrder } from "./models-form";

afterEach(() => vi.unstubAllGlobals());

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });

it("lists one entry per vendor: subscription logins live inside their vendor's form", () => {
  for (const hidden of ["openai-codex", "xai-oauth", "google-gemini-cli", "google-antigravity", "qwen-portal", "minimax-portal"])
    expect(providerOrder).not.toContain(hidden);
  for (const shown of ["openai", "anthropic", "gemini", "grok", "qwen", "minimax", "chutes", "github-copilot", "openrouter",
    "bedrock", "vertex", "azure_openai", "openai_compatible"])
    expect(providerOrder).toContain(shown);
  // A saved subscription provider shows under its vendor.
  expect(llmConfigFromSaved({ provider: "openai-codex", model: "m" }, defaultLlmConfig("openai")).provider).toBe("openai-codex");
});

it("logs in with ChatGPT from the OpenAI form and saves the account as the Codex provider", async () => {
  let connected = false;
  const saved: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (url.includes("/auth/status")) return json({ provider: "openai-codex", configured: false });
    if (url.endsWith("/auth/start"))
      return json({ session_id: "s", provider: "openai-codex", flow: "device_code", status: "waiting_for_user",
        expires_in_seconds: 900, verification_uri: "https://auth.openai.com/codex/device", user_code: "ABCD-1234" });
    if (url.includes("/auth/session/")) {
      connected = true;
      return json({ session_id: "s", provider: "openai-codex", flow: "device_code", status: "complete",
        expires_in_seconds: 800, credential: { provider: "openai-codex", configured: true } });
    }
    if (url.endsWith("/llm/models"))
      return json({ models: connected ? ["account-model"] : [], default: connected ? "account-model" : "", key_source: connected ? "oauth" : null });
    if (url === "/api/init/llm") { saved.push(JSON.parse(options!.body as string)); return json({ status: { stage: "llm" }, config: {} }); }
    return json({});
  }));
  vi.stubGlobal("open", vi.fn());
  const onSaved = vi.fn();
  const config = defaultLlmConfig("openai");
  render(<ModelsForm initialConscious={config} initialSubconscious={config} onSaved={onSaved} />);
  // Codex is not a provider of its own; it is the login inside the OpenAI form.
  expect(screen.queryByRole("option", { name: /Codex/ })).toBeNull();
  expect(screen.getByLabelText("API key")).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "Log in with ChatGPT" }));
  expect(await screen.findByText("ABCD-1234")).toBeInTheDocument();
  await screen.findByText("Logged in with your ChatGPT account", undefined, { timeout: 4000 });
  expect(screen.queryByLabelText("API key")).toBeNull();
  expect(screen.getByLabelText("Provider")).toHaveValue("openai");
  await waitFor(() => expect(screen.getByLabelText("Model")).toHaveValue("account-model"));
  fireEvent.click(screen.getByRole("button", { name: "Save Models" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  expect(saved[0]).toMatchObject({ reuse_primary: true, conscious: { provider: "openai-codex", model: "account-model", api_key: "" } });

  fireEvent.click(screen.getByRole("button", { name: "Use an API key instead" }));
  expect(screen.getByLabelText("API key")).toBeInTheDocument();
  expect(screen.getByLabelText("Provider")).toHaveValue("openai");
});

it("asks the server to reuse the saved key when the background model follows the primary", async () => {
  const saved: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (url === "/api/init/llm") { saved.push(JSON.parse(options!.body as string)); return json({ status: {}, config: {} }); }
    if (url.endsWith("/llm/models")) return json({ models: [], default: "", key_source: "stored" });
    return json({ configured: false });
  }));
  const onSaved = vi.fn();
  render(<ModelsForm
    initialConscious={{ ...defaultLlmConfig("openrouter"), model: "primary" }}
    initialSubconscious={{ ...defaultLlmConfig("anthropic"), model: "background" }}
    onSaved={onSaved}
  />);
  await screen.findAllByText("A key is already saved from an earlier setup; leave blank to keep it.");
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Save Models" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  expect(saved).toEqual([{
    conscious: { provider: "openrouter", model: "primary", endpoint: "https://openrouter.ai/api/v1", api_key: "" },
    subconscious: { provider: "openrouter", model: "primary", endpoint: "https://openrouter.ai/api/v1", api_key: "" },
    reuse_primary: true,
  }]);
});

it("lists models once per endpoint and key, not on every blur, and not before an endpoint", async () => {
  const listed: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith("/llm/models")) listed.push(JSON.parse(options!.body as string));
    return json({ models: ["m1"], default: "m1", key_source: null });
  }));
  const local = { ...defaultLlmConfig("openai_compatible"), endpoint: "" };
  render(<ModelsForm initialConscious={local} initialSubconscious={local} onSaved={vi.fn()} />);
  await screen.findByText("Enter the endpoint to list its models.");
  expect(listed).toHaveLength(0);

  const endpoint = screen.getByPlaceholderText("https://...");
  fireEvent.change(endpoint, { target: { value: "http://gateway:4000/v1" } });
  fireEvent.blur(endpoint);
  await waitFor(() => expect(listed).toHaveLength(1));
  fireEvent.blur(endpoint);
  fireEvent.focus(endpoint);
  fireEvent.blur(endpoint);
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(listed).toHaveLength(1);
});

it("keeps a stored Claude setup token as the Anthropic credential and saves without a key", async () => {
  const saved: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (url.includes("/auth/status")) return json({ provider: "anthropic", configured: true });
    if (url.endsWith("/llm/models")) return json({ models: ["claude-sonnet-4-5"], default: "claude-sonnet-4-5", key_source: "token" });
    if (url === "/api/init/llm") { saved.push(JSON.parse(options!.body as string)); return json({ status: {}, config: {} }); }
    return json({});
  }));
  const onSaved = vi.fn();
  const config = defaultLlmConfig("anthropic");
  render(<ModelsForm initialConscious={config} initialSubconscious={config} onSaved={onSaved} />);
  expect(await screen.findByText("Using your Claude subscription (setup token)")).toBeInTheDocument();
  expect(screen.queryByLabelText("API key")).toBeNull();
  await waitFor(() => expect(screen.getByLabelText("Model")).toHaveValue("claude-sonnet-4-5"));
  fireEvent.click(screen.getByRole("button", { name: "Save Models" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  expect(saved[0]).toMatchObject({ conscious: { provider: "anthropic", model: "claude-sonnet-4-5", api_key: "" } });
});

it("names what is still missing before saving", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ models: [], default: "", key_source: null, configured: false })));
  const config = defaultLlmConfig("vertex");
  render(<ModelsForm initialConscious={config} initialSubconscious={config} onSaved={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Save Models" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Still needed: conscious model, conscious Google Cloud project");
});
