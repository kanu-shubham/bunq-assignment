import React, { useEffect, useState, useCallback } from "react";
import type { WorkflowInstance, UserRole } from "../types/domain";
import { api } from "../api/client";
import { StateMachineViz } from "./StateMachineViz";
import { DiffViewer } from "./DiffViewer";
import { ApprovalCard } from "./ApprovalCard";
import { AuditTrail } from "./AuditTrail";
import { StateBadge, TypeBadge } from "./WorkflowBadge";

interface Props {
  workflowId: string;
  currentUser: string;
  currentRole: UserRole;
  onUpdate: () => void;
}

type Tab = "overview" | "audit";

export function WorkflowDetail({ workflowId, currentUser, currentRole, onUpdate }: Props) {
  const [workflow, setWorkflow] = useState<WorkflowInstance | null>(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<Tab>("overview");

  const reload = useCallback(() => {
    setLoading(true);
    api.getWorkflow(workflowId)
      .then(setWorkflow)
      .finally(() => setLoading(false));
  }, [workflowId]);

  useEffect(() => { reload(); }, [reload]);

  const handleApprove = async (comment: string) => {
    await api.approveWorkflow(workflowId, {
      actor: currentUser,
      role: currentRole,
      decision: "approve",
      comment,
    });
    reload();
    onUpdate();
  };

  const handleDeny = async (comment: string) => {
    await api.approveWorkflow(workflowId, {
      actor: currentUser,
      role: currentRole,
      decision: "deny",
      comment,
    });
    reload();
    onUpdate();
  };

  const handleEscalate = async (reason: string) => {
    await api.escalateWorkflow(workflowId, currentUser, reason);
    reload();
    onUpdate();
  };

  const handleDispute = async (reason: string) => {
    await api.disputeWorkflow(workflowId, currentUser, reason);
    reload();
    onUpdate();
  };

  if (loading && !workflow) {
    return <div style={{ padding: 24, color: "#6b7280" }}>Loading...</div>;
  }

  if (!workflow) {
    return <div style={{ padding: 24, color: "#9ca3af" }}>Select a workflow from the queue.</div>;
  }

  return (
    <div style={styles.container}>
      {/* Header */}
      <div style={styles.pageHeader}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
            <TypeBadge type={workflow.type} />
            <StateBadge state={workflow.state} />
          </div>
          <h2 style={styles.title}>{workflow.title}</h2>
          <p style={styles.desc}>{workflow.description}</p>
          <div style={{ fontSize: 11, color: "#9ca3af" }}>
            Created by <strong>{workflow.created_by}</strong> at {new Date(workflow.created_at).toLocaleString()}
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div style={styles.tabs}>
        {(["overview", "audit"] as Tab[]).map((t) => (
          <button
            key={t}
            style={{ ...styles.tab, ...(tab === t ? styles.activeTab : {}) }}
            onClick={() => setTab(t)}
          >
            {t === "overview" ? "Overview" : "Audit Trail"}
          </button>
        ))}
      </div>

      <div style={styles.body}>
        {tab === "overview" && (
          <>
            <StateMachineViz currentState={workflow.state} />

            <ApprovalCard
              workflow={workflow}
              currentRole={currentRole}
              currentUser={currentUser}
              onApprove={handleApprove}
              onDeny={handleDeny}
              onEscalate={handleEscalate}
              onDispute={handleDispute}
            />

            <DiffViewer
              title="Workflow Payload vs LLM Proposal"
              current={workflow.payload}
              proposed={workflow.llm_proposal}
            />
          </>
        )}

        {tab === "audit" && <AuditTrail workflowId={workflowId} />}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
  },
  pageHeader: {
    padding: "16px 24px",
    borderBottom: "1px solid #e5e7eb",
    background: "white",
  },
  title: {
    margin: 0,
    fontSize: 18,
    fontWeight: 700,
    color: "#111827",
  },
  desc: {
    margin: "4px 0 6px",
    fontSize: 13,
    color: "#6b7280",
  },
  tabs: {
    display: "flex",
    borderBottom: "1px solid #e5e7eb",
    background: "white",
    paddingLeft: 24,
  },
  tab: {
    padding: "8px 16px",
    background: "none",
    border: "none",
    borderBottom: "2px solid transparent",
    cursor: "pointer",
    fontSize: 13,
    color: "#6b7280",
    fontWeight: 500,
  },
  activeTab: {
    color: "#2563eb",
    borderBottomColor: "#2563eb",
    fontWeight: 700,
  },
  body: {
    flex: 1,
    overflowY: "auto",
    padding: "16px 24px",
    background: "#f9fafb",
  },
};
