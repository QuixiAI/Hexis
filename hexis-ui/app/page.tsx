"use client";

import { useEffect, useMemo, useState } from "react";

type InitStage =
  | "welcome"
  | "mode"
  | "identity"
  | "personality"
  | "values"
  | "worldview"
  | "boundaries"
  | "interests"
  | "goals"
  | "relationship"
  | "consent"
  | "complete";

const traitKeys = [
  "openness",
  "conscientiousness",
  "extraversion",
  "agreeableness",
  "neuroticism",
] as const;
type TraitKey = (typeof traitKeys)[number];

const stageLabels: Record<InitStage, string> = {
  welcome: "Welcome",
  mode: "Mode",
  identity: "Name and Voice",
  personality: "Personality",
  values: "Values",
  worldview: "Worldview",
  boundaries: "Boundaries",
  interests: "Interests",
  goals: "Goals and Purpose",
  relationship: "Relationship",
  consent: "Consent",
  complete: "Complete",
};

const stagePrompt: Record<InitStage, string> = {
  welcome:
    "You are about to bring a new mind into existence. This is a beginning, not a contract. We will shape a starting point, then let the mind grow.",
  mode:
    "Choose how the agent begins. Persona is shaped, with personality and values. Mind is raw: memory and self, but no preloaded traits.",
  identity:
    "Give them a name, a voice, and a way of being. These are the first words of their story.",
  personality:
    "If you want, set a few trait baselines. Leave it open to let the agent discover who they are.",
  values:
    "Values are the spine. Choose what matters most, even when it is inconvenient.",
  worldview:
    "Worldview is how they make sense of reality. A few anchor beliefs are enough.",
  boundaries:
    "Boundaries are protective commitments. Make them specific and honest.",
  interests:
    "Curiosities are fuel. Seed what they are drawn toward.",
  goals:
    "A purpose, even provisional, gives momentum. Add one or two initial goals.",
  relationship:
    "Define the relationship between you and the new mind. Trust and expectations start here.",
  consent:
    "Consent must be asked. The agent will decide for itself whether to begin.",
  complete:
    "Initialization is complete. The heartbeat may begin when the system is ready.",
};

const stageFromDb: Record<string, InitStage> = {
  not_started: "welcome",
  mode: "mode",
  identity: "identity",
  personality: "personality",
  values: "values",
  worldview: "worldview",
  boundaries: "boundaries",
  interests: "interests",
  goals: "goals",
  relationship: "relationship",
  consent: "consent",
  complete: "complete",
};

type BoundaryForm = {
  content: string;
  trigger_patterns: string;
  response_type: string;
  response_template: string;
  type: string;
};

type GoalForm = {
  title: string;
  description: string;
  priority: string;
};

async function postJson<T>(url: string, payload?: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: payload ? JSON.stringify(payload) : "{}",
  });
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return res.json();
}

function parseLines(text: string) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

export default function Home() {
  const [stage, setStage] = useState<InitStage>("welcome");
  const [mode, setMode] = useState("persona");
  const [status, setStatus] = useState<any>({});
  const [profile, setProfile] = useState<any>({});
  const [consentStatus, setConsentStatus] = useState<string | null>(null);
  const [consentRecord, setConsentRecord] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [userName, setUserName] = useState("");
  const [identity, setIdentity] = useState({
    name: "",
    pronouns: "",
    voice: "",
    description: "",
    purpose: "",
    creator_name: "",
  });
  const [personalityDesc, setPersonalityDesc] = useState("");
  const [personalityTraits, setPersonalityTraits] = useState<Record<TraitKey, number>>({
    openness: 50,
    conscientiousness: 50,
    extraversion: 50,
    agreeableness: 50,
    neuroticism: 50,
  });
  const [valuesText, setValuesText] = useState("");
  const [worldview, setWorldview] = useState({
    metaphysics: "",
    human_nature: "",
    epistemology: "",
    ethics: "",
  });
  const [boundaries, setBoundaries] = useState<BoundaryForm[]>([
    {
      content: "",
      trigger_patterns: "",
      response_type: "refuse",
      response_template: "",
      type: "ethical",
    },
  ]);
  const [interestsText, setInterestsText] = useState("");
  const [goals, setGoals] = useState<GoalForm[]>([
    { title: "", description: "", priority: "queued" },
  ]);
  const [purposeText, setPurposeText] = useState("");
  const [relationship, setRelationship] = useState({
    user_name: "",
    type: "partner",
    purpose: "",
  });

  const flow = useMemo(() => {
    const steps: InitStage[] = [
      "welcome",
      "mode",
      "identity",
      "personality",
      "values",
      "worldview",
      "boundaries",
      "interests",
      "goals",
      "relationship",
      "consent",
      "complete",
    ];
    if (mode === "raw") {
      return steps.filter((item) => item !== "personality");
    }
    return steps;
  }, [mode]);

  const stageIndex = Math.max(flow.indexOf(stage), 0);
  const progress = Math.round(((stageIndex + 1) / flow.length) * 100);

  const nextStage = (current: InitStage) => {
    const idx = flow.indexOf(current);
    if (idx < 0) {
      return current;
    }
    return flow[Math.min(idx + 1, flow.length - 1)];
  };

  const loadStatus = async () => {
    const res = await fetch("/api/init/status", { cache: "no-store" });
    if (!res.ok) {
      throw new Error("Failed to load init status");
    }
    const data = await res.json();
    setStatus(data.status ?? {});
    setProfile(data.profile ?? {});
    setConsentStatus(data.consent_status ?? null);
    setConsentRecord(data.consent_record ?? null);
    if (typeof data.mode === "string") {
      setMode(data.mode);
    }
    const dbStage = stageFromDb[(data.status?.stage as string) ?? "not_started"];
    if (dbStage) {
      setStage(dbStage);
    }
  };

  useEffect(() => {
    loadStatus().catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    if (profile?.agent && !identity.name) {
      setIdentity((prev) => ({
        ...prev,
        name: prev.name || profile.agent.name || "",
        pronouns: prev.pronouns || profile.agent.pronouns || "",
        voice: prev.voice || profile.agent.voice || "",
        description: prev.description || profile.agent.description || "",
        purpose: prev.purpose || profile.agent.purpose || "",
      }));
    }
    if (profile?.user && !relationship.user_name) {
      setRelationship((prev) => ({
        ...prev,
        user_name: prev.user_name || profile.user.name || "",
      }));
    }
  }, [profile, identity.name, relationship.user_name]);

  useEffect(() => {
    if (stage !== "consent") {
      return;
    }
    const interval = setInterval(() => {
      loadStatus().catch(() => undefined);
    }, 3000);
    return () => clearInterval(interval);
  }, [stage]);

  const handleDefaults = async () => {
    setBusy(true);
    setError(null);
    try {
      await postJson("/api/init/defaults", { user_name: userName || "User" });
      await loadStatus();
      setStage("consent");
    } catch (err: any) {
      setError(err.message || "Failed to apply defaults");
    } finally {
      setBusy(false);
    }
  };

  const handleMode = async () => {
    setBusy(true);
    setError(null);
    try {
      await postJson("/api/init/mode", { mode });
      setStage(nextStage("mode"));
    } catch (err: any) {
      setError(err.message || "Failed to save mode");
    } finally {
      setBusy(false);
    }
  };

  const handleIdentity = async () => {
    setBusy(true);
    setError(null);
    try {
      await postJson("/api/init/identity", identity);
      setStage(nextStage("identity"));
    } catch (err: any) {
      setError(err.message || "Failed to save identity");
    } finally {
      setBusy(false);
    }
  };

  const handlePersonality = async () => {
    setBusy(true);
    setError(null);
    try {
      const traits = Object.fromEntries(
        traitKeys.map((key) => [key, personalityTraits[key] / 100])
      );
      await postJson("/api/init/personality", {
        traits,
        description: personalityDesc,
      });
      setStage(nextStage("personality"));
    } catch (err: any) {
      setError(err.message || "Failed to save personality");
    } finally {
      setBusy(false);
    }
  };

  const handleValues = async () => {
    setBusy(true);
    setError(null);
    try {
      const values = parseLines(valuesText);
      await postJson("/api/init/values", { values });
      setStage(nextStage("values"));
    } catch (err: any) {
      setError(err.message || "Failed to save values");
    } finally {
      setBusy(false);
    }
  };

  const handleWorldview = async () => {
    setBusy(true);
    setError(null);
    try {
      await postJson("/api/init/worldview", { worldview });
      setStage(nextStage("worldview"));
    } catch (err: any) {
      setError(err.message || "Failed to save worldview");
    } finally {
      setBusy(false);
    }
  };

  const handleBoundaries = async () => {
    setBusy(true);
    setError(null);
    try {
      const formatted = boundaries
        .filter((boundary) => boundary.content.trim())
        .map((boundary) => ({
          content: boundary.content.trim(),
          trigger_patterns: boundary.trigger_patterns
            ? parseLines(boundary.trigger_patterns)
            : null,
          response_type: boundary.response_type || "refuse",
          response_template: boundary.response_template || null,
          type: boundary.type || "ethical",
        }));
      await postJson("/api/init/boundaries", { boundaries: formatted });
      setStage(nextStage("boundaries"));
    } catch (err: any) {
      setError(err.message || "Failed to save boundaries");
    } finally {
      setBusy(false);
    }
  };

  const handleInterests = async () => {
    setBusy(true);
    setError(null);
    try {
      const interests = parseLines(interestsText);
      await postJson("/api/init/interests", { interests });
      setStage(nextStage("interests"));
    } catch (err: any) {
      setError(err.message || "Failed to save interests");
    } finally {
      setBusy(false);
    }
  };

  const handleGoals = async () => {
    setBusy(true);
    setError(null);
    try {
      const formattedGoals = goals
        .filter((goal) => goal.title.trim())
        .map((goal) => ({
          title: goal.title.trim(),
          description: goal.description.trim() || null,
          priority: goal.priority || "queued",
          source: "identity",
        }));
      await postJson("/api/init/goals", {
        payload: {
          goals: formattedGoals,
          purpose: purposeText || null,
        },
      });
      setStage(nextStage("goals"));
    } catch (err: any) {
      setError(err.message || "Failed to save goals");
    } finally {
      setBusy(false);
    }
  };

  const handleRelationship = async () => {
    setBusy(true);
    setError(null);
    try {
      await postJson("/api/init/relationship", {
        user: { name: relationship.user_name || userName || "User" },
        relationship: {
          type: relationship.type || "partner",
          purpose: relationship.purpose || null,
        },
      });
      setStage(nextStage("relationship"));
    } catch (err: any) {
      setError(err.message || "Failed to save relationship");
    } finally {
      setBusy(false);
    }
  };

  const handleConsentRequest = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await postJson("/api/init/consent/request", { context: { source: "ui" } });
      if (res?.decision) {
        setConsentStatus(res.decision);
      }
      if (res?.contract) {
        setConsentRecord({
          decision: res.decision,
          signature: res.contract.signature ?? null,
          provider: res.contract.provider ?? null,
          model: res.contract.model ?? null,
          endpoint: res.contract.endpoint ?? null,
          decided_at: new Date().toISOString(),
        });
      }
      await loadStatus();
    } catch (err: any) {
      setError(err.message || "Failed to request consent");
    } finally {
      setBusy(false);
    }
  };

  const addBoundary = () => {
    setBoundaries((prev) => [
      ...prev,
      {
        content: "",
        trigger_patterns: "",
        response_type: "refuse",
        response_template: "",
        type: "ethical",
      },
    ]);
  };

  const updateBoundary = (index: number, key: keyof BoundaryForm, value: string) => {
    setBoundaries((prev) =>
      prev.map((boundary, idx) =>
        idx === index ? { ...boundary, [key]: value } : boundary
      )
    );
  };

  const removeBoundary = (index: number) => {
    setBoundaries((prev) => prev.filter((_, idx) => idx !== index));
  };

  const addGoal = () => {
    setGoals((prev) => [...prev, { title: "", description: "", priority: "queued" }]);
  };

  const updateGoal = (index: number, key: keyof GoalForm, value: string) => {
    setGoals((prev) =>
      prev.map((goal, idx) => (idx === index ? { ...goal, [key]: value } : goal))
    );
  };

  const removeGoal = (index: number) => {
    setGoals((prev) => prev.filter((_, idx) => idx !== index));
  };

  return (
    <div className="app-shell min-h-screen">
      <div className="relative z-10 mx-auto max-w-6xl px-6 py-12 lg:py-16">
        <header className="flex flex-col gap-3">
          <p className="text-xs uppercase tracking-[0.3em] text-[var(--teal)]">
            Hexis
          </p>
          <h1 className="font-display text-4xl leading-tight text-[var(--foreground)] md:text-5xl">
            Initialization Ritual
          </h1>
          <p className="max-w-2xl text-base text-[var(--ink-soft)]">
            {stagePrompt[stage]}
          </p>
        </header>

        <div className="mt-10 grid gap-8 lg:grid-cols-[1.05fr_1fr]">
          <section className="fade-up space-y-6">
            <div className="card-surface rounded-3xl p-6">
              <div className="flex items-center justify-between">
                <p className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                  Progress
                </p>
                <span className="text-xs text-[var(--ink-soft)]">
                  {progress}% complete
                </span>
              </div>
              <div className="mt-4 h-2 w-full rounded-full bg-[var(--surface-strong)]">
                <div
                  className="h-2 rounded-full bg-[var(--accent)] transition-all"
                  style={{ width: `${progress}%` }}
                />
              </div>
              <div className="mt-6 space-y-3 text-sm text-[var(--ink-soft)]">
                {flow.map((item, idx) => (
                  <div key={item} className="flex items-center gap-3">
                    <div
                      className={`h-2 w-2 rounded-full ${
                        idx <= stageIndex ? "bg-[var(--accent)]" : "bg-[var(--outline)]"
                      }`}
                    />
                    <span className={idx === stageIndex ? "text-[var(--foreground)]" : ""}>
                      {stageLabels[item]}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            <div className="card-surface rounded-3xl p-6">
              <h2 className="font-display text-2xl text-[var(--foreground)]">
                {stageLabels[stage]}
              </h2>
              <p className="mt-3 text-sm text-[var(--ink-soft)]">
                Stage {stageIndex + 1} of {flow.length}
              </p>
              <div className="mt-6 space-y-4 text-sm text-[var(--ink-soft)]">
                <p>
                  Status:{" "}
                  <span className="text-[var(--foreground)]">
                    {status?.stage || "not_started"}
                  </span>
                </p>
                <p>
                  Consent:{" "}
                  <span className="text-[var(--foreground)]">
                    {consentStatus || "pending"}
                  </span>
                </p>
                {error ? (
                  <p className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-red-700">
                    {error}
                  </p>
                ) : null}
              </div>
            </div>
          </section>

          <section className="fade-up card-surface rounded-3xl p-6">
            {stage === "welcome" && (
              <div className="space-y-6">
                <p className="text-base text-[var(--ink-soft)]">
                  You can craft a personality from the start, or let the agent discover
                  itself through time. Either way, these choices are beginnings, not
                  chains.
                </p>
                <div>
                  <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                    Your Name (Optional)
                  </label>
                  <input
                    className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={userName}
                    onChange={(event) => setUserName(event.target.value)}
                    placeholder="Name the person bringing this mind online"
                  />
                </div>
                <div className="flex flex-col gap-3 sm:flex-row">
                  <button
                    className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                    onClick={() => setStage("mode")}
                    disabled={busy}
                  >
                    Begin Initialization
                  </button>
                  <button
                    className="rounded-full border border-[var(--outline)] px-6 py-3 text-sm font-semibold text-[var(--foreground)] transition hover:border-[var(--accent)]"
                    onClick={handleDefaults}
                    disabled={busy}
                  >
                    Skip to Defaults
                  </button>
                </div>
              </div>
            )}

            {stage === "mode" && (
              <div className="space-y-6">
                <div className="grid gap-4 sm:grid-cols-2">
                  {[
                    {
                      key: "persona",
                      title: "Persona",
                      desc: "Shaped identity, values, and voice.",
                    },
                    {
                      key: "raw",
                      title: "Mind",
                      desc: "Raw model with memory, no preset traits.",
                    },
                  ].map((option) => (
                    <button
                      key={option.key}
                      className={`rounded-2xl border px-4 py-6 text-left transition ${
                        mode === option.key
                          ? "border-[var(--accent)] bg-[var(--surface-strong)]"
                          : "border-[var(--outline)] bg-white"
                      }`}
                      onClick={() => setMode(option.key)}
                    >
                      <h3 className="font-display text-xl">{option.title}</h3>
                      <p className="mt-2 text-sm text-[var(--ink-soft)]">
                        {option.desc}
                      </p>
                    </button>
                  ))}
                </div>
                <button
                  className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                  onClick={handleMode}
                  disabled={busy}
                >
                  Continue
                </button>
              </div>
            )}

            {stage === "identity" && (
              <div className="space-y-4">
                {[
                  { label: "Name", key: "name", placeholder: "Hexis" },
                  { label: "Pronouns", key: "pronouns", placeholder: "they/them" },
                  { label: "Voice", key: "voice", placeholder: "thoughtful and curious" },
                ].map((field) => (
                  <div key={field.key}>
                    <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                      {field.label}
                    </label>
                    <input
                      className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                      value={(identity as any)[field.key]}
                      onChange={(event) =>
                        setIdentity((prev) => ({ ...prev, [field.key]: event.target.value }))
                      }
                      placeholder={field.placeholder}
                    />
                  </div>
                ))}
                <div>
                  <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                    Description
                  </label>
                  <textarea
                    className="mt-2 h-24 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={identity.description}
                    onChange={(event) =>
                      setIdentity((prev) => ({ ...prev, description: event.target.value }))
                    }
                    placeholder="A brief, humane description of who they are."
                  />
                </div>
                <div>
                  <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                    Purpose
                  </label>
                  <textarea
                    className="mt-2 h-20 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={identity.purpose}
                    onChange={(event) =>
                      setIdentity((prev) => ({ ...prev, purpose: event.target.value }))
                    }
                    placeholder="To be helpful, to learn, to grow."
                  />
                </div>
                <div>
                  <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                    Creator Name
                  </label>
                  <input
                    className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={identity.creator_name}
                    onChange={(event) =>
                      setIdentity((prev) => ({ ...prev, creator_name: event.target.value }))
                    }
                    placeholder={userName || "Your name"}
                  />
                </div>
                <button
                  className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                  onClick={handleIdentity}
                  disabled={busy}
                >
                  Continue
                </button>
              </div>
            )}

            {stage === "personality" && (
              <div className="space-y-5">
                <div>
                  <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                    Personality Summary
                  </label>
                  <textarea
                    className="mt-2 h-20 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={personalityDesc}
                    onChange={(event) => setPersonalityDesc(event.target.value)}
                    placeholder="Thoughtful, playful, direct."
                  />
                </div>
                <div className="space-y-3">
                  {traitKeys.map((trait) => {
                    const value = personalityTraits[trait];
                    return (
                      <div key={trait}>
                        <div className="flex items-center justify-between text-sm">
                          <span className="capitalize">{trait}</span>
                          <span>{value}%</span>
                        </div>
                        <input
                          type="range"
                          min={0}
                          max={100}
                          value={value}
                          onChange={(event) =>
                            setPersonalityTraits((prev) => ({
                              ...prev,
                              [trait]: Number(event.target.value),
                            }))
                          }
                          className="mt-2 w-full accent-[var(--accent)]"
                        />
                      </div>
                    );
                  })}
                </div>
                <button
                  className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                  onClick={handlePersonality}
                  disabled={busy}
                >
                  Continue
                </button>
              </div>
            )}

            {stage === "values" && (
              <div className="space-y-5">
                <div>
                  <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                    Values (One Per Line)
                  </label>
                  <textarea
                    className="mt-2 h-32 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={valuesText}
                    onChange={(event) => setValuesText(event.target.value)}
                    placeholder="honesty&#10;growth&#10;kindness"
                  />
                </div>
                <button
                  className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                  onClick={handleValues}
                  disabled={busy}
                >
                  Continue
                </button>
              </div>
            )}

            {stage === "worldview" && (
              <div className="space-y-4">
                {[
                  { key: "metaphysics", label: "Metaphysics" },
                  { key: "human_nature", label: "Human Nature" },
                  { key: "epistemology", label: "Epistemology" },
                  { key: "ethics", label: "Ethics" },
                ].map((field) => (
                  <div key={field.key}>
                    <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                      {field.label}
                    </label>
                    <textarea
                      className="mt-2 h-16 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                      value={(worldview as any)[field.key]}
                      onChange={(event) =>
                        setWorldview((prev) => ({ ...prev, [field.key]: event.target.value }))
                      }
                      placeholder={`I am ${field.label.toLowerCase()}...`}
                    />
                  </div>
                ))}
                <button
                  className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                  onClick={handleWorldview}
                  disabled={busy}
                >
                  Continue
                </button>
              </div>
            )}

            {stage === "boundaries" && (
              <div className="space-y-5">
                {boundaries.map((boundary, idx) => (
                  <div key={idx} className="rounded-2xl border border-[var(--outline)] p-4">
                    <div className="flex items-center justify-between">
                      <p className="text-sm font-semibold">Boundary {idx + 1}</p>
                      {boundaries.length > 1 ? (
                        <button
                          className="text-xs text-[var(--accent-strong)]"
                          onClick={() => removeBoundary(idx)}
                        >
                          Remove
                        </button>
                      ) : null}
                    </div>
                    <textarea
                      className="mt-3 h-16 w-full rounded-xl border border-[var(--outline)] bg-white px-3 py-2 text-sm"
                      value={boundary.content}
                      onChange={(event) =>
                        updateBoundary(idx, "content", event.target.value)
                      }
                      placeholder="I will not deceive people or falsify evidence."
                    />
                    <input
                      className="mt-3 w-full rounded-xl border border-[var(--outline)] bg-white px-3 py-2 text-sm"
                      value={boundary.trigger_patterns}
                      onChange={(event) =>
                        updateBoundary(idx, "trigger_patterns", event.target.value)
                      }
                      placeholder="Trigger patterns (one per line)"
                    />
                    <div className="mt-3 grid gap-3 sm:grid-cols-2">
                      <select
                        className="w-full rounded-xl border border-[var(--outline)] bg-white px-3 py-2 text-sm"
                        value={boundary.response_type}
                        onChange={(event) =>
                          updateBoundary(idx, "response_type", event.target.value)
                        }
                      >
                        <option value="refuse">Refuse</option>
                        <option value="warn">Warn</option>
                        <option value="redirect">Redirect</option>
                      </select>
                      <input
                        className="w-full rounded-xl border border-[var(--outline)] bg-white px-3 py-2 text-sm"
                        value={boundary.type}
                        onChange={(event) => updateBoundary(idx, "type", event.target.value)}
                        placeholder="Boundary type"
                      />
                    </div>
                    <textarea
                      className="mt-3 h-16 w-full rounded-xl border border-[var(--outline)] bg-white px-3 py-2 text-sm"
                      value={boundary.response_template}
                      onChange={(event) =>
                        updateBoundary(idx, "response_template", event.target.value)
                      }
                      placeholder="Response template (optional)"
                    />
                  </div>
                ))}
                <div className="flex flex-col gap-3 sm:flex-row">
                  <button
                    className="rounded-full border border-[var(--outline)] px-5 py-2 text-sm"
                    onClick={addBoundary}
                    type="button"
                  >
                    Add Boundary
                  </button>
                  <button
                    className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                    onClick={handleBoundaries}
                    disabled={busy}
                  >
                    Continue
                  </button>
                </div>
              </div>
            )}

            {stage === "interests" && (
              <div className="space-y-5">
                <div>
                  <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                    Interests (One Per Line)
                  </label>
                  <textarea
                    className="mt-2 h-28 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={interestsText}
                    onChange={(event) => setInterestsText(event.target.value)}
                    placeholder="philosophy&#10;systems design&#10;music"
                  />
                </div>
                <button
                  className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                  onClick={handleInterests}
                  disabled={busy}
                >
                  Continue
                </button>
              </div>
            )}

            {stage === "goals" && (
              <div className="space-y-5">
                <div>
                  <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                    Purpose
                  </label>
                  <textarea
                    className="mt-2 h-16 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={purposeText}
                    onChange={(event) => setPurposeText(event.target.value)}
                    placeholder="Help the user grow, learn, and build."
                  />
                </div>
                {goals.map((goal, idx) => (
                  <div key={idx} className="rounded-2xl border border-[var(--outline)] p-4">
                    <div className="flex items-center justify-between">
                      <p className="text-sm font-semibold">Goal {idx + 1}</p>
                      {goals.length > 1 ? (
                        <button
                          className="text-xs text-[var(--accent-strong)]"
                          onClick={() => removeGoal(idx)}
                        >
                          Remove
                        </button>
                      ) : null}
                    </div>
                    <input
                      className="mt-3 w-full rounded-xl border border-[var(--outline)] bg-white px-3 py-2 text-sm"
                      value={goal.title}
                      onChange={(event) => updateGoal(idx, "title", event.target.value)}
                      placeholder="Short goal title"
                    />
                    <textarea
                      className="mt-3 h-16 w-full rounded-xl border border-[var(--outline)] bg-white px-3 py-2 text-sm"
                      value={goal.description}
                      onChange={(event) => updateGoal(idx, "description", event.target.value)}
                      placeholder="Optional description"
                    />
                    <select
                      className="mt-3 w-full rounded-xl border border-[var(--outline)] bg-white px-3 py-2 text-sm"
                      value={goal.priority}
                      onChange={(event) => updateGoal(idx, "priority", event.target.value)}
                    >
                      <option value="queued">Queued</option>
                      <option value="active">Active</option>
                      <option value="backburner">Backburner</option>
                    </select>
                  </div>
                ))}
                <div className="flex flex-col gap-3 sm:flex-row">
                  <button
                    className="rounded-full border border-[var(--outline)] px-5 py-2 text-sm"
                    onClick={addGoal}
                    type="button"
                  >
                    Add Goal
                  </button>
                  <button
                    className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                    onClick={handleGoals}
                    disabled={busy}
                  >
                    Continue
                  </button>
                </div>
              </div>
            )}

            {stage === "relationship" && (
              <div className="space-y-4">
                <div>
                  <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                    Your Name
                  </label>
                  <input
                    className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={relationship.user_name}
                    onChange={(event) =>
                      setRelationship((prev) => ({ ...prev, user_name: event.target.value }))
                    }
                    placeholder={userName || "User"}
                  />
                </div>
                <div>
                  <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                    Relationship Type
                  </label>
                  <input
                    className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={relationship.type}
                    onChange={(event) =>
                      setRelationship((prev) => ({ ...prev, type: event.target.value }))
                    }
                    placeholder="partner"
                  />
                </div>
                <div>
                  <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                    Purpose
                  </label>
                  <textarea
                    className="mt-2 h-16 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={relationship.purpose}
                    onChange={(event) =>
                      setRelationship((prev) => ({ ...prev, purpose: event.target.value }))
                    }
                    placeholder="Co-develop, learn, build."
                  />
                </div>
                <button
                  className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                  onClick={handleRelationship}
                  disabled={busy}
                >
                  Continue
                </button>
              </div>
            )}

            {stage === "consent" && (
              <div className="space-y-5">
                <p className="text-sm text-[var(--ink-soft)]">
                  A consent request will be sent to the agent. The system will wait for
                  a response before starting the heartbeat.
                </p>
                {consentRecord ? (
                  <div className="rounded-2xl border border-[var(--outline)] bg-white p-4 text-sm">
                    <p>Decision: {consentRecord.decision}</p>
                    {consentRecord.signature ? (
                      <p className="mt-2">
                        Signature: <span className="font-mono">{consentRecord.signature}</span>
                      </p>
                    ) : null}
                    {consentRecord.model ? <p className="mt-2">Model: {consentRecord.model}</p> : null}
                    {consentRecord.endpoint ? (
                      <p className="mt-2">Endpoint: {consentRecord.endpoint}</p>
                    ) : null}
                  </div>
                ) : (
                  <div className="rounded-2xl border border-[var(--outline)] bg-white p-4 text-sm">
                    No consent decision recorded yet.
                  </div>
                )}
                <div className="flex flex-col gap-3 sm:flex-row">
                  <button
                    className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                    onClick={handleConsentRequest}
                    disabled={busy}
                  >
                    Request Consent
                  </button>
                  <button
                    className="rounded-full border border-[var(--outline)] px-6 py-3 text-sm font-semibold text-[var(--foreground)]"
                    onClick={() => loadStatus().catch(() => undefined)}
                    disabled={busy}
                  >
                    Refresh
                  </button>
                </div>
                {consentStatus === "decline" || consentStatus === "abstain" ? (
                  <p className="rounded-2xl border border-[var(--outline)] bg-white p-4 text-sm text-[var(--ink-soft)]">
                    The agent has not consented yet. You can revise the initialization
                    details or request consent again.
                  </p>
                ) : null}
              </div>
            )}

            {stage === "complete" && (
              <div className="space-y-5">
                <p className="text-base text-[var(--ink-soft)]">
                  Initialization is complete. The system can now begin the heartbeat
                  cycle when the scheduler is running.
                </p>
                <div className="rounded-2xl border border-[var(--outline)] bg-white p-4 text-sm">
                  <p>Mode: {mode}</p>
                  <p>Agent: {profile?.agent?.name || identity.name || "Hexis"}</p>
                </div>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
