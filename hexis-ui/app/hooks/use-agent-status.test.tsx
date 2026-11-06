import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { refreshAgentStatus, useAgentStatus } from "./use-agent-status";

function Label({ name }: { name: string }) {
  const { status } = useAgentStatus<{ heartbeat_active?: boolean }>();
  return <span data-testid={name}>{status?.heartbeat_active ? "running" : "idle"}</span>;
}

describe("useAgentStatus", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("gives every component the same status from one request", async () => {
    let active = false;
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ heartbeat_active: active }) }));
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<><Label name="sidebar" /><Label name="overview" /></>);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    active = true;
    await act(() => refreshAgentStatus());
    expect(screen.getByTestId("sidebar").textContent).toBe("running");
    expect(screen.getByTestId("overview").textContent).toBe("running");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
