"use client";

import { useSyncExternalStore } from "react";

/**
 * One copy of /api/status for every component on the page.
 *
 * The sidebar and the dashboard used to poll it separately, 15 s apart at
 * worst, so one could say Running while the other said Idle. They now read
 * the same snapshot; refreshAgentStatus() updates both at once.
 */

export type AgentStatusSnapshot<T> = {
  status: T | null;
  /** True once the first request has finished, whether or not it succeeded. */
  loaded: boolean;
  /** True when the last request could not reach the API at all. */
  unreachable: boolean;
};

const POLL_MS = 15000;

let snapshot: AgentStatusSnapshot<unknown> = { status: null, loaded: false, unreachable: false };
let inflight: Promise<void> | null = null;
let stopPolling: (() => void) | null = null;
const listeners = new Set<() => void>();

export function refreshAgentStatus(): Promise<void> {
  if (inflight) return inflight;
  inflight = (async () => {
    let status = snapshot.status;
    let unreachable = false;
    try {
      const response = await fetch("/api/status", { cache: "no-store" });
      if (response.ok) status = await response.json();
      else unreachable = status === null;
    } catch {
      // Keep the last good status; the page owns visible connection errors.
      unreachable = status === null;
    } finally {
      snapshot = { status, loaded: true, unreachable };
      inflight = null;
      listeners.forEach((listener) => listener());
    }
  })();
  return inflight;
}

function startPolling(): () => void {
  const refreshWhenVisible = () => {
    if (document.visibilityState === "visible") void refreshAgentStatus();
  };
  const interval = window.setInterval(refreshWhenVisible, POLL_MS);
  document.addEventListener("visibilitychange", refreshWhenVisible);
  window.addEventListener("focus", refreshWhenVisible);
  return () => {
    window.clearInterval(interval);
    document.removeEventListener("visibilitychange", refreshWhenVisible);
    window.removeEventListener("focus", refreshWhenVisible);
  };
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (listeners.size === 1) {
    stopPolling = startPolling();
    void refreshAgentStatus();
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      stopPolling?.();
      stopPolling = null;
    }
  };
}

const serverSnapshot: AgentStatusSnapshot<unknown> = { status: null, loaded: false, unreachable: false };

export function useAgentStatus<T>(): AgentStatusSnapshot<T> {
  return useSyncExternalStore(
    subscribe,
    () => snapshot as AgentStatusSnapshot<T>,
    () => serverSnapshot as AgentStatusSnapshot<T>,
  );
}
