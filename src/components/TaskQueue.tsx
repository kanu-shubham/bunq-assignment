import React from "react";
import type { WorkflowListItem } from "../types/domain";
import { StateBadge, TypeBadge, RiskBadge } from "./WorkflowBadge";

interface Props {
  workflows: WorkflowListItem[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  loading: boolean;
}

export function TaskQueue({ workflows, selectedId, onSelect, loading }: Props) {
  if (loading) {
    return (
      <div style={styles.container}>
        <div style={styles.header}>Task Queue</div>
        <div style={{ padding: 16, color: "#6b7280" }}>Loading...</div>
      </div>
    );
  }

  const pending = workflows.filter(
    (w) => w.state === "AWAITING_APPROVAL" || w.state === "DISPUTED" || w.state === "ESCALATED"
  );
  const resolved = workflows.filter(
    (w) => w.state === "RESOLVED" || w.state === "REJECTED"
  );
  const other = workflows.filter((w) => w.state === "OPEN");

  const renderGroup = (title: string, items: WorkflowListItem[]) => {
    if (items.length === 0) return null;
    return (
      <div key={title}>
        <div style={styles.groupHeader}>{title} ({items.length})</div>
        {items.map((wf) => (
          <div
            key={wf.id}
            style={{
              ...styles.item,
              background: selectedId === wf.id ? "#eff6ff" : "white",
              borderLeft: selectedId === wf.id ? "3px solid #2563eb" : "3px solid transparent",
            }}
            onClick={() => onSelect(wf.id)}
          >
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
              <TypeBadge type={wf.type} />
              <StateBadge state={wf.state} />
            </div>
            <div style={styles.title}>{wf.title}</div>
            <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6 }}>
              <RiskBadge score={wf.risk_score} />
              {wf.pending_approvals > 0 && (
                <span style={styles.pendingBadge}>
                  {wf.pending_approvals} pending
                </span>
              )}
            </div>
            <div style={styles.time}>
              {new Date(wf.created_at).toLocaleString()}
            </div>
          </div>
        ))}
      </div>
    );
  };

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        Task Queue
        <span style={{ fontSize: 12, color: "#6b7280", fontWeight: 400 }}>
          {" "}({workflows.length} total)
        </span>
      </div>
      <div style={styles.scroll}>
        {renderGroup("Needs Attention", pending)}
        {renderGroup("Open", other)}
        {renderGroup("Completed", resolved)}
        {workflows.length === 0 && (
          <div style={{ padding: 16, color: "#9ca3af", textAlign: "center" }}>
            No workflows yet.
          </div>
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    width: 300,
    minWidth: 300,
    borderRight: "1px solid #e5e7eb",
    display: "flex",
    flexDirection: "column",
    background: "#f9fafb",
  },
  header: {
    padding: "12px 16px",
    fontWeight: 700,
    fontSize: 14,
    borderBottom: "1px solid #e5e7eb",
    background: "white",
  },
  scroll: {
    overflowY: "auto",
    flex: 1,
  },
  groupHeader: {
    padding: "6px 16px",
    fontSize: 11,
    fontWeight: 700,
    color: "#6b7280",
    textTransform: "uppercase",
    letterSpacing: "0.05em",
    background: "#f3f4f6",
    borderBottom: "1px solid #e5e7eb",
  },
  item: {
    padding: "12px 16px",
    borderBottom: "1px solid #f3f4f6",
    cursor: "pointer",
    transition: "background 0.1s",
  },
  title: {
    fontSize: 13,
    fontWeight: 600,
    color: "#111827",
    marginTop: 4,
  },
  time: {
    fontSize: 11,
    color: "#9ca3af",
    marginTop: 4,
  },
  pendingBadge: {
    background: "#fef3c7",
    color: "#92400e",
    padding: "1px 6px",
    borderRadius: 8,
    fontSize: 11,
    fontWeight: 600,
  },
};
