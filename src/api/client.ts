import type {
  WorkflowInstance,
  WorkflowListItem,
  AuditEvent,
  ToolDefinition,
  UserRole,
  WorkflowType,
} from "../types/domain";

const BASE = process.env.REACT_APP_API_URL || "http://localhost:8000/api";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API error ${res.status}: ${text}`);
  }
  return res.json();
}

export const api = {
  listWorkflows(params?: {
    state?: string;
    type?: WorkflowType;
    limit?: number;
    offset?: number;
  }): Promise<WorkflowListItem[]> {
    const qs = new URLSearchParams();
    if (params?.state) qs.set("state", params.state);
    if (params?.type) qs.set("type", params.type);
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.offset) qs.set("offset", String(params.offset));
    return request(`/workflows/?${qs}`);
  },

  getWorkflow(id: string): Promise<WorkflowInstance> {
    return request(`/workflows/${id}`);
  },

  createWorkflow(data: {
    type: WorkflowType;
    title: string;
    description: string;
    payload: Record<string, unknown>;
    created_by: string;
  }): Promise<WorkflowInstance> {
    return request("/workflows/", {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  approveWorkflow(
    id: string,
    action: { actor: string; role: UserRole; decision: "approve" | "deny"; comment: string }
  ): Promise<unknown> {
    return request(`/workflows/${id}/approve`, {
      method: "POST",
      body: JSON.stringify(action),
    });
  },

  escalateWorkflow(id: string, actor: string, reason: string): Promise<unknown> {
    return request(`/workflows/${id}/escalate`, {
      method: "POST",
      body: JSON.stringify({ actor, reason }),
    });
  },

  disputeWorkflow(id: string, actor: string, reason: string): Promise<unknown> {
    return request(`/workflows/${id}/dispute`, {
      method: "POST",
      body: JSON.stringify({ actor, reason }),
    });
  },

  getAuditTrail(workflowId?: string, limit?: number): Promise<AuditEvent[]> {
    const qs = new URLSearchParams();
    if (workflowId) qs.set("workflow_id", workflowId);
    if (limit) qs.set("limit", String(limit));
    return request(`/audit/?${qs}`);
  },

  verifyChain(workflowId?: string): Promise<{ valid: boolean; broken_at?: string; events_checked?: number }> {
    const qs = new URLSearchParams();
    if (workflowId) qs.set("workflow_id", workflowId);
    return request(`/audit/verify?${qs}`);
  },

  listTools(): Promise<ToolDefinition[]> {
    return request("/tools/");
  },
};

export function createEventSource(workflowId?: string): EventSource {
  const url = workflowId
    ? `${BASE}/events/stream?workflow_id=${workflowId}`
    : `${BASE}/events/stream`;
  return new EventSource(url);
}
