import { act, fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { confirmAction } from "./confirm";

describe("confirmAction", () => {
  it("resolves true only when confirmed, and removes itself", async () => {
    let answer: Promise<boolean> | undefined;
    await act(async () => {
      answer = confirmAction("Clear this conversation?", { confirmLabel: "Clear" });
    });
    expect(screen.getByRole("alertdialog")).toHaveTextContent("Clear this conversation?");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    });
    await expect(answer).resolves.toBe(true);
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("resolves false on Escape", async () => {
    let answer: Promise<boolean> | undefined;
    await act(async () => {
      answer = confirmAction("Disconnect?");
    });
    await act(async () => {
      fireEvent.keyDown(document, { key: "Escape" });
    });
    await expect(answer).resolves.toBe(false);
  });
});
