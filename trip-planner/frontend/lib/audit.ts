"use client";

import { useSyncExternalStore } from "react";

export type AuditActor = "agent" | "operator" | "system";

export type AuditEntry = {
  id: string;
  caseId: string;
  timestamp: number;
  actor: AuditActor;
  action: string;
  details?: string;
};

const STORAGE_KEY = "ops-audit-v1";
const listeners = new Set<() => void>();
let cache: AuditEntry[] = [];
let hydrated = false;
let seq = 0;

function load(): AuditEntry[] {
  if (typeof window === "undefined") return [];
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) return [];
  try {
    return JSON.parse(raw) as AuditEntry[];
  } catch {
    return [];
  }
}

function persist() {
  if (typeof window === "undefined") return;
  // Cap at 500 entries so localStorage stays small.
  const trimmed = cache.slice(-500);
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
}

function ensureHydrated() {
  if (hydrated || typeof window === "undefined") return;
  cache = load();
  hydrated = true;
}

function notify() {
  listeners.forEach((fn) => fn());
}

export function appendAudit(
  caseId: string,
  actor: AuditActor,
  action: string,
  details?: string,
) {
  ensureHydrated();
  const last = cache[cache.length - 1];
  // Dedupe: ignore back-to-back identical entries (HITL re-renders fire twice).
  if (
    last &&
    last.caseId === caseId &&
    last.actor === actor &&
    last.action === action &&
    last.details === details &&
    Date.now() - last.timestamp < 2000
  ) {
    return;
  }
  const entry: AuditEntry = {
    id: `aud-${Date.now()}-${seq++}`,
    caseId,
    timestamp: Date.now(),
    actor,
    action,
    details,
  };
  cache = [...cache, entry];
  persist();
  notify();
}

function subscribe(fn: () => void) {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

const EMPTY: AuditEntry[] = [];

export function useAuditFor(caseId: string): AuditEntry[] {
  const all = useSyncExternalStore(
    subscribe,
    () => {
      ensureHydrated();
      return cache;
    },
    () => EMPTY,
  );
  return all.filter((e) => e.caseId === caseId);
}
