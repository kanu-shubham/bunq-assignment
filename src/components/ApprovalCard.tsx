import React, { useState } from "react";
import type { WorkflowInstance, UserRole } from "../types/domain";
import { RiskBadge } from "./WorkflowBadge";

interface Props {
  workflow: WorkflowInstance;
  currentRole: UserRole;
  currentUser: string;
  onApprove: (comment: string) => Promise<void>;
  onDeny: (comment: string) => Promise<void>;
  onEscalate: (reason: string) => Promise<void>;
  onDispute: (reason: string) => Promise<void>;
}

export function ApprovalCard({
  workflow,
  currentRole,
  currentUser,
  onApprove,
  onDeny,
  onEscalate,
  onDispute,
}: Props) {
  const [comment, setComment] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const gate = workflow.approval_gate;
  const canAct =
    workflow.state === "AWAITING_APPROVAL" &&
    gate &&
    gate.status === "PENDING" &&
    gate.required_roles.includes(currentRole) &&
    !gate.approvals.find((a) => a.actor === currentUser);

  const isSelfApproval = workflow.created_by === currentUser;

  const wrap = async (fn: () => Promise<void>) => {
    setLoading(true);
    setError("");
    try {
      await fn();
    } catch (e: any) {
      setError(e.message || "Action failed");
    } finally {
      setLoading(false);
    }
  };

  const approveCount = gate?.approvals.filter((a) => a.decision === "approve").length ?? 0;
  const quorum = gate?.quorum ?? 1;

  return (
    <div style={styles.card}>
      <div style={styles.header}>
        <span style={{ fontWeight: 700 }}>Approval Required</span>
        <RiskBadge score={workflow.risk_score} />
      </div>

      {/* LLM reasoning */}
      <div style={styles.section}>
        <div style={styles.sectionTitle}>LLM Analysis &amp; Recommendation</div>
        <div style={styles.reasoning}>{workflow.llm_reasoning}</div>
      </div>

      {/* N-of-M gate */}
      {gate && (
        <div style={styles.section}>
          <div style={styles.sectionTitle}>
            Approval Gate: {approveCount} / {quorum} required ({gate.required_roles.join(", ")})
          </div>
          <div style={styles.progress}>
            <div
              style={{
                ...styles.progressBar,
                width: `${Math.min((approveCount / quorum) * 100, 100)}%`,
              }}
            />
          </div>
          {gate.approvals.length > 0 && (
            <div style={{ marginTop: 8 }}>
              {gate.approvals.map((a, i) => (
                <div key={i} style={styles.approvalRow}>
                  <span style={{ fontWeight: 600 }}>{a.actor}</span>
                  <span style={{ color: "#6b7280", marginLeft: 4 }}>({a.role})</span>
                  <span
                    style={{
                      marginLeft: 8,
                      color: a.decision === "approve" ? "#16a34a" : "#dc2626",
                      fontWeight: 700,
                    }}
                  >
                    {a.decision === "approve" ? "✓ Approved" : "✗ Denied"}
                  </span>
                  {a.comment && (
                    <span style={{ marginLeft: 8, color: "#6b7280", fontStyle: "italic" }}>
                      "{a.comment}"
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
          {gate.deadline && (
            <div style={{ fontSize: 11, color: "#9ca3af", marginTop: 4 }}>
              Deadline: {new Date(gate.deadline).toLocaleString()}
            </div>
          )}
        </div>
      )}

      {/* Self-approval warning */}
      {isSelfApproval && (
        <div style={styles.warning}>
          Anti-self-approval: you created this workflow and cannot approve it.
        </div>
      )}

      {/* Role warning */}
      {!isSelfApproval && gate && !gate.required_roles.includes(currentRole) && (
        <div style={styles.info}>
          Your role ({currentRole}) is not in the required approvers for this gate.
        </div>
      )}

      {/* Action area */}
      {canAct && !isSelfApproval && (
        <div style={styles.actions}>
          <textarea
            style={styles.textarea}
            placeholder="Comment (optional)..."
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            rows={2}
          />
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              style={{ ...styles.btn, ...styles.approveBtn }}
              disabled={loading}
              onClick={() => wrap(() => onApprove(comment))}
            >
              {loading ? "..." : "✓ Approve"}
            </button>
            <button
              style={{ ...styles.btn, ...styles.denyBtn }}
              disabled={loading}
              onClick={() => wrap(() => onDeny(comment))}
            >
              ✗ Deny
            </button>
            <button
              style={{ ...styles.btn, ...styles.escalateBtn }}
              disabled={loading}
              onClick={() => wrap(() => onEscalate(comment || "Escalated by reviewer"))}
            >
              ↑ Escalate
            </button>
            <button
              style={{ ...styles.btn, ...styles.disputeBtn }}
              disabled={loading}
              onClick={() => wrap(() => onDispute(comment || "Disputed by reviewer"))}
            >
              ⚡ Dispute
            </button>
          </div>
          {error && <div style={styles.error}>{error}</div>}
        </div>
      )}

      {workflow.state === "RESOLVED" && (
        <div style={{ ...styles.info, background: "#f0fdf4", color: "#166534" }}>
          Workflow resolved.
        </div>
      )}
      {workflow.state === "REJECTED" && (
        <div style={{ ...styles.info, background: "#fef2f2", color: "#991b1b" }}>
          Workflow rejected.
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  card: {
    border: "1px solid #e5e7eb",
    borderRadius: 8,
    overflow: "hidden",
    marginBottom: 16,
  },
  header: {
    padding: "10px 14px",
    background: "#fef3c7",
    borderBottom: "1px solid #fde68a",
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
  },
  section: {
    padding: "10px 14px",
    borderBottom: "1px solid #f3f4f6",
  },
  sectionTitle: {
    fontSize: 11,
    fontWeight: 700,
    color: "#6b7280",
    textTransform: "uppercase",
    letterSpacing: "0.05em",
    marginBottom: 6,
  },
  reasoning: {
    fontSize: 13,
    color: "#374151",
    lineHeight: 1.5,
    background: "#f9fafb",
    padding: "8px 10px",
    borderRadius: 6,
    fontStyle: "italic",
  },
  progress: {
    height: 6,
    background: "#e5e7eb",
    borderRadius: 3,
    overflow: "hidden",
    marginTop: 6,
  },
  progressBar: {
    height: "100%",
    background: "#16a34a",
    borderRadius: 3,
    transition: "width 0.3s ease",
  },
  approvalRow: {
    fontSize: 12,
    padding: "2px 0",
  },
  actions: {
    padding: "12px 14px",
    display: "flex",
    flexDirection: "column",
    gap: 8,
  },
  textarea: {
    width: "100%",
    padding: "6px 8px",
    border: "1px solid #d1d5db",
    borderRadius: 6,
    fontSize: 12,
    resize: "vertical",
    boxSizing: "border-box",
  },
  btn: {
    padding: "6px 14px",
    borderRadius: 6,
    border: "none",
    fontWeight: 700,
    fontSize: 12,
    cursor: "pointer",
  },
  approveBtn: { background: "#16a34a", color: "white" },
  denyBtn: { background: "#dc2626", color: "white" },
  escalateBtn: { background: "#7c3aed", color: "white" },
  disputeBtn: { background: "#d97706", color: "white" },
  warning: {
    margin: "8px 14px",
    padding: "6px 10px",
    background: "#fef2f2",
    color: "#991b1b",
    borderRadius: 6,
    fontSize: 12,
    border: "1px solid #fecaca",
  },
  info: {
    margin: "8px 14px",
    padding: "6px 10px",
    background: "#eff6ff",
    color: "#1e40af",
    borderRadius: 6,
    fontSize: 12,
    border: "1px solid #bfdbfe",
  },
  error: {
    color: "#dc2626",
    fontSize: 12,
    marginTop: 4,
  },
};
