"use client";

import { useEffect, useRef } from "react";
import { createRoot } from "react-dom/client";

/**
 * Ask the person to confirm, in the page.
 *
 * Replaces window.confirm, which some embedded browsers (the Claude desktop
 * app's browser among them) answer "no" at once without showing anything, so
 * every action behind it silently did nothing. Resolves true only when the
 * confirm button is pressed; Escape, Cancel and the backdrop resolve false.
 */
export function confirmAction(
  message: string,
  options: { confirmLabel?: string; cancelLabel?: string; destructive?: boolean } = {},
): Promise<boolean> {
  if (typeof document === "undefined") return Promise.resolve(false);
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  return new Promise((resolve) => {
    const close = (answer: boolean) => {
      root.unmount();
      host.remove();
      resolve(answer);
    };
    root.render(
      <ConfirmDialog
        message={message}
        confirmLabel={options.confirmLabel ?? "OK"}
        cancelLabel={options.cancelLabel ?? "Cancel"}
        destructive={options.destructive ?? false}
        onClose={close}
      />,
    );
  });
}

function ConfirmDialog({
  message,
  confirmLabel,
  cancelLabel,
  destructive,
  onClose,
}: {
  message: string;
  confirmLabel: string;
  cancelLabel: string;
  destructive: boolean;
  onClose: (answer: boolean) => void;
}) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    confirmRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose(false);
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus?.();
    };
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/30 px-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose(false);
      }}
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-describedby="confirm-action-message"
        className="w-full max-w-sm rounded-2xl border border-[var(--outline)] bg-white p-5 shadow-xl"
      >
        <p id="confirm-action-message" className="text-sm leading-relaxed text-[var(--foreground)]">
          {message}
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => onClose(false)}
            className="rounded-full border border-[var(--outline)] bg-white px-4 py-2 text-sm"
          >
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            type="button"
            onClick={() => onClose(true)}
            className={`rounded-full px-4 py-2 text-sm font-semibold text-white ${
              destructive ? "bg-red-700 hover:bg-red-800" : "bg-[var(--foreground)] hover:bg-[var(--accent-strong)]"
            }`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
