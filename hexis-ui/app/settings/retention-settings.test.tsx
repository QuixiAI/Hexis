import { afterEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RetentionSettings } from "./retention-settings";

afterEach(() => vi.unstubAllGlobals());

it("shows each limit and saves a change, including keeping one kind forever", async () => {
  const saved = { enabled: true, turn_trace_days: 30, conversation_days: 365, raw_turn_days: 365, last_run: null };
  const posted: object[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      posted.push(body);
      return Response.json({ ...saved, ...body });
    }
    return Response.json(saved);
  }));
  render(<RetentionSettings />);
  expect(await screen.findByLabelText("Turn traces")).toHaveValue(30);
  expect(screen.getByText(/Never deleted here: memories/)).toBeInTheDocument();
  const save = screen.getByRole("button", { name: "Save" });
  expect(save).toBeDisabled();

  fireEvent.change(screen.getByLabelText("Turn traces"), { target: { value: "14" } });
  fireEvent.click(screen.getAllByRole("checkbox", { name: "Keep forever" })[2]);
  fireEvent.click(save);
  await waitFor(() => expect(posted).toEqual([
    { enabled: true, turn_trace_days: 14, conversation_days: 365, raw_turn_days: 0 },
  ]));
  expect(await screen.findByRole("status")).toHaveTextContent("Saved.");
});
