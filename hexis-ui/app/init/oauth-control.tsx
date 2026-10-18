"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * Subscription sign-ins and pasted tokens, shown inside a provider's form.
 *
 * A vendor is one entry in the provider list; its form offers an API key and,
 * where the vendor has one, a subscription login: ChatGPT under OpenAI (the
 * `openai-codex` provider), SuperGrok / X under Grok (`xai-oauth`), Google
 * under Gemini (`google-gemini-cli`, `google-antigravity`), Qwen Portal under
 * Alibaba Qwen, MiniMax Portal under MiniMax, and a Claude Code setup token
 * under Anthropic. Chutes and GitHub Copilot are login-only.
 *
 * `active` means this model uses the account rather than an API key. `onUse`
 * switches the model to the account once it is signed in; `onLeave` goes back
 * to an API key (absent for login-only providers).
 */

export type OAuthProvider =
  | "openai-codex"
  | "xai-oauth"
  | "chutes"
  | "github-copilot"
  | "qwen-portal"
  | "minimax-portal"
  | "google-gemini-cli"
  | "google-antigravity";

export type OAuthCredentialStatus = {
  provider: string;
  configured: boolean;
  expires_at?: string;
  expires_in_seconds?: number;
  email?: string;
  account_id?: string;
  base_url?: string;
  project_id?: string;
  resource_url?: string;
  region?: string;
};

type OAuthSession = {
  session_id: string;
  provider: string;
  flow: "authorization_code" | "device_code";
  status: "awaiting_code" | "waiting_for_user" | "exchanging" | "complete" | "error" | "expired";
  expires_in_seconds: number;
  callback_active?: boolean;
  authorization_url?: string;
  verification_uri?: string;
  user_code?: string;
  error?: string;
  credential?: OAuthCredentialStatus;
};

type OAuthOptions = {
  client_id: string;
  client_secret: string;
  redirect_uri: string;
  enterprise_domain: string;
  region: "global" | "cn";
};

const initialOptions: OAuthOptions = {
  client_id: "",
  client_secret: "",
  redirect_uri: "",
  enterprise_domain: "",
  region: "global",
};

// What the account is called, per sign-in.
export const ACCOUNT_LABEL: Record<OAuthProvider, { name: string; site: string; hint: string }> = {
  "openai-codex": {
    name: "ChatGPT", site: "OpenAI",
    hint: "Use your ChatGPT Plus/Pro subscription instead of an API key.",
  },
  "xai-oauth": {
    name: "xAI", site: "xAI",
    hint: "Use your SuperGrok or X Premium subscription instead of an API key.",
  },
  chutes: { name: "Chutes", site: "Chutes", hint: "Free inference with a Chutes account. No API key is needed." },
  "github-copilot": {
    name: "GitHub Copilot", site: "GitHub",
    hint: "Use your GitHub Copilot subscription. No API key is needed.",
  },
  "qwen-portal": {
    name: "Qwen Portal", site: "Qwen",
    hint: "Use a free Qwen Portal account instead of a Model Studio API key.",
  },
  "minimax-portal": {
    name: "MiniMax Portal", site: "MiniMax",
    hint: "Use your MiniMax Portal account instead of an API key.",
  },
  "google-gemini-cli": {
    name: "Google (Gemini CLI)", site: "Google",
    hint: "Use your Google account through Cloud Code Assist, as the Gemini CLI does.",
  },
  "google-antigravity": {
    name: "Google (Antigravity)", site: "Google",
    hint: "Use your Google account through the Antigravity Cloud Code Assist sandbox.",
  },
};

async function authRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { cache: "no-store", ...init });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = payload?.detail || payload?.error || payload?.message;
    const error = new Error(
      typeof message === "string" ? message : `Authorization request failed (${response.status}).`,
    ) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  return payload as T;
}

export async function getOAuthStatus(provider: string, validate = false): Promise<OAuthCredentialStatus> {
  const params = new URLSearchParams({ provider });
  if (validate) params.set("validate", "true");
  return authRequest<OAuthCredentialStatus>(`/api/init/auth/status?${params.toString()}`);
}

function credentialIdentity(status: OAuthCredentialStatus): string | null {
  return status.email || status.account_id || status.project_id || status.base_url || status.resource_url || null;
}

const buttonClass =
  "rounded-full bg-[var(--foreground)] px-5 py-2 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)] disabled:opacity-50";
const secondaryButtonClass =
  "rounded-full border border-[var(--outline)] px-4 py-2 text-sm font-semibold transition hover:border-[var(--accent-strong)] disabled:opacity-50";
const linkClass = "text-sm text-[var(--accent-strong)] underline disabled:opacity-50";
const inputClass =
  "mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-3 py-2 text-sm normal-case tracking-normal text-[var(--foreground)]";
const optionLabelClass = "block text-xs uppercase tracking-[0.2em] text-[var(--ink-soft)]";

export function OAuthControl({ provider, active, onUse, onLeave }: {
  provider: OAuthProvider;
  active: boolean;
  onUse: () => void;
  onLeave?: () => void;
}) {
  const label = ACCOUNT_LABEL[provider];
  const [credential, setCredential] = useState<OAuthCredentialStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [session, setSession] = useState<OAuthSession | null>(null);
  const [authorizationInput, setAuthorizationInput] = useState("");
  const [options, setOptions] = useState<OAuthOptions>(initialOptions);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The parent keys this control by provider, so a fresh mount is a fresh status check.
  useEffect(() => {
    let cancelled = false;
    getOAuthStatus(provider)
      .then((status) => { if (!cancelled) { setCredential(status); setError(null); } })
      .catch((reason: unknown) => { if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [provider]);

  const signedIn = useCallback((next?: OAuthCredentialStatus) => {
    setCredential(next ?? { provider, configured: true });
    setSession(null);
    setAuthorizationInput("");
    setError(null);
    onUse();
  }, [onUse, provider]);

  // Wait for the user to approve the code (device flow) or for the browser
  // callback (authorization-code flow with a reachable callback port).
  useEffect(() => {
    if (!session || session.status === "complete" || session.status === "error" || session.status === "expired") return;
    if (session.flow === "authorization_code" && !session.callback_active && session.status === "awaiting_code") return;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      try {
        const next = await authRequest<OAuthSession>(`/api/init/auth/session/${encodeURIComponent(session.session_id)}`);
        if (cancelled) return;
        if (next.status === "complete") {
          signedIn(next.credential);
        } else if (next.status === "error" || next.status === "expired") {
          setSession(null);
          setError(next.error || "This sign-in attempt expired. Start again.");
        } else {
          setSession(next);
        }
      } catch (reason: unknown) {
        if (cancelled) return;
        setSession(null);
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    }, 1500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [session, signedIn]);

  const start = async () => {
    setBusy(true); setError(null); setAuthorizationInput(""); setCopied(false);
    try {
      const publicOptions = Object.fromEntries(Object.entries(options).filter(([, value]) => value.trim()));
      setSession(await authRequest<OAuthSession>("/api/init/auth/start", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ provider, options: publicOptions }),
      }));
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  // Already signed in from an earlier setup: no need to go through the provider again.
  const login = () => (credential?.configured ? onUse() : void start());

  const complete = async () => {
    if (!session) return;
    setBusy(true); setError(null);
    try {
      const next = await authRequest<OAuthSession>("/api/init/auth/complete", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ session_id: session.session_id, authorization_input: authorizationInput }),
      });
      if (next.status === "complete") signedIn(next.credential);
      else setSession(next);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const forget = async () => {
    setBusy(true); setError(null);
    try {
      setCredential(await authRequest<OAuthCredentialStatus>("/api/init/auth/forget", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ provider }),
      }));
      setSession(null);
      if (active) onLeave?.();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const copyAndOpen = async () => {
    if (!session?.verification_uri) return;
    window.open(session.verification_uri, "_blank", "noopener,noreferrer");
    try {
      if (session.user_code) await navigator.clipboard.writeText(session.user_code);
      setCopied(Boolean(session.user_code));
    } catch {
      setCopied(false);
    }
  };

  const identity = credential ? credentialIdentity(credential) : null;
  const idle = !session || session.status === "complete" || session.status === "error" || session.status === "expired";
  const needsGoogleClient = provider === "google-gemini-cli" || provider === "google-antigravity";

  return (
    <div className="rounded-xl border border-[var(--outline)] bg-[var(--surface)] p-4 text-sm">
      {session && session.flow === "device_code" && session.status !== "complete" ? (
        <div className="space-y-3">
          <p>Enter this code on {label.site}&apos;s sign-in page:</p>
          <p className="select-all font-mono text-2xl font-semibold tracking-[0.2em]" aria-label="Login code">
            {session.user_code}
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <button type="button" onClick={copyAndOpen} className={buttonClass}>
              Copy code and open {label.site}
            </button>
            {copied ? <span className="text-xs text-green-700">Code copied</span> : null}
          </div>
          <p role="status" className="text-[var(--ink-soft)]">Waiting for you to approve it at {label.site}…</p>
          <button type="button" onClick={() => setSession(null)} className={linkClass}>Cancel</button>
        </div>
      ) : session && session.flow === "authorization_code" && session.status !== "complete" ? (
        <div className="space-y-3">
          <a className={`inline-flex ${buttonClass}`} href={session.authorization_url} target="_blank" rel="noopener noreferrer">
            Continue to {label.site}
          </a>
          <p className="text-[var(--ink-soft)]">
            {session.callback_active
              ? "After approval this page updates by itself. If it does not, paste the redirect URL below."
              : "After signing in, copy the full redirect URL from the browser address bar and paste it below. If that page cannot load, the URL still works here."}
          </p>
          <label className="block">
            Authorization code or redirect URL
            <input type="password" autoComplete="off" value={authorizationInput}
              onChange={(e) => setAuthorizationInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && authorizationInput.trim() && !busy) void complete(); }}
              className={inputClass} />
          </label>
          <div className="flex flex-wrap gap-3">
            <button type="button" onClick={complete} disabled={busy || !authorizationInput.trim()} className={secondaryButtonClass}>
              Complete sign-in
            </button>
            <button type="button" onClick={() => setSession(null)} className={linkClass}>Cancel</button>
          </div>
          {session.status === "exchanging" ? <p className="text-[var(--ink-soft)]">Completing sign-in…</p> : null}
        </div>
      ) : active && credential?.configured ? (
        <>
          <p className="font-semibold">Logged in with your {label.name} account</p>
          {identity ? <p className="mt-1 break-words text-[var(--ink-soft)]">{identity}</p> : null}
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
            {onLeave ? <button type="button" onClick={onLeave} className={linkClass}>Use an API key instead</button> : null}
            <button type="button" onClick={start} disabled={busy} className={linkClass}>Log in again</button>
            <button type="button" onClick={forget} disabled={busy} className={linkClass}>Sign out</button>
          </div>
        </>
      ) : (
        <>
          <button type="button" onClick={login} disabled={busy || loading} className={buttonClass}>
            {credential?.configured ? `Use your ${label.name} login` : `Log in with ${label.name}`}
          </button>
          <p className="mt-2 text-[var(--ink-soft)]">
            {credential?.configured && identity ? `Signed in as ${identity}. ` : ""}{label.hint}
          </p>
          {active && onLeave ? (
            <button type="button" onClick={onLeave} className={`mt-2 ${linkClass}`}>Use an API key instead</button>
          ) : null}
          {idle && provider === "chutes" ? (
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <OptionInput label="Chutes client ID" value={options.client_id}
                onChange={(value) => setOptions((c) => ({ ...c, client_id: value }))}
                placeholder="Required unless configured on the server" />
              <OptionInput label="Redirect URI" value={options.redirect_uri}
                onChange={(value) => setOptions((c) => ({ ...c, redirect_uri: value }))}
                placeholder="http://localhost:11435/auth/callback" />
            </div>
          ) : null}
          {idle && provider === "github-copilot" ? (
            <div className="mt-4">
              <OptionInput label="GitHub enterprise hostname" value={options.enterprise_domain}
                onChange={(value) => setOptions((c) => ({ ...c, enterprise_domain: value }))}
                placeholder="Optional, for example github.example.com" />
            </div>
          ) : null}
          {idle && provider === "minimax-portal" ? (
            <label className={`mt-4 ${optionLabelClass}`}>
              Region
              <select className={inputClass} value={options.region}
                onChange={(e) => setOptions((c) => ({ ...c, region: e.target.value as "global" | "cn" }))}>
                <option value="global">Global</option>
                <option value="cn">China</option>
              </select>
            </label>
          ) : null}
          {idle && needsGoogleClient ? (
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <OptionInput label="OAuth client ID" value={options.client_id}
                onChange={(value) => setOptions((c) => ({ ...c, client_id: value }))}
                placeholder="Uses server configuration when blank" />
              <OptionInput label="OAuth client secret" value={options.client_secret}
                onChange={(value) => setOptions((c) => ({ ...c, client_secret: value }))}
                placeholder="Uses server configuration when blank" secret />
            </div>
          ) : null}
        </>
      )}
      {error ? (
        <p role="alert" className="mt-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-red-700">{error}</p>
      ) : null}
    </div>
  );
}

/**
 * A Claude Code setup token pasted under the Anthropic form. Claude
 * subscriptions have no OAuth flow Hexis can drive; the token comes from
 * `claude setup-token` and is stored in Hexis's own auth store.
 */
export function SetupTokenControl({ active, onUse, onLeave }: {
  active: boolean;
  onUse: () => void;
  onLeave: () => void;
}) {
  const [configured, setConfigured] = useState(false);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getOAuthStatus("anthropic")
      .then((status) => { if (!cancelled) setConfigured(Boolean(status.configured)); })
      .catch((reason: unknown) => { if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const save = async () => {
    setBusy(true); setError(null);
    try {
      await authRequest("/api/init/auth/setup-token", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ provider: "anthropic", token }),
      });
      setConfigured(true); setEditing(false); setToken("");
      onUse();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const forget = async () => {
    setBusy(true); setError(null);
    try {
      await authRequest("/api/init/auth/forget", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ provider: "anthropic" }),
      });
      setConfigured(false); setEditing(false);
      if (active) onLeave();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border border-[var(--outline)] bg-[var(--surface)] p-4 text-sm">
      {editing ? (
        <div className="space-y-3">
          <label className="block">
            Claude Code setup token
            <input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && token.trim() && !busy) void save(); }}
              placeholder="sk-ant-oat01-…" className={inputClass} />
          </label>
          <p className="text-[var(--ink-soft)]">
            Run <code>claude setup-token</code> in a terminal with the Claude Code CLI signed in, then paste the token it prints.
          </p>
          <div className="flex flex-wrap gap-3">
            <button type="button" onClick={save} disabled={busy || !token.trim()} className={secondaryButtonClass}>Save token</button>
            <button type="button" onClick={() => { setEditing(false); setToken(""); }} className={linkClass}>Cancel</button>
          </div>
        </div>
      ) : active && configured ? (
        <>
          <p className="font-semibold">Using your Claude subscription (setup token)</p>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
            <button type="button" onClick={onLeave} className={linkClass}>Use an API key instead</button>
            <button type="button" onClick={() => setEditing(true)} className={linkClass}>Replace token</button>
            <button type="button" onClick={forget} disabled={busy} className={linkClass}>Sign out</button>
          </div>
        </>
      ) : (
        <>
          <button type="button" onClick={() => (configured ? onUse() : setEditing(true))} disabled={busy || loading} className={buttonClass}>
            {configured ? "Use your Claude subscription" : "Use a Claude subscription"}
          </button>
          <p className="mt-2 text-[var(--ink-soft)]">
            Paste a Claude Code setup token instead of an API key.
          </p>
          {active ? <button type="button" onClick={onLeave} className={`mt-2 ${linkClass}`}>Use an API key instead</button> : null}
        </>
      )}
      {error ? (
        <p role="alert" className="mt-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-red-700">{error}</p>
      ) : null}
    </div>
  );
}

function OptionInput({ label, value, onChange, placeholder, secret = false }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  secret?: boolean;
}) {
  return (
    <label className={optionLabelClass}>
      {label}
      <input className={inputClass} type={secret ? "password" : "text"} value={value}
        onChange={(event) => onChange(event.target.value)} placeholder={placeholder} autoComplete="off" />
    </label>
  );
}
