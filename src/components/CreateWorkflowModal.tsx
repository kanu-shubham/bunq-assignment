import React, { useState } from "react";
import type { WorkflowType, UserRole } from "../types/domain";
import { api } from "../api/client";

interface Props {
  currentUser: string;
  onCreated: () => void;
  onClose: () => void;
}

const SAMPLE_PAYLOADS: Record<WorkflowType, Record<string, unknown>> = {
  SETTLEMENT: {
    amount: 2500000,
    currency: "USD",
    counterparty: "DEUTSCHE_BANK",
    trade_id: "TRD-2024-0042",
    is_cross_border: true,
    value_date: new Date(Date.now() + 86400000).toISOString().split("T")[0],
  },
  RECONCILIATION: {
    break_amount: 12450.0,
    currency: "USD",
    internal_balance: 12450.0,
    custodian_balance: 12500.0,
    tolerance: 0.01,
    rec_date: new Date().toISOString().split("T")[0],
  },
  DISPUTE: {
    dispute_reason: "Counterparty disputes margin call based on stale pricing",
    original_trade_id: "TRD-2024-0039",
    disputed_amount: 180000,
    currency: "USD",
    csa_reference: "CSA-2023-001",
    legal_action_threatened: false,
  },
  MARGIN_CALL: {
    call_amount: 1800000,
    currency: "USD",
    portfolio_id: "PTFL-EUR-FX-001",
    trigger: "VaR_breach_3pct",
    current_collateral: 4200000,
    disputed: false,
  },
  REGULATORY_FILING: {
    regime: "EMIR",
    uti: "UTI-2024-EUR-0042",
    trade_repository: "DTCC_EU",
    reporting_counterparty: "BUNQ_NV",
    asset_class: "FX",
    late_filing: false,
  },
};

const TYPES: WorkflowType[] = [
  "SETTLEMENT",
  "RECONCILIATION",
  "DISPUTE",
  "MARGIN_CALL",
  "REGULATORY_FILING",
];

const TYPE_TITLES: Record<WorkflowType, string> = {
  SETTLEMENT: "T+1 Settlement Break",
  RECONCILIATION: "Daily Reconciliation Exception",
  DISPUTE: "Margin Call Dispute",
  MARGIN_CALL: "Cross-border Margin Call",
  REGULATORY_FILING: "EMIR Regulatory Filing",
};

export function CreateWorkflowModal({ currentUser, onCreated, onClose }: Props) {
  const [type, setType] = useState<WorkflowType>("SETTLEMENT");
  const [title, setTitle] = useState(TYPE_TITLES["SETTLEMENT"]);
  const [description, setDescription] = useState("");
  const [payloadText, setPayloadText] = useState(
    JSON.stringify(SAMPLE_PAYLOADS["SETTLEMENT"], null, 2)
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleTypeChange = (t: WorkflowType) => {
    setType(t);
    setTitle(TYPE_TITLES[t]);
    setPayloadText(JSON.stringify(SAMPLE_PAYLOADS[t], null, 2));
  };

  const handleSubmit = async () => {
    setLoading(true);
    setError("");
    try {
      let payload: Record<string, unknown>;
      try {
        payload = JSON.parse(payloadText);
      } catch {
        setError("Invalid JSON payload");
        return;
      }
      await api.createWorkflow({
        type,
        title,
        description: description || `${type} workflow created by ${currentUser}`,
        payload,
        created_by: currentUser,
      });
      onCreated();
      onClose();
    } catch (e: any) {
      setError(e.message || "Failed to create workflow");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={styles.overlay} onClick={onClose}>
      <div style={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div style={styles.header}>
          <span style={{ fontWeight: 700 }}>New Workflow</span>
          <button style={styles.closeBtn} onClick={onClose}>✕</button>
        </div>
        <div style={styles.body}>
          <label style={styles.label}>Workflow Type</label>
          <div style={styles.typeGrid}>
            {TYPES.map((t) => (
              <button
                key={t}
                style={{
                  ...styles.typeBtn,
                  background: type === t ? "#2563eb" : "#f9fafb",
                  color: type === t ? "white" : "#374151",
                  border: `1px solid ${type === t ? "#2563eb" : "#e5e7eb"}`,
                }}
                onClick={() => handleTypeChange(t)}
              >
                {t.replace("_", " ")}
              </button>
            ))}
          </div>

          <label style={styles.label}>Title</label>
          <input
            style={styles.input}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />

          <label style={styles.label}>Description</label>
          <input
            style={styles.input}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Optional description..."
          />

          <label style={styles.label}>Payload (JSON)</label>
          <textarea
            style={styles.textarea}
            value={payloadText}
            onChange={(e) => setPayloadText(e.target.value)}
            rows={10}
            spellCheck={false}
          />

          {error && <div style={styles.error}>{error}</div>}
        </div>
        <div style={styles.footer}>
          <button style={styles.cancelBtn} onClick={onClose}>Cancel</button>
          <button
            style={{ ...styles.submitBtn, opacity: loading ? 0.7 : 1 }}
            onClick={handleSubmit}
            disabled={loading}
          >
            {loading ? "Creating..." : "Create Workflow"}
          </button>
        </div>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  overlay: {
    position: "fixed",
    inset: 0,
    background: "rgba(0,0,0,0.4)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 1000,
  },
  modal: {
    background: "white",
    borderRadius: 12,
    width: 560,
    maxWidth: "95vw",
    maxHeight: "90vh",
    display: "flex",
    flexDirection: "column",
    boxShadow: "0 25px 50px rgba(0,0,0,0.25)",
  },
  header: {
    padding: "14px 20px",
    borderBottom: "1px solid #e5e7eb",
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    fontSize: 15,
  },
  closeBtn: {
    background: "none",
    border: "none",
    cursor: "pointer",
    fontSize: 16,
    color: "#6b7280",
  },
  body: {
    padding: "16px 20px",
    overflowY: "auto",
    flex: 1,
  },
  label: {
    display: "block",
    fontSize: 12,
    fontWeight: 700,
    color: "#374151",
    marginBottom: 4,
    marginTop: 10,
  },
  typeGrid: {
    display: "flex",
    flexWrap: "wrap",
    gap: 6,
  },
  typeBtn: {
    padding: "4px 10px",
    borderRadius: 6,
    cursor: "pointer",
    fontSize: 11,
    fontWeight: 600,
    border: "1px solid #e5e7eb",
  },
  input: {
    width: "100%",
    padding: "6px 10px",
    border: "1px solid #d1d5db",
    borderRadius: 6,
    fontSize: 13,
    boxSizing: "border-box",
  },
  textarea: {
    width: "100%",
    padding: "8px 10px",
    border: "1px solid #d1d5db",
    borderRadius: 6,
    fontSize: 11,
    fontFamily: "monospace",
    resize: "vertical",
    boxSizing: "border-box",
  },
  error: {
    marginTop: 8,
    color: "#dc2626",
    fontSize: 12,
    background: "#fef2f2",
    padding: "6px 10px",
    borderRadius: 6,
  },
  footer: {
    padding: "12px 20px",
    borderTop: "1px solid #e5e7eb",
    display: "flex",
    justifyContent: "flex-end",
    gap: 8,
  },
  cancelBtn: {
    padding: "7px 16px",
    borderRadius: 6,
    background: "#f3f4f6",
    border: "1px solid #e5e7eb",
    cursor: "pointer",
    fontSize: 13,
  },
  submitBtn: {
    padding: "7px 16px",
    borderRadius: 6,
    background: "#2563eb",
    color: "white",
    border: "none",
    cursor: "pointer",
    fontSize: 13,
    fontWeight: 700,
  },
};
