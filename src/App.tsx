import React, { useState, useEffect, useCallback } from "react";
import type { WorkflowListItem, UserRole, AGUIEvent } from "./types/domain";
import { api, createEventSource } from "./api/client";
import { TaskQueue } from "./components/TaskQueue";
import { WorkflowDetail } from "./components/WorkflowDetail";
import { CreateWorkflowModal } from "./components/CreateWorkflowModal";
import "./App.css";

const ROLES: UserRole[] = [
  "TRADER",
  "RISK",
  "COMPLIANCE",
  "OPERATIONS",
  "LEGAL",
  "ADMIN",
];

const USERS: Record<UserRole, string> = {
  TRADER: "alice_trader",
  RISK: "bob_risk",
  COMPLIANCE: "carol_compliance",
  OPERATIONS: "dave_ops",
  LEGAL: "eve_legal",
  ADMIN: "frank_admin",
};

export default function App(): JSX.Element {
  const [workflows, setWorkflows] = useState<WorkflowListItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [currentRole, setCurrentRole] = useState<UserRole>("OPERATIONS");
  const [showCreate, setShowCreate] = useState(false);
  const [liveEvents, setLiveEvents] = useState<string[]>([]);

  const currentUser = USERS[currentRole];

  const loadWorkflows = useCallback(() => {
    setLoading(true);
    api
      .listWorkflows({ limit: 50 })
      .then(setWorkflows)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    loadWorkflows();
  }, [loadWorkflows]);

  // SSE live event subscription
  useEffect(() => {
    const es = createEventSource();
    es.onmessage = (e) => {
      try {
        const event: AGUIEvent = JSON.parse(e.data);
        if (event.event_type !== "CONNECTED") {
          const msg = `[${new Date().toLocaleTimeString()}] ${event.event_type}${event.workflow_id ? ` (${event.workflow_id.slice(0, 8)}...)` : ""}`;
          setLiveEvents((prev) => [msg, ...prev].slice(0, 20));
          // Refresh workflow list on state-changing events
          if (
            ["APPROVAL_GRANTED", "APPROVAL_DENIED", "WORKFLOW_STATE_CHANGED"].includes(
              event.event_type
            )
          ) {
            loadWorkflows();
          }
        }
      } catch {
        // Ignore parse errors (heartbeats)
      }
    };
    es.onerror = () => {}; // Suppress console noise for reconnects
    return () => es.close();
  }, [loadWorkflows]);

  return (
    <div style={styles.shell}>
      {/* Top bar */}
      <div style={styles.topbar}>
        <div style={styles.brand}>
          <span style={styles.brandIcon}>⚡</span>
          HITL Financial Workbench
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={styles.label}>Acting as:</span>
          <select
            style={styles.roleSelect}
            value={currentRole}
            onChange={(e) => setCurrentRole(e.target.value as UserRole)}
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r} ({USERS[r]})
              </option>
            ))}
          </select>
          <button style={styles.newBtn} onClick={() => setShowCreate(true)}>
            + New Workflow
          </button>
        </div>
      </div>

      <div style={styles.main}>
        {/* Task queue sidebar */}
        <TaskQueue
          workflows={workflows}
          selectedId={selectedId}
          onSelect={setSelectedId}
          loading={loading}
        />

        {/* Detail panel */}
        <div style={styles.detail}>
          {selectedId ? (
            <WorkflowDetail
              workflowId={selectedId}
              currentUser={currentUser}
              currentRole={currentRole}
              onUpdate={loadWorkflows}
            />
          ) : (
            <div style={styles.empty}>
              <div style={styles.emptyIcon}>📋</div>
              <div style={styles.emptyTitle}>Select a workflow to review</div>
              <div style={styles.emptySubtitle}>
                Choose from the task queue on the left, or create a new workflow.
              </div>
              <button
                style={{ ...styles.newBtn, marginTop: 16 }}
                onClick={() => setShowCreate(true)}
              >
                + Create Demo Workflow
              </button>
            </div>
          )}
        </div>

        {/* Live events panel */}
        <div style={styles.eventPanel}>
          <div style={styles.eventHeader}>Live Events (SSE)</div>
          {liveEvents.length === 0 && (
            <div style={{ padding: "8px 12px", color: "#9ca3af", fontSize: 11 }}>
              Waiting for events...
            </div>
          )}
          {liveEvents.map((msg, i) => (
            <div key={i} style={styles.eventItem}>
              {msg}
            </div>
          ))}
        </div>
      </div>

      {showCreate && (
        <CreateWorkflowModal
          currentUser={currentUser}
          onCreated={loadWorkflows}
          onClose={() => setShowCreate(false)}
        />
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  shell: {
    height: "100vh",
    display: "flex",
    flexDirection: "column",
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    background: "#f9fafb",
    overflow: "hidden",
  },
  topbar: {
    height: 52,
    background: "#1e293b",
    color: "white",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "0 20px",
    flexShrink: 0,
    zIndex: 100,
  },
  brand: {
    fontSize: 15,
    fontWeight: 700,
    letterSpacing: "-0.02em",
    display: "flex",
    alignItems: "center",
    gap: 8,
  },
  brandIcon: {
    fontSize: 18,
  },
  label: {
    fontSize: 12,
    color: "#94a3b8",
  },
  roleSelect: {
    padding: "4px 8px",
    borderRadius: 6,
    border: "1px solid #475569",
    background: "#334155",
    color: "white",
    fontSize: 12,
    cursor: "pointer",
  },
  newBtn: {
    padding: "6px 14px",
    borderRadius: 6,
    background: "#2563eb",
    color: "white",
    border: "none",
    cursor: "pointer",
    fontWeight: 700,
    fontSize: 12,
  },
  main: {
    flex: 1,
    display: "flex",
    overflow: "hidden",
  },
  detail: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
    minWidth: 0,
  },
  eventPanel: {
    width: 240,
    borderLeft: "1px solid #e5e7eb",
    background: "#f8fafc",
    overflow: "hidden",
    display: "flex",
    flexDirection: "column",
  },
  eventHeader: {
    padding: "10px 12px",
    fontWeight: 700,
    fontSize: 11,
    color: "#374151",
    borderBottom: "1px solid #e5e7eb",
    background: "white",
    textTransform: "uppercase",
    letterSpacing: "0.05em",
  },
  eventItem: {
    padding: "4px 12px",
    fontSize: 10,
    color: "#6b7280",
    borderBottom: "1px solid #f1f5f9",
    fontFamily: "monospace",
    wordBreak: "break-all",
  },
  empty: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    color: "#9ca3af",
    padding: 40,
  },
  emptyIcon: {
    fontSize: 48,
    marginBottom: 12,
  },
  emptyTitle: {
    fontSize: 16,
    fontWeight: 600,
    color: "#374151",
    marginBottom: 6,
  },
  emptySubtitle: {
    fontSize: 13,
    color: "#6b7280",
    textAlign: "center",
    maxWidth: 320,
  },
};
