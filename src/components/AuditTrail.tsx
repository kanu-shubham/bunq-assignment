import React, { useEffect, useState } from "react";
import type { AuditEvent } from "../types/domain";
import { api } from "../api/client";

interface Props {
  workflowId?: string;
}

export function AuditTrail({ workflowId }: Props) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [chainValid, setChainValid] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      api.getAuditTrail(workflowId, 100),
      api.verifyChain(workflowId),
    ])
      .then(([evts, verify]) => {
        setEvents(evts);
        setChainValid(verify.valid);
      })
      .finally(() => setLoading(false));
  }, [workflowId]);

  const EVENT_COLORS: Record<string, string> = {
    WORKFLOW_CREATED: "#2563eb",
    WORKFLOW_STATE_CHANGED: "#7c3aed",
    APPROVAL_REQUEST: "#d97706",
    APPROVAL_GRANTED: "#16a34a",
    APPROVAL_DENIED: "#dc2626",
    OVERRIDE_INVOKED: "#b91c1c",
    COUNTERPARTY_INPUT_REQUIRED: "#0891b2",
    REG_FILING_DRAFTED: "#0d9488",
    EVIDENCE_PINNED: "#65a30d",
  };

  if (loading) {
    return <div style={{ padding: 12, color: "#6b7280" }}>Loading audit trail...</div>;
  }

  return (
    <div>
      <div style={styles.header}>
        Audit Trail
        {chainValid !== null && (
          <span
            style={{
              marginLeft: 12,
              fontSize: 11,
              fontWeight: 700,
              color: chainValid ? "#16a34a" : "#dc2626",
            }}
          >
            {chainValid ? "✓ Chain Intact" : "⚠ Chain BROKEN"}
          </span>
        )}
        <span style={{ fontSize: 11, color: "#9ca3af", marginLeft: 8 }}>
          {events.length} events
        </span>
      </div>
      <div style={styles.timeline}>
        {events.length === 0 && (
          <div style={{ color: "#9ca3af", padding: 8 }}>No events yet.</div>
        )}
        {events.map((ev, i) => (
          <div key={ev.id} style={styles.event}>
            <div
              style={{
                ...styles.dot,
                background: EVENT_COLORS[ev.event_type] || "#6b7280",
              }}
            />
            {i < events.length - 1 && <div style={styles.line} />}
            <div style={styles.content}>
              <div style={styles.eventType}>
                <span
                  style={{
                    color: EVENT_COLORS[ev.event_type] || "#6b7280",
                    fontWeight: 700,
                  }}
                >
                  {ev.event_type}
                </span>
                <span style={styles.actor}>by {ev.actor}</span>
                <span style={styles.time}>
                  {new Date(ev.timestamp).toLocaleString()}
                </span>
              </div>
              <div style={styles.hash}>
                hash: {ev.hash.slice(0, 16)}...
              </div>
              {Object.keys(ev.payload).length > 0 && (
                <details style={{ marginTop: 2 }}>
                  <summary style={{ fontSize: 11, color: "#6b7280", cursor: "pointer" }}>
                    Payload
                  </summary>
                  <pre style={styles.pre}>
                    {JSON.stringify(ev.payload, null, 2)}
                  </pre>
                </details>
              )}
              {ev.llm_output && (
                <details style={{ marginTop: 2 }}>
                  <summary style={{ fontSize: 11, color: "#7c3aed", cursor: "pointer" }}>
                    LLM Output
                  </summary>
                  <pre style={{ ...styles.pre, background: "#faf5ff", color: "#4c1d95" }}>
                    {ev.llm_output}
                  </pre>
                </details>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  header: {
    padding: "10px 0 6px",
    fontWeight: 700,
    fontSize: 13,
    borderBottom: "1px solid #e5e7eb",
    marginBottom: 12,
    display: "flex",
    alignItems: "center",
  },
  timeline: {
    position: "relative",
    paddingLeft: 20,
  },
  event: {
    position: "relative",
    paddingLeft: 20,
    paddingBottom: 16,
  },
  dot: {
    position: "absolute",
    left: -6,
    top: 4,
    width: 12,
    height: 12,
    borderRadius: "50%",
  },
  line: {
    position: "absolute",
    left: -1,
    top: 16,
    bottom: 0,
    width: 2,
    background: "#e5e7eb",
  },
  content: {
    background: "#f9fafb",
    borderRadius: 6,
    padding: "6px 10px",
    border: "1px solid #f3f4f6",
  },
  eventType: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    fontSize: 12,
  },
  actor: {
    color: "#6b7280",
    fontSize: 11,
  },
  time: {
    color: "#9ca3af",
    fontSize: 11,
    marginLeft: "auto",
  },
  hash: {
    fontSize: 10,
    color: "#9ca3af",
    fontFamily: "monospace",
    marginTop: 2,
  },
  pre: {
    fontSize: 11,
    background: "#f3f4f6",
    borderRadius: 4,
    padding: 6,
    overflow: "auto",
    maxHeight: 200,
    margin: "4px 0 0",
  },
};
