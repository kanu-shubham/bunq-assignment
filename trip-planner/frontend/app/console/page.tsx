"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import {
  KIND_LABELS,
  type CaseStatus,
  type OpsCase,
  createCase,
  useCases,
} from "../../lib/cases";

const STATUS_ORDER: Record<CaseStatus, number> = {
  blocked: 0,
  running: 1,
  idle: 2,
  done: 3,
};

const STATUS_COLOR: Record<CaseStatus, string> = {
  blocked: "#dc2626",
  running: "#2563eb",
  idle: "#6b7280",
  done: "#16a34a",
};

function formatDuration(ms: number) {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function StatusPill({ status }: { status: CaseStatus }) {
  return (
    <span
      style={{
        background: STATUS_COLOR[status],
        color: "white",
        fontSize: 11,
        padding: "2px 8px",
        borderRadius: 999,
        textTransform: "uppercase",
        letterSpacing: 0.4,
      }}
    >
      {status}
    </span>
  );
}

export default function ConsolePage() {
  const cases = useCases();
  const [filter, setFilter] = useState<"all" | "blocked" | "active">("all");
  const [now, setNow] = useState(Date.now());

  // Tick once a second to keep "blocked for" durations live.
  useMemo(() => {
    if (typeof window === "undefined") return;
    const i = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(i);
  }, []);

  const visible = useMemo(() => {
    const filtered = cases.filter((c) => {
      if (filter === "blocked") return c.status === "blocked";
      if (filter === "active") return c.status !== "done";
      return true;
    });
    // Sort: blocked first (by longest blocked), then by recency.
    return [...filtered].sort((a, b) => {
      const so = STATUS_ORDER[a.status] - STATUS_ORDER[b.status];
      if (so !== 0) return so;
      if (a.status === "blocked" && b.status === "blocked") {
        return (a.blockedSince ?? 0) - (b.blockedSince ?? 0);
      }
      return b.updatedAt - a.updatedAt;
    });
  }, [cases, filter]);

  const stats = useMemo(() => {
    return {
      total: cases.length,
      blocked: cases.filter((c) => c.status === "blocked").length,
      running: cases.filter((c) => c.status === "running").length,
      pendingApprovals: cases.reduce((s, c) => s + c.pendingApprovals, 0),
    };
  }, [cases]);

  return (
    <div style={{ padding: 24, maxWidth: 1400, margin: "0 auto" }}>
      <header style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
        <div>
          <h1 style={{ margin: 0 }}>Operations Console</h1>
          <p style={{ color: "#666", margin: "4px 0 0" }}>
            Supervising {stats.total} agent runs · {stats.blocked} blocked on human ·
            {" "}{stats.pendingApprovals} pending approvals
          </p>
        </div>
        <button
          onClick={() => {
            const c = createCase({
              kind: "settlement-break",
              title: "New case (untitled)",
              owner: "you",
            });
            window.location.href = `/console/${c.id}`;
          }}
          style={{
            background: "#111",
            color: "white",
            border: 0,
            padding: "8px 14px",
            borderRadius: 6,
            cursor: "pointer",
          }}
        >
          + New case
        </button>
      </header>

      <nav style={{ marginTop: 20, marginBottom: 12, display: "flex", gap: 8 }}>
        {(["all", "blocked", "active"] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            style={{
              padding: "6px 12px",
              borderRadius: 6,
              border: `1px solid ${filter === f ? "#111" : "#ddd"}`,
              background: filter === f ? "#111" : "white",
              color: filter === f ? "white" : "#111",
              cursor: "pointer",
              textTransform: "capitalize",
            }}
          >
            {f}
          </button>
        ))}
      </nav>

      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
        <thead>
          <tr style={{ background: "#f6f6f7", textAlign: "left" }}>
            <Th>Case</Th>
            <Th>Kind</Th>
            <Th>Status</Th>
            <Th>Current step</Th>
            <Th>Approvals</Th>
            <Th>Blocked for</Th>
            <Th>Owner</Th>
            <Th>Updated</Th>
          </tr>
        </thead>
        <tbody>
          {visible.map((c) => (
            <CaseRow key={c.id} c={c} now={now} />
          ))}
          {visible.length === 0 && (
            <tr>
              <td colSpan={8} style={{ padding: 24, textAlign: "center", color: "#999" }}>
                No cases match this filter.
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <p style={{ marginTop: 20, color: "#888", fontSize: 12 }}>
        Sorted by: blocked (longest first) → active → done. Click a row to open the case.
      </p>
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th style={{ padding: "10px 12px", borderBottom: "1px solid #e5e5e5", fontWeight: 600 }}>
      {children}
    </th>
  );
}

function CaseRow({ c, now }: { c: OpsCase; now: number }) {
  const blockedFor = c.blockedSince ? formatDuration(now - c.blockedSince) : "—";
  const updatedAgo = formatDuration(now - c.updatedAt);
  return (
    <tr
      onClick={() => {
        window.location.href = `/console/${c.id}`;
      }}
      style={{ cursor: "pointer", borderBottom: "1px solid #f0f0f0" }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "#fafafa")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "white")}
    >
      <td style={td}>
        <Link href={`/console/${c.id}`} style={{ color: "#111", fontWeight: 600 }}>
          {c.id}
        </Link>
        <div style={{ color: "#666", marginTop: 2 }}>{c.title}</div>
      </td>
      <td style={td}>{KIND_LABELS[c.kind]}</td>
      <td style={td}><StatusPill status={c.status} /></td>
      <td style={{ ...td, color: "#444" }}>{c.currentStep}</td>
      <td style={{ ...td, color: c.pendingApprovals > 0 ? "#dc2626" : "#999", fontWeight: c.pendingApprovals > 0 ? 600 : 400 }}>
        {c.pendingApprovals || "—"}
      </td>
      <td style={{ ...td, color: c.status === "blocked" ? "#dc2626" : "#999" }}>{blockedFor}</td>
      <td style={td}>{c.owner}</td>
      <td style={{ ...td, color: "#888" }}>{updatedAgo} ago</td>
    </tr>
  );
}

const td: React.CSSProperties = { padding: "10px 12px", verticalAlign: "top" };
