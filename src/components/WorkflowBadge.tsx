import React from "react";
import type { WorkflowState, WorkflowType } from "../types/domain";

const STATE_COLORS: Record<WorkflowState, string> = {
  OPEN: "#6b7280",
  AWAITING_APPROVAL: "#d97706",
  DISPUTED: "#dc2626",
  ESCALATED: "#7c3aed",
  RESOLVED: "#16a34a",
  REJECTED: "#b91c1c",
};

const TYPE_ICONS: Record<WorkflowType, string> = {
  SETTLEMENT: "💱",
  RECONCILIATION: "🔍",
  DISPUTE: "⚖️",
  MARGIN_CALL: "📉",
  REGULATORY_FILING: "📋",
};

export function StateBadge({ state }: { state: WorkflowState }) {
  return (
    <span
      style={{
        background: STATE_COLORS[state],
        color: "white",
        padding: "2px 8px",
        borderRadius: 12,
        fontSize: 12,
        fontWeight: 600,
        whiteSpace: "nowrap",
      }}
    >
      {state.replace("_", " ")}
    </span>
  );
}

export function TypeBadge({ type }: { type: WorkflowType }) {
  return (
    <span style={{ fontSize: 13, color: "#374151" }}>
      {TYPE_ICONS[type]} {type.replace("_", " ")}
    </span>
  );
}

export function RiskBadge({ score }: { score: number }) {
  const color = score >= 0.7 ? "#dc2626" : score >= 0.4 ? "#d97706" : "#16a34a";
  const label = score >= 0.7 ? "HIGH" : score >= 0.4 ? "MEDIUM" : "LOW";
  return (
    <span
      style={{
        background: color,
        color: "white",
        padding: "2px 8px",
        borderRadius: 12,
        fontSize: 11,
        fontWeight: 700,
      }}
    >
      RISK: {label} ({(score * 100).toFixed(0)}%)
    </span>
  );
}
