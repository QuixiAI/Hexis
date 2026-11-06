"use client";

import { useCallback, useEffect, useState } from "react";

type Channel = "slack" | "telegram" | "email";
type Destination = "auto" | "inbox" | Channel;

export type OutreachState = {
  destination: Destination;
  available: Channel[];
  channels: {
    slack: {
      ready: boolean;
      reachable: boolean;
      user_id: string | null;
      name: string | null;
      source: "set" | "channel_setting" | "recent_dm" | null;
    };
    telegram: { ready: boolean; reachable: boolean; chat_id: string | null; name: string | null };
    email: { ready: boolean; reachable: boolean; address: string | null };
  };
};

const DESTINATIONS: Destination[] = ["auto", "slack", "telegram", "email", "inbox"];
const CHANNEL_LABEL: Record<Channel, string> = { slack: "Slack", telegram: "Telegram", email: "email" };
const OPTIONS: Record<Destination, { title: string; hint: string }> = {
  auto: {
    title: "Let it decide",
    hint: "With one channel set up, it uses that one. With several, it picks for each message: a chat for quick things, email for longer ones.",
  },
  slack: { title: "Slack DM", hint: "Always your own DM with it." },
  telegram: { title: "Telegram", hint: "Always your Telegram chat with it." },
  email: { title: "Email", hint: "Always your connected Gmail address." },
  inbox: { title: "Dashboard only", hint: "Nothing is sent anywhere else." },
};

async function post(body: object): Promise<OutreachState> {
  const res = await fetch("/api/outreach", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(payload.error || `HTTP ${res.status}`);
  return payload as OutreachState;
}

/** Where messages meant for you go, and who you are in Slack and Telegram. */
export function OutreachSettings() {
  const [state, setState] = useState<OutreachState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [slackUser, setSlackUser] = useState("");
  const [telegramChat, setTelegramChat] = useState("");

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/outreach", { cache: "no-store" });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(payload.error || `HTTP ${res.status}`);
      setState(payload as OutreachState);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async (body: object) => {
    setSaving(true);
    setError(null);
    try {
      setState(await post(body));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  if (!state) {
    return error ? <p role="alert" className="text-sm text-red-700">{error}</p> : null;
  }

  const ready = (d: Destination) => d === "auto" || d === "inbox" || state.channels[d].reachable;
  const slack = state.channels.slack;
  const telegram = state.channels.telegram;
  const names = (channels: Channel[]) => channels.map((c) => CHANNEL_LABEL[c]).join(" / ");
  const whereNow =
    state.destination === "auto"
      ? state.available.length > 1
        ? `Right now it chooses between ${names(state.available)}.`
        : state.available.length === 1
          ? `Right now its messages reach you on ${CHANNEL_LABEL[state.available[0]]}.`
          : "Right now its messages only reach the dashboard."
      : state.destination === "inbox" || !ready(state.destination)
        ? "Right now its messages only reach the dashboard."
        : `Right now its messages reach you on ${CHANNEL_LABEL[state.destination]}.`;

  const slackWho = slack.user_id
    ? slack.source === "set"
      ? `Messages for you go to ${slack.name ?? slack.user_id}'s DM.`
      : slack.source === "channel_setting"
        ? `Messages for you go to ${slack.name ?? slack.user_id}'s DM, the Slack account named in the channel setup. Change it if that isn't you.`
        : `Messages for you go to ${slack.name ?? slack.user_id}'s DM, the most recent Slack DM. Change it if that isn't you.`
    : "It doesn't know which Slack account is yours yet. Enter it here.";

  const input =
    "mt-1 w-full rounded-lg border border-[var(--outline)] bg-white px-3 py-2 text-sm";

  return (
    <div className="space-y-5">
      <div className="rounded-lg border border-[var(--outline)] bg-white p-5">
        <h2 className="text-sm font-semibold">Where its messages to you go</h2>
        <p className="mt-1 text-xs leading-relaxed text-[var(--ink-soft)]">
          When it has something for you, a question, a finished task, a reminder, it always appears in the
          dashboard inbox. Choose where else it reaches you.
        </p>
        <fieldset className="mt-4 space-y-2" disabled={saving}>
          <legend className="sr-only">Where its messages to you go</legend>
          {DESTINATIONS.map((d) => (
            <label
              key={d}
              className={`flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 text-sm ${
                state.destination === d ? "border-[var(--teal)] bg-[var(--teal)]/5" : "border-[var(--outline)]"
              } ${ready(d) ? "" : "opacity-50"}`}
            >
              <input
                type="radio"
                name="outreach-destination"
                className="mt-1"
                checked={state.destination === d}
                disabled={!ready(d)}
                onChange={() => void save({ destination: d })}
              />
              <span>
                <span className="block font-medium">{OPTIONS[d].title}</span>
                <span className="block text-xs text-[var(--ink-soft)]">
                  {ready(d) ? OPTIONS[d].hint : "Not set up yet."}
                </span>
              </span>
            </label>
          ))}
        </fieldset>
        <p role="status" className="mt-3 text-xs text-[var(--ink-soft)]">{whereNow}</p>
      </div>

      {slack.ready ? (
        <div className="rounded-lg border border-[var(--outline)] bg-white p-5">
          <h2 className="text-sm font-semibold">You in Slack</h2>
          <p className="mt-1 text-xs leading-relaxed text-[var(--ink-soft)]">{slackWho}</p>
          <form
            className="mt-3"
            onSubmit={(e) => {
              e.preventDefault();
              void save({ slack_user_id: slackUser });
            }}
          >
            <label className="block text-xs font-medium">
              Your Slack member ID (starts with U)
              <input
                className={input}
                value={slackUser}
                placeholder={slack.user_id ?? "U0123ABCD"}
                onChange={(e) => setSlackUser(e.target.value)}
                spellCheck={false}
              />
            </label>
            <button
              type="submit"
              disabled={saving || !slackUser.trim()}
              className="mt-2 rounded-md border border-[var(--outline)] bg-white px-3 py-1.5 text-xs font-semibold disabled:opacity-50"
            >
              Save
            </button>
          </form>
        </div>
      ) : null}

      {telegram.ready ? (
        <div className="rounded-lg border border-[var(--outline)] bg-white p-5">
          <h2 className="text-sm font-semibold">You on Telegram</h2>
          <p className="mt-1 text-xs leading-relaxed text-[var(--ink-soft)]">
            {telegram.chat_id
              ? `Messages for you go to ${telegram.name ? `${telegram.name}'s chat` : `chat ${telegram.chat_id}`}. Change it if that isn't you.`
              : "It doesn't know your Telegram chat yet. Message it on Telegram once, or enter the chat ID here."}
          </p>
          <form
            className="mt-3"
            onSubmit={(e) => {
              e.preventDefault();
              void save({ telegram_chat_id: telegramChat });
            }}
          >
            <label className="block text-xs font-medium">
              Your Telegram chat ID
              <input
                className={input}
                value={telegramChat}
                placeholder={telegram.chat_id ?? "123456789"}
                onChange={(e) => setTelegramChat(e.target.value)}
                spellCheck={false}
              />
            </label>
            <button
              type="submit"
              disabled={saving || !telegramChat.trim()}
              className="mt-2 rounded-md border border-[var(--outline)] bg-white px-3 py-1.5 text-xs font-semibold disabled:opacity-50"
            >
              Save
            </button>
          </form>
        </div>
      ) : null}
      {error ? <p role="alert" className="text-sm text-red-700">{error}</p> : null}
    </div>
  );
}
