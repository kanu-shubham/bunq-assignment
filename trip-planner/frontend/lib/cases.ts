"use client";

import { useSyncExternalStore } from "react";

export type CaseKind =
  | "settlement-break"
  | "margin-dispute"
  | "reconciliation"
  | "kyc-refresh";

export type CaseStatus = "idle" | "running" | "blocked" | "done";

export type OpsCase = {
  id: string;
  kind: CaseKind;
  title: string;
  owner: string;
  status: CaseStatus;
  currentStep: string;
  pendingApprovals: number;
  blockedSince: number | null;
  createdAt: number;
  updatedAt: number;
};

export const KIND_LABELS: Record<CaseKind, string> = {
  "settlement-break": "Settlement Break",
  "margin-dispute": "Margin Dispute",
  reconciliation: "Reconciliation",
  "kyc-refresh": "KYC Refresh",
};

const STORAGE_KEY = "ops-cases-v1";
const listeners = new Set<() => void>();
let cache: OpsCase[] = [];
let hydrated = false;

function seed(): OpsCase[] {
  const now = Date.now();
  const min = 60_000;
  return [
    {
      id: "CASE-1041",
      kind: "margin-dispute",
      title: "GS vs MS — IM variance $1.2M on rates portfolio",
      owner: "a.kapoor",
      status: "blocked",
      currentStep: "Awaiting reviewer approval on proposed recalculation",
      pendingApprovals: 1,
      blockedSince: now - 42 * min,
      createdAt: now - 3 * 60 * min,
      updatedAt: now - 42 * min,
    },
    {
      id: "CASE-1042",
      kind: "settlement-break",
      title: "DTCC fail — UST 10Y, 5,000 lots, T+1",
      owner: "j.li",
      status: "blocked",
      currentStep: "Operator must confirm counterparty contact draft",
      pendingApprovals: 1,
      blockedSince: now - 17 * min,
      createdAt: now - 90 * min,
      updatedAt: now - 17 * min,
    },
    {
      id: "CASE-1043",
      kind: "reconciliation",
      title: "Custodian break — BNY vs internal ledger, EUR cash",
      owner: "m.okafor",
      status: "running",
      currentStep: "Matching unallocated transfers (3 of 47)",
      pendingApprovals: 0,
      blockedSince: null,
      createdAt: now - 12 * min,
      updatedAt: now - 30_000,
    },
    {
      id: "CASE-1044",
      kind: "kyc-refresh",
      title: "Periodic refresh — Acme Trading Ltd (high risk)",
      owner: "s.patel",
      status: "running",
      currentStep: "Pulling beneficial ownership chain",
      pendingApprovals: 0,
      blockedSince: null,
      createdAt: now - 6 * min,
      updatedAt: now - 90_000,
    },
    {
      id: "CASE-1045",
      kind: "settlement-break",
      title: "Euroclear fail — IG corporate bonds, partial delivery",
      owner: "a.kapoor",
      status: "idle",
      currentStep: "Not yet started",
      pendingApprovals: 0,
      blockedSince: null,
      createdAt: now - 2 * min,
      updatedAt: now - 2 * min,
    },
    {
      id: "CASE-1039",
      kind: "margin-dispute",
      title: "DB vs JPM — VM dispute on FX options book",
      owner: "j.li",
      status: "done",
      currentStep: "Resolved — counterparty accepted recalculation",
      pendingApprovals: 0,
      blockedSince: null,
      createdAt: now - 6 * 60 * min,
      updatedAt: now - 70 * min,
    },
  ];
}

function load(): OpsCase[] {
  if (typeof window === "undefined") return [];
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) {
    const s = seed();
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
    return s;
  }
  try {
    return JSON.parse(raw) as OpsCase[];
  } catch {
    return seed();
  }
}

function persist() {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(cache));
}

function ensureHydrated() {
  if (hydrated || typeof window === "undefined") return;
  cache = load();
  hydrated = true;
}

function notify() {
  listeners.forEach((fn) => fn());
}

export function listCases(): OpsCase[] {
  ensureHydrated();
  return cache;
}

export function getCase(id: string): OpsCase | undefined {
  ensureHydrated();
  return cache.find((c) => c.id === id);
}

export function updateCase(id: string, patch: Partial<OpsCase>) {
  ensureHydrated();
  cache = cache.map((c) =>
    c.id === id ? { ...c, ...patch, updatedAt: Date.now() } : c,
  );
  persist();
  notify();
}

export function createCase(input: Pick<OpsCase, "kind" | "title" | "owner">) {
  ensureHydrated();
  const id = `CASE-${1046 + cache.filter((c) => c.id.startsWith("CASE-10")).length}`;
  const now = Date.now();
  const c: OpsCase = {
    id,
    ...input,
    status: "idle",
    currentStep: "Not yet started",
    pendingApprovals: 0,
    blockedSince: null,
    createdAt: now,
    updatedAt: now,
  };
  cache = [c, ...cache];
  persist();
  notify();
  return c;
}

function subscribe(fn: () => void) {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

const EMPTY: OpsCase[] = [];

export function useCases(): OpsCase[] {
  return useSyncExternalStore(
    subscribe,
    () => {
      ensureHydrated();
      return cache;
    },
    () => EMPTY,
  );
}

export function useCase(id: string): OpsCase | undefined {
  const all = useCases();
  return all.find((c) => c.id === id);
}
