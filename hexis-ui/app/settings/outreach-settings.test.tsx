import { afterEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { OutreachSettings, type OutreachState } from "./outreach-settings";

afterEach(() => vi.unstubAllGlobals());

const state: OutreachState = {
  destination: "auto",
  available: ["slack", "email"],
  channels: {
    slack: { ready: true, reachable: true, user_id: "U1", name: "Ana Ruiz", source: "recent_dm" },
    telegram: { ready: false, reachable: false, chat_id: null, name: null },
    email: { ready: true, reachable: true, address: "ana@example.com" },
  },
};

it("shows where messages go, offers only what is set up, and saves a choice", async () => {
  const posted: object[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        posted.push(JSON.parse(String(init.body)));
        return Response.json({ ...state, destination: "email" });
      }
      return Response.json(state);
    }),
  );
  render(<OutreachSettings />);
  expect(await screen.findByText("Right now it chooses between Slack / email.")).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: /Telegram/ })).toBeDisabled();
  expect(screen.getByText(/the most recent Slack DM/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("radio", { name: /^Email/ }));
  await waitFor(() => expect(posted).toEqual([{ destination: "email" }]));
  expect(await screen.findByText("Right now its messages reach you on email.")).toBeInTheDocument();
});
