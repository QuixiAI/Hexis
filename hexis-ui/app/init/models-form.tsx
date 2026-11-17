"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ACCOUNT_LABEL, OAuthControl, SetupTokenControl, type OAuthProvider } from "./oauth-control";

/**
 * The Models step: one entry per vendor. A vendor's form takes an API key
 * and, where the vendor has one, offers its subscription login instead:
 * ChatGPT under OpenAI, SuperGrok / X under Grok, Google under Gemini, Qwen
 * Portal under Alibaba Qwen, MiniMax Portal under MiniMax, a Claude Code setup
 * token under Anthropic. Chutes and GitHub Copilot are login-only.
 *
 * Model lists and default models are not hardcoded here: they come live from
 * /api/init/llm/models (the endpoint's own list, else the models.dev catalog).
 */

export type LlmProvider =
  // API keys
  | "openai"
  | "anthropic"
  | "gemini"
  | "grok"
  | "openrouter"
  | "deepseek"
  | "qwen"
  | "qwen_coding"
  | "zai"
  | "minimax"
  | "meta"
  // Enterprise clouds
  | "azure_openai"
  | "bedrock"
  | "vertex"
  // Login-only
  | "chutes"
  | "github-copilot"
  // Subscription logins chosen from a vendor's form (not listed themselves)
  | "openai-codex"
  | "xai-oauth"
  | "google-gemini-cli"
  | "google-antigravity"
  | "qwen-portal"
  | "minimax-portal"
  // A server of your own
  | "openai_compatible";

export type LlmRole = "conscious" | "subconscious";

export type LlmConfig = {
  provider: LlmProvider;
  model: string;
  endpoint: string;
  apiKey: string;
};

type ProviderMeta = {
  label: string;
  endpoint: string;
  apiKeyRequired: boolean;
  // Chosen from this vendor's form; never listed as a provider of its own.
  accounts?: OAuthProvider[];
  // Sign-in only: no API key field.
  loginOnly?: boolean;
};

export const providerMeta: Record<LlmProvider, ProviderMeta> = {
  openai: { label: "OpenAI", endpoint: "https://api.openai.com/v1", apiKeyRequired: true, accounts: ["openai-codex"] },
  anthropic: { label: "Anthropic", endpoint: "", apiKeyRequired: true },
  gemini: { label: "Google Gemini", endpoint: "", apiKeyRequired: true, accounts: ["google-gemini-cli", "google-antigravity"] },
  grok: { label: "Grok (xAI)", endpoint: "https://api.x.ai/v1", apiKeyRequired: true, accounts: ["xai-oauth"] },
  openrouter: { label: "OpenRouter", endpoint: "https://openrouter.ai/api/v1", apiKeyRequired: true },
  deepseek: { label: "DeepSeek", endpoint: "https://api.deepseek.com", apiKeyRequired: true },
  qwen: { label: "Alibaba Qwen", endpoint: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", apiKeyRequired: true, accounts: ["qwen-portal"] },
  qwen_coding: { label: "Alibaba Qwen Coding Plan", endpoint: "https://coding-intl.dashscope.aliyuncs.com/v1", apiKeyRequired: true },
  zai: { label: "Z.ai (GLM)", endpoint: "https://api.z.ai/api/paas/v4", apiKeyRequired: true },
  minimax: { label: "MiniMax", endpoint: "https://api.minimax.io/v1", apiKeyRequired: true, accounts: ["minimax-portal"] },
  meta: { label: "Meta Muse", endpoint: "https://api.meta.ai/v1", apiKeyRequired: true },
  // Enterprise clouds. The endpoint holds the Azure resource URL, the AWS
  // region, or the Vertex project and location; Bedrock and Vertex can use the
  // server's own cloud credentials, so their key is optional.
  azure_openai: { label: "Azure OpenAI", endpoint: "", apiKeyRequired: true },
  bedrock: { label: "Amazon Bedrock", endpoint: "us-east-1", apiKeyRequired: false },
  vertex: { label: "Google Vertex AI", endpoint: "", apiKeyRequired: false },
  chutes: { label: "Chutes", endpoint: "https://api.chutes.ai/v1", apiKeyRequired: false, loginOnly: true },
  "github-copilot": { label: "GitHub Copilot", endpoint: "", apiKeyRequired: false, loginOnly: true },
  "openai-codex": { label: "OpenAI", endpoint: "", apiKeyRequired: false },
  "xai-oauth": { label: "Grok (xAI)", endpoint: "https://api.x.ai/v1", apiKeyRequired: false },
  "google-gemini-cli": { label: "Google Gemini", endpoint: "https://cloudcode-pa.googleapis.com", apiKeyRequired: false },
  "google-antigravity": { label: "Google Gemini", endpoint: "https://cloudcode-pa.googleapis.com", apiKeyRequired: false },
  "qwen-portal": { label: "Alibaba Qwen", endpoint: "https://portal.qwen.ai/v1", apiKeyRequired: false },
  "minimax-portal": { label: "MiniMax", endpoint: "https://api.minimax.io/anthropic", apiKeyRequired: false },
  // The API container makes the call, so "this machine" is host.docker.internal.
  openai_compatible: {
    label: "Local / Custom (OpenAI-compatible: vLLM, llama.cpp, LM Studio, Ollama)",
    endpoint: "http://host.docker.internal:8000/v1",
    apiKeyRequired: false,
  },
};

// Which vendor's form a subscription login belongs to.
const ACCOUNT_OF: Partial<Record<LlmProvider, LlmProvider>> = {
  "openai-codex": "openai",
  "xai-oauth": "grok",
  "google-gemini-cli": "gemini",
  "google-antigravity": "gemini",
  "qwen-portal": "qwen",
  "minimax-portal": "minimax",
};
export const providerOrder = (Object.keys(providerMeta) as LlmProvider[]).filter((p) => !ACCOUNT_OF[p]);
export const providerChoice = (provider: LlmProvider): LlmProvider => ACCOUNT_OF[provider] ?? provider;
const isAccount = (provider: LlmProvider): provider is LlmProvider & OAuthProvider => Boolean(ACCOUNT_OF[provider]);
export const providerLabel = (provider: LlmProvider): string => providerMeta[provider]?.label ?? provider;

// Region and plan for providers that serve the same API from several hosts.
const ENDPOINT_OPTIONS: Partial<Record<LlmProvider, { label: string; url: string }[]>> = {
  zai: [
    { label: "Global (pay as you go)", url: "https://api.z.ai/api/paas/v4" },
    { label: "GLM Coding Plan (Global)", url: "https://api.z.ai/api/coding/paas/v4" },
    { label: "China (pay as you go)", url: "https://open.bigmodel.cn/api/paas/v4" },
    { label: "GLM Coding Plan (China)", url: "https://open.bigmodel.cn/api/coding/paas/v4" },
  ],
  qwen: [
    { label: "International (Singapore)", url: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1" },
    { label: "Mainland China (Beijing)", url: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
  ],
  qwen_coding: [
    { label: "International (Singapore)", url: "https://coding-intl.dashscope.aliyuncs.com/v1" },
    { label: "Mainland China (Beijing)", url: "https://coding.dashscope.aliyuncs.com/v1" },
  ],
  minimax: [
    { label: "International", url: "https://api.minimax.io/v1" },
    { label: "China", url: "https://api.minimaxi.com/v1" },
  ],
};

// Model ids differ by cloud; an example shows the shape expected.
const MODEL_EXAMPLES: Partial<Record<LlmProvider, string>> = {
  bedrock: "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
  vertex: "gemini-2.5-pro / claude-sonnet-4-5@20250929",
  openrouter: "anthropic/claude-sonnet-4.5",
  zai: "glm-5.3",
  deepseek: "deepseek-v4-pro",
  qwen: "qwen3.8-max",
  qwen_coding: "qwen3-coder-plus",
  meta: "muse-spark-1.3",
  minimax: "MiniMax-M2",
};

const DEFAULT_VERTEX_LOCATION = "us-central1";
// Vertex keeps project and location in the endpoint, as Google names them.
export function parseVertexEndpoint(endpoint: string): { project: string; location: string } {
  const match = /^projects\/([^/]*)\/locations\/([^/]*)$/.exec(endpoint.trim());
  return { project: match?.[1] ?? "", location: match?.[2] || DEFAULT_VERTEX_LOCATION };
}
export const vertexEndpoint = (project: string, location: string) =>
  `projects/${project.trim()}/locations/${location.trim() || DEFAULT_VERTEX_LOCATION}`;

export const defaultLlmConfig = (provider: LlmProvider): LlmConfig => ({
  provider,
  model: "",
  endpoint: providerMeta[provider].endpoint,
  apiKey: "",
});

export function llmConfigFromSaved(saved: unknown, fallback: LlmConfig): LlmConfig {
  if (!saved || typeof saved !== "object") return fallback;
  const record = saved as Record<string, unknown>;
  const provider =
    typeof record.provider === "string" && record.provider in providerMeta
      ? (record.provider as LlmProvider)
      : fallback.provider;
  return {
    ...fallback,
    provider,
    model: typeof record.model === "string" ? record.model : fallback.model,
    endpoint: typeof record.endpoint === "string" && record.endpoint ? record.endpoint : providerMeta[provider].endpoint,
  };
}

// Where the Python API found this role's credential (core/init_api.key_source).
type KeySource = "typed" | "stored" | "env" | "oauth" | "token" | null;

type ConnectionTest = { ok: boolean; status: string; message: string; detail?: string } | null;

type ModelCatalog = {
  models: string[];
  recommended: string;
  keySource?: KeySource;
  loading: boolean;
  error: string | null;
  status?: string | null;
  detail?: string | null;
};

// The API classifies provider failures into these slugs (core/init_api.py
// classify_llm_error); the page shows them in plain words.
const STATUS_TEXT: Record<string, string> = {
  ok: "Connected.",
  auth: "Authentication failed. Check the API key or sign in again.",
  billing: "This account is out of credit or needs payment.",
  rate_limit: "The provider is throttling requests. Try again shortly.",
  model: "Model or endpoint not found. Check the model name and endpoint.",
  network: "Could not reach the endpoint. Check the address and network.",
  config: "Choose a provider and model.",
};
const outcomeText = (status: string | null | undefined, raw: string) =>
  (status && STATUS_TEXT[status]) || `Request failed: ${raw}`;

function sameSettings(a: LlmConfig, b: LlmConfig): boolean {
  return a.provider === b.provider && a.model === b.model && a.endpoint === b.endpoint;
}

const emptyCatalog: ModelCatalog = { models: [], recommended: "", loading: false, error: null };

async function postJson<T>(url: string, payload: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload ?? {}),
  });
  const body = (await res.json().catch(() => null)) as Record<string, unknown> | null;
  if (!res.ok) {
    const message = body?.error || body?.detail || body?.message;
    throw new Error(typeof message === "string" && message.trim() ? message : `Request failed: ${res.status}`);
  }
  return body as T;
}

const errorText = (err: unknown) => (err instanceof Error && err.message ? err.message : String(err));

const fieldClass = "mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm";
const labelClass = "text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]";

export type SavedModels = {
  status: Record<string, unknown> | null;
  config: Record<LlmRole, { provider: string; model: string; endpoint?: string }>;
};

export function ModelsForm({ initialConscious, initialSubconscious, onSaved }: {
  initialConscious: LlmConfig;
  initialSubconscious: LlmConfig;
  onSaved: (saved: SavedModels) => void;
}) {
  const [conscious, setConscious] = useState(initialConscious);
  const [subconscious, setSubconscious] = useState(initialSubconscious);
  // Most installs run one model for both jobs: ask once unless they differ.
  const [sameAsPrimary, setSameAsPrimary] = useState(
    () => !initialSubconscious.model.trim() || sameSettings(initialConscious, initialSubconscious),
  );
  const [catalog, setCatalog] = useState<Record<LlmRole, ModelCatalog>>({
    conscious: emptyCatalog,
    subconscious: emptyCatalog,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tests, setTests] = useState<Record<LlmRole, ConnectionTest>>({ conscious: null, subconscious: null });
  const [testing, setTesting] = useState<LlmRole | null>(null);

  // The provider/endpoint/key each role's catalog was last fetched for. A
  // field losing focus refetches only if one of them changed, so the provider
  // is not asked again (with the same or a half-typed key) on every blur.
  const lastRequest = useRef<Record<LlmRole, string | null>>({ conscious: null, subconscious: null });

  // Fetch the live model list for a role's provider. Fills the model field
  // with the recommended default only when it is currently empty.
  const loadModels = useCallback(async (role: LlmRole, config: LlmConfig, force = false) => {
    const setConfig = role === "conscious" ? setConscious : setSubconscious;
    if ((config.provider === "openai_compatible" || config.provider === "azure_openai") && !config.endpoint.trim()) {
      // Nothing to ask until there is an endpoint.
      lastRequest.current[role] = null;
      setCatalog((prev) => ({ ...prev, [role]: emptyCatalog }));
      return;
    }
    const request = JSON.stringify([config.provider, config.endpoint.trim(), config.apiKey.trim()]);
    if (!force && lastRequest.current[role] === request) return;
    lastRequest.current[role] = request;
    setCatalog((prev) => ({ ...prev, [role]: { ...prev[role], loading: true, error: null } }));
    try {
      const payload = await postJson<Record<string, unknown>>("/api/init/llm/models", {
        role, provider: config.provider, endpoint: config.endpoint, api_key: config.apiKey,
      });
      const models = Array.isArray(payload?.models)
        ? payload.models.filter((item: unknown): item is string => typeof item === "string")
        : [];
      const recommended = typeof payload?.default === "string" ? payload.default : "";
      const keySource = (typeof payload?.key_source === "string" ? payload.key_source : null) as KeySource;
      setCatalog((prev) => ({
        ...prev,
        [role]: {
          models, recommended, keySource, loading: false,
          error: typeof payload?.error === "string" ? payload.error : null,
          status: typeof payload?.status === "string" ? payload.status : null,
          detail: typeof payload?.detail === "string" ? payload.detail : null,
        },
      }));
      if (recommended) setConfig((prev) => (prev.model.trim() ? prev : { ...prev, model: recommended }));
    } catch (err: unknown) {
      setCatalog((prev) => ({ ...prev, [role]: { ...emptyCatalog, error: errorText(err) } }));
    }
  }, []);

  useEffect(() => {
    void loadModels("conscious", conscious);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conscious.provider, loadModels]);

  useEffect(() => {
    // With "same as primary" the background model is not shown or saved.
    if (!sameAsPrimary) void loadModels("subconscious", subconscious);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subconscious.provider, sameAsPrimary, loadModels]);

  const setProvider = (role: LlmRole, provider: LlmProvider) => {
    // Clear the model so the list fetch can supply the provider's default.
    (role === "conscious" ? setConscious : setSubconscious)((prev) => ({ ...prev, ...defaultLlmConfig(provider) }));
    setTests((prev) => ({ ...prev, [role]: null }));
  };

  // Signed in with a subscription: this model now uses the account, and any
  // other model already on that account lists its models again.
  const switchToAccount = (role: LlmRole, account: LlmProvider) => {
    const configs = { conscious, subconscious };
    if (configs[role].provider !== account) setProvider(role, account);
    else void loadModels(role, configs[role], true);
    const other: LlmRole = role === "conscious" ? "subconscious" : "conscious";
    if ((other === "conscious" || !sameAsPrimary) && configs[other].provider === account)
      void loadModels(other, configs[other], true);
  };

  const testConnection = async (role: LlmRole, config: LlmConfig) => {
    setTesting(role);
    setTests((prev) => ({ ...prev, [role]: null }));
    try {
      const payload = await postJson<{ ok?: boolean; status?: string; message?: string; detail?: string }>(
        "/api/init/llm/test",
        { role, provider: config.provider, model: config.model, endpoint: config.endpoint, api_key: config.apiKey },
      );
      setTests((prev) => ({
        ...prev,
        [role]: {
          ok: Boolean(payload?.ok),
          status: typeof payload?.status === "string" ? payload.status : "error",
          message: typeof payload?.message === "string" ? payload.message : "",
          detail: typeof payload?.detail === "string" ? payload.detail : undefined,
        },
      }));
    } catch (err: unknown) {
      setTests((prev) => ({ ...prev, [role]: { ok: false, status: "error", message: errorText(err) } }));
    } finally {
      setTesting(null);
    }
  };

  const keyHint = (role: LlmRole, required: boolean) => {
    const source = catalog[role].keySource;
    if (source === "env") return "A key for this provider is already set in the environment; leave blank to use it.";
    if (source === "stored") return "A key is already saved from an earlier setup; leave blank to keep it.";
    return required ? "Required" : "Optional";
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const missing: string[] = [];
      const check = (role: LlmRole, config: LlmConfig) => {
        const meta = providerMeta[config.provider];
        if (!config.model.trim()) missing.push(`${role} model`);
        if ((config.provider === "openai_compatible" || config.provider === "azure_openai") && !config.endpoint.trim())
          missing.push(`${role} endpoint`);
        if (config.provider === "vertex" && !parseVertexEndpoint(config.endpoint).project)
          missing.push(`${role} Google Cloud project`);
        if ((isAccount(config.provider) || meta.loginOnly) && catalog[role].keySource !== "oauth")
          missing.push(`${role} ${isAccount(config.provider) ? ACCOUNT_LABEL[config.provider].name : meta.label} sign-in`);
        const keyAvailable = ["env", "stored", "token"].includes(catalog[role].keySource ?? "");
        if (meta.apiKeyRequired && !config.apiKey.trim() && !keyAvailable) missing.push(`${role} API key`);
      };
      const background = sameAsPrimary ? conscious : subconscious;
      check("conscious", conscious);
      if (!sameAsPrimary) check("subconscious", subconscious);
      if (missing.length > 0) {
        setError(`Still needed: ${missing.join(", ")}`);
        return;
      }
      const payload = (c: LlmConfig) => ({ provider: c.provider, model: c.model, endpoint: c.endpoint, api_key: c.apiKey });
      const saved = await postJson<SavedModels>("/api/init/llm", {
        conscious: payload(conscious),
        subconscious: payload(background),
        reuse_primary: sameAsPrimary,
      });
      onSaved(saved);
    } catch (err: unknown) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const entries = [
    { role: "conscious" as const, label: "Conscious Model", hint: "Makes decisions, writes replies, and takes actions.", config: conscious, setConfig: setConscious },
    ...(sameAsPrimary ? [] : [{
      role: "subconscious" as const, label: "Subconscious Model",
      hint: "Senses emotional tone, forms first impressions, and recalls memories. It runs often, so a fast model works well.",
      config: subconscious, setConfig: setSubconscious,
    }]),
  ];

  return (
    <div className="space-y-6">
      {entries.map(({ role, label, hint, config, setConfig }) => {
        const choice = providerChoice(config.provider);
        const meta = providerMeta[config.provider];
        const choiceMeta = providerMeta[choice];
        const cat = catalog[role];
        const keyKnown = cat.keySource === "env" || cat.keySource === "stored";
        const usingAccount = isAccount(config.provider) || Boolean(meta.loginOnly);
        const usingToken = config.provider === "anthropic" && cat.keySource === "token" && !config.apiKey.trim();
        const showKeyField = !usingAccount;
        return (
          <fieldset key={role} className="rounded-2xl border border-[var(--outline)] p-4">
            <legend className="px-2 text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">{label}</legend>
            <p className="px-2 text-xs text-[var(--ink-soft)]">{hint}</p>
            <div className="mt-3 grid gap-4">
              <div>
                <label htmlFor={`provider-${role}`} className={labelClass}>Provider</label>
                <select id={`provider-${role}`} className={fieldClass} value={choice}
                  onChange={(e) => setProvider(role, e.target.value as LlmProvider)}>
                  {providerOrder.map((value) => (
                    <option key={value} value={value}>{providerMeta[value].label}</option>
                  ))}
                </select>
              </div>

              {config.provider === "azure_openai" ? (
                <div>
                  <label htmlFor={`endpoint-${role}`} className={labelClass}>Azure OpenAI endpoint</label>
                  <input id={`endpoint-${role}`} className={fieldClass} value={config.endpoint}
                    onChange={(e) => setConfig((prev) => ({ ...prev, endpoint: e.target.value }))}
                    onBlur={() => void loadModels(role, config)}
                    placeholder="https://<resource>.openai.azure.com" />
                  <p className="mt-1 text-xs text-[var(--ink-soft)]">The resource URL from the Azure portal (Keys and Endpoint).</p>
                </div>
              ) : null}
              {config.provider === "bedrock" ? (
                <div>
                  <label htmlFor={`region-${role}`} className={labelClass}>AWS region</label>
                  <input id={`region-${role}`} className={fieldClass} value={config.endpoint}
                    onChange={(e) => setConfig((prev) => ({ ...prev, endpoint: e.target.value }))}
                    placeholder="us-east-1" />
                  <p className="mt-1 text-xs text-[var(--ink-soft)]">The region whose Bedrock endpoint serves your models.</p>
                </div>
              ) : null}
              {config.provider === "vertex" ? (() => {
                const vertex = parseVertexEndpoint(config.endpoint);
                return (
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div>
                      <label htmlFor={`project-${role}`} className={labelClass}>Google Cloud project ID</label>
                      <input id={`project-${role}`} className={fieldClass} value={vertex.project}
                        onChange={(e) => setConfig((prev) => ({ ...prev, endpoint: vertexEndpoint(e.target.value, parseVertexEndpoint(prev.endpoint).location) }))}
                        placeholder="my-project" />
                    </div>
                    <div>
                      <label htmlFor={`location-${role}`} className={labelClass}>Region</label>
                      <input id={`location-${role}`} className={fieldClass} value={vertex.location}
                        onChange={(e) => setConfig((prev) => ({ ...prev, endpoint: vertexEndpoint(parseVertexEndpoint(prev.endpoint).project, e.target.value) }))}
                        placeholder={DEFAULT_VERTEX_LOCATION} />
                    </div>
                  </div>
                );
              })() : null}
              {config.provider === "openai_compatible" ? (
                <div>
                  <label htmlFor={`endpoint-${role}`} className={labelClass}>Endpoint</label>
                  <input id={`endpoint-${role}`} className={fieldClass} value={config.endpoint}
                    onChange={(e) => setConfig((prev) => ({ ...prev, endpoint: e.target.value }))}
                    onBlur={() => void loadModels(role, config)}
                    placeholder="https://..." />
                  <p className="mt-1 text-xs text-[var(--ink-soft)]">
                    A server on this machine is reached as host.docker.internal, not localhost, because the Hexis containers make the call.
                  </p>
                </div>
              ) : null}
              {ENDPOINT_OPTIONS[config.provider] ? (
                <div>
                  <label htmlFor={`plan-${role}`} className={labelClass}>{config.provider === "zai" ? "Plan and region" : "Region"}</label>
                  <select id={`plan-${role}`} className={fieldClass} value={config.endpoint}
                    onChange={(e) => setConfig((prev) => ({ ...prev, endpoint: e.target.value }))}
                    onBlur={() => void loadModels(role, config)}>
                    {ENDPOINT_OPTIONS[config.provider]!.map((option) => (
                      <option key={option.url} value={option.url}>{option.label}</option>
                    ))}
                  </select>
                </div>
              ) : null}

              {/* Subscription logins for this vendor, or the login-only provider itself. */}
              {(choiceMeta.accounts ?? (choiceMeta.loginOnly ? [choice as OAuthProvider] : [])).map((account) => (
                <OAuthControl
                  key={account}
                  provider={account}
                  active={config.provider === account}
                  onUse={() => switchToAccount(role, account)}
                  onLeave={choiceMeta.loginOnly ? undefined : () => setProvider(role, choice)}
                />
              ))}
              {choice === "anthropic" ? (
                <SetupTokenControl
                  active={usingToken}
                  onUse={() => {
                    setConfig((prev) => ({ ...prev, apiKey: "" }));
                    void loadModels(role, { ...config, apiKey: "" }, true);
                  }}
                  onLeave={() => void loadModels(role, config, true)}
                />
              ) : null}

              {showKeyField && !usingToken ? (
                <div>
                  {choiceMeta.accounts ? (
                    <p className="mb-3 text-xs text-[var(--ink-soft)]">Or use a {choiceMeta.label} API key:</p>
                  ) : null}
                  <label htmlFor={`apikey-${role}`} className={labelClass}>
                    {config.provider === "bedrock" ? "Bedrock API key (optional)"
                      : config.provider === "vertex" ? "Service account key, JSON (optional)"
                      : meta.apiKeyRequired ? "API key" : "API key (optional)"}
                  </label>
                  <input id={`apikey-${role}`} className={fieldClass} type="password" value={config.apiKey}
                    onChange={(e) => setConfig((prev) => ({ ...prev, apiKey: e.target.value }))}
                    onBlur={() => void loadModels(role, config)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !busy) void save(); }}
                    placeholder={keyKnown ? undefined : keyHint(role, meta.apiKeyRequired)} />
                  {keyKnown ? (
                    <p className="mt-1 text-xs text-[var(--ink-soft)]">{keyHint(role, meta.apiKeyRequired)}</p>
                  ) : config.provider === "bedrock" ? (
                    <p className="mt-1 text-xs text-[var(--ink-soft)]">Leave blank to use this server&apos;s AWS credentials (an IAM role or AWS_* environment variables).</p>
                  ) : config.provider === "vertex" ? (
                    <p className="mt-1 text-xs text-[var(--ink-soft)]">Paste the key file&apos;s contents, or leave blank to use this server&apos;s Google credentials. Gemini and Claude models both work.</p>
                  ) : null}
                </div>
              ) : null}

              {/* The model list is read from the endpoint with the key, so those come first. */}
              <div>
                <label htmlFor={`model-${role}`} className={labelClass}>
                  {config.provider === "azure_openai" ? "Deployment name" : "Model"}
                </label>
                <input id={`model-${role}`} className={fieldClass} list={`model-options-${role}`} value={config.model}
                  onChange={(e) => setConfig((prev) => ({ ...prev, model: e.target.value }))}
                  onKeyDown={(e) => { if (e.key === "Enter" && !busy) void save(); }}
                  placeholder={MODEL_EXAMPLES[config.provider] ?? "Model id"} />
                {cat.models.length > 0 ? (
                  <datalist id={`model-options-${role}`}>
                    {cat.models.map((m) => <option key={m} value={m} />)}
                  </datalist>
                ) : null}
                <p className="mt-2 text-xs text-[var(--ink-soft)]">
                  {usingAccount && !cat.keySource
                    ? "Log in to see the models your plan includes."
                    : (config.provider === "openai_compatible" || config.provider === "azure_openai") && !config.endpoint.trim()
                      ? "Enter the endpoint to list its models."
                      : cat.loading
                        ? "Loading available models…"
                        : cat.error && cat.status === "auth" && !config.apiKey.trim() && !cat.keySource
                          ? "Enter an API key to load the list of available models."
                          : cat.error
                            ? outcomeText(cat.status, cat.detail || cat.error)
                            : cat.models.length > 0
                              ? `${cat.models.length} models available — or type any model id.`
                              : "No models listed — type a model id."}
                  {cat.error && !(cat.status === "auth" && !config.apiKey.trim() && !cat.keySource) ? (
                    <button type="button" className="ml-2 text-[var(--accent-strong)] underline"
                      onClick={() => void loadModels(role, config, true)}>
                      Retry
                    </button>
                  ) : null}
                </p>
              </div>

              <div className="flex flex-wrap items-center gap-3">
                <button type="button"
                  className="rounded-full border border-[var(--outline)] px-4 py-2 text-xs font-semibold text-[var(--foreground)] transition hover:border-[var(--accent-strong)] disabled:opacity-50"
                  onClick={() => void testConnection(role, config)}
                  disabled={testing !== null || busy || !config.model.trim()}>
                  {testing === role ? "Testing…" : "Test connection"}
                </button>
                {tests[role] ? (
                  <span role="status" className={`text-xs ${tests[role]?.ok ? "text-green-700" : "text-red-700"}`}>
                    {tests[role]?.ok ? "Connected." : outcomeText(tests[role]?.status, tests[role]?.detail || tests[role]?.message || "")}
                  </span>
                ) : null}
              </div>
            </div>
          </fieldset>
        );
      })}
      <label className="flex items-start gap-3 rounded-2xl border border-[var(--outline)] p-4 text-sm text-[var(--foreground)]">
        <input type="checkbox" className="mt-1" checked={sameAsPrimary}
          onChange={(e) => {
            const same = e.target.checked;
            setSameAsPrimary(same);
            // Start the separate form from the primary settings, not blank.
            if (!same && !subconscious.model.trim()) setSubconscious({ ...conscious });
          }} />
        <span>
          <span className="font-semibold">Use the conscious model for the subconscious too</span>
          <span className="mt-1 block text-xs text-[var(--ink-soft)]">Most installs run one model for both jobs. Untick to use a different one.</span>
        </span>
      </label>
      {error ? (
        <p role="alert" className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
      ) : null}
      <button
        className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)] disabled:opacity-50"
        onClick={() => void save()} disabled={busy}>
        Save Models
      </button>
    </div>
  );
}
