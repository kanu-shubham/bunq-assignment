// Domain types mirroring backend Pydantic models

export type WorkflowType =
  | "SETTLEMENT"
  | "RECONCILIATION"
  | "DISPUTE"
  | "MARGIN_CALL"
  | "REGULATORY_FILING";

export type WorkflowState =
  | "OPEN"
  | "AWAITING_APPROVAL"
  | "DISPUTED"
  | "ESCALATED"
  | "RESOLVED"
  | "REJECTED";

export type ApprovalStatus = "PENDING" | "APPROVED" | "DENIED" | "EXPIRED";

export type UserRole =
  | "TRADER"
  | "RISK"
  | "COMPLIANCE"
  | "OPERATIONS"
  | "LEGAL"
  | "ADMIN";

export type ToolClass = "read" | "propose" | "commit";

// AG-UI + Finance HITL event types
export type AGUIEventType =
  | "RUN_STARTED"
  | "RUN_FINISHED"
  | "TEXT_MESSAGE_CONTENT"
  | "TOOL_CALL_START"
  | "TOOL_CALL_END"
  | "STATE_SNAPSHOT"
  | "STATE_DELTA"
  | "APPROVAL_REQUEST"
  | "APPROVAL_GRANTED"
  | "APPROVAL_DENIED"
  | "OVERRIDE_INVOKED"
  | "COUNTERPARTY_INPUT_REQUIRED"
  | "REG_FILING_DRAFTED"
  | "EVIDENCE_PINNED"
  | "WORKFLOW_STATE_CHANGED"
  | "AUDIT_EVENT"
  | "CONNECTED";

export interface AGUIEvent {
  event_type: AGUIEventType;
  workflow_id?: string;
  payload: Record<string, unknown>;
  timestamp?: string;
}

export interface ApprovalRecord {
  actor: string;
  role: UserRole;
  decision: "approve" | "deny";
  comment: string;
  timestamp: string;
}

export interface ApprovalGate {
  id: string;
  workflow_id: string;
  required_roles: UserRole[];
  quorum: number;
  deadline?: string;
  approvals: ApprovalRecord[];
  status: ApprovalStatus;
  created_at: string;
}

export interface WorkflowInstance {
  id: string;
  type: WorkflowType;
  state: WorkflowState;
  title: string;
  description: string;
  payload: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  resolved_at?: string;
  created_by: string;
  risk_score: number;
  llm_reasoning: string;
  llm_proposal: Record<string, unknown>;
  approval_gate?: ApprovalGate | null;
}

export interface WorkflowListItem {
  id: string;
  type: WorkflowType;
  state: WorkflowState;
  title: string;
  risk_score: number;
  created_at: string;
  updated_at: string;
  pending_approvals: number;
}

export interface AuditEvent {
  id: string;
  parent_hash: string;
  hash: string;
  event_type: string;
  workflow_id?: string;
  payload: Record<string, unknown>;
  llm_input?: string;
  llm_output?: string;
  actor: string;
  timestamp: string;
}

export interface ToolDefinition {
  name: string;
  description: string;
  tool_class: ToolClass;
  approval_policy?: string;
  allowed_roles: UserRole[];
}
