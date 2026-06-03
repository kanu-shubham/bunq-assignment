# HITL Financial Operations Platform — Platform Standards
## Architecture Review Board Reference Document

**Classification:** Internal Engineering — Staff/VP Level  
**Version:** 1.0.0  
**Date:** 2026-06-03  
**Scope:** JPMorganChase-scale HITL platform (millions of queries/month)

---

## Table of Contents

1. [Standard 1: Event Protocol](#standard-1-event-protocol)
2. [Standard 2: Approval Contract](#standard-2-approval-contract)
3. [Standard 3: Audit Contract](#standard-3-audit-contract)
4. [Standard 4: Tool Classification](#standard-4-tool-classification)
5. [How the Four Standards Compose](#how-the-four-standards-compose)

---

# Standard 1: Event Protocol

## Contract Summary

```
┌─────────────────────────────────────────────────────────────────┐
│                    EVENT PROTOCOL CONTRACT                       │
│                                                                  │
│  Every event MUST carry:                                         │
│    event_id        — globally unique (UUIDv7, time-ordered)      │
│    correlation_id  — root workflow trace ID (never changes)      │
│    causation_id    — direct parent event_id                      │
│    tenant_id       — isolated namespace                          │
│    schema_version  — semver, checked before processing           │
│    timestamp       — RFC 3339 with microsecond precision         │
│    event_type      — string literal from the registry            │
│                                                                  │
│  Transport:                                                      │
│    Server → Client : SSE  (streaming, fan-out)                   │
│    Client → Server : REST POST /events/commands                  │
│    Bi-directional  : WebSocket (only for live trading rooms)     │
│                                                                  │
│  Schema evolution : additive-only within major version           │
│  Breaking changes : new major version, parallel topic            │
└─────────────────────────────────────────────────────────────────┘
```

## 1.1 Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         Event Bus (Kafka)                                │
│                                                                          │
│  topic: fin.events.v1.{tenant_id}                                        │
│  topic: fin.commands.v1.{tenant_id}                                      │
│  topic: fin.audit.v1.{tenant_id}         (append-only, compacted=false)  │
│  topic: fin.dlq.v1.{tenant_id}           (dead letter queue)             │
└──────────┬───────────────────────────────────────┬───────────────────────┘
           │                                       │
           ▼                                       ▼
┌──────────────────────┐               ┌──────────────────────┐
│   Orchestrator       │               │   SSE Gateway        │
│   (Python/FastAPI)   │──publishes──▶ │   (Node/Express)     │
│                      │               │                      │
│  - Consumes commands │               │  - Fan-out to UI     │
│  - Emits events      │               │  - Auth/tenant check │
│  - Manages workflow  │               │  - Heartbeat 15s     │
└──────────────────────┘               └──────────────────────┘
           │                                       │
           ▼                                       ▼
┌──────────────────────┐               ┌──────────────────────┐
│   Agent Runtime      │               │   React Frontend     │
│   (LangGraph/custom) │               │   (AG-UI SDK)        │
└──────────────────────┘               └──────────────────────┘
```

## 1.2 Base Envelope Schema

### TypeScript

```typescript
import { z } from "zod";

// UUIDv7 string — time-ordered, globally unique
type UUIDv7 = string;
type ISO8601Micro = string; // "2026-06-03T14:22:01.123456Z"
type SemVer = string;       // "1.0.0"
type TenantId = string;     // "jpm-equities-desk-nyc"

// ─── Actor ────────────────────────────────────────────────────────────────────
export interface HumanActor {
  kind: "human";
  user_id: string;
  display_name: string;
  roles: string[];         // e.g. ["trader", "approver-l1"]
  desk_id: string;
}

export interface SystemActor {
  kind: "system";
  service_id: string;      // e.g. "orchestrator-v2"
  version: string;
}

export interface AgentActor {
  kind: "agent";
  agent_id: string;
  model: string;           // e.g. "gpt-4o-2024-11-20"
  run_id: string;
}

export type Actor = HumanActor | SystemActor | AgentActor;

// ─── Base Envelope ────────────────────────────────────────────────────────────
export interface BaseEnvelope {
  event_id: UUIDv7;
  correlation_id: UUIDv7;   // Root of the causal tree; never changes
  causation_id: UUIDv7;     // Direct parent event_id
  tenant_id: TenantId;
  timestamp: ISO8601Micro;
  schema_version: SemVer;
  workflow_id: UUIDv7;
  actor: Actor;
  // event_type is added by each concrete event interface
}
```

### Python (Pydantic)

```python
from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
import hashlib


class HumanActor(BaseModel):
    kind: Literal["human"] = "human"
    user_id: str
    display_name: str
    roles: list[str]
    desk_id: str


class SystemActor(BaseModel):
    kind: Literal["system"] = "system"
    service_id: str
    version: str


class AgentActor(BaseModel):
    kind: Literal["agent"] = "agent"
    agent_id: str
    model: str
    run_id: str


Actor = Annotated[
    Union[HumanActor, SystemActor, AgentActor],
    Field(discriminator="kind"),
]


class BaseEnvelope(BaseModel):
    event_id: UUID
    correlation_id: UUID
    causation_id: UUID
    tenant_id: str
    timestamp: datetime
    schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    workflow_id: UUID
    actor: Actor

    @field_validator("schema_version")
    @classmethod
    def validate_semver(cls, v: str) -> str:
        parts = v.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            raise ValueError(f"Invalid semver: {v}")
        return v
```

## 1.3 Standard AG-UI Events

### TypeScript Discriminated Union

```typescript
// ─── RUN_STARTED ──────────────────────────────────────────────────────────────
export interface RunStartedPayload {
  workflow_type: string;        // e.g. "settlement_approval"
  input_summary: string;
  config: Record<string, unknown>;
}
export interface RunStartedEvent extends BaseEnvelope {
  event_type: "RUN_STARTED";
  payload: RunStartedPayload;
}

// ─── RUN_FINISHED ─────────────────────────────────────────────────────────────
export type RunOutcome = "completed" | "failed" | "cancelled" | "timed_out";
export interface RunFinishedPayload {
  outcome: RunOutcome;
  output_summary: string;
  error?: { code: string; message: string; retryable: boolean };
  duration_ms: number;
}
export interface RunFinishedEvent extends BaseEnvelope {
  event_type: "RUN_FINISHED";
  payload: RunFinishedPayload;
}

// ─── TEXT_MESSAGE_CONTENT ─────────────────────────────────────────────────────
export interface TextMessageContentPayload {
  message_id: UUIDv7;
  role: "assistant" | "system" | "tool";
  content: string;
  is_partial: boolean;    // true during streaming chunks
  sequence_number: number;
}
export interface TextMessageContentEvent extends BaseEnvelope {
  event_type: "TEXT_MESSAGE_CONTENT";
  payload: TextMessageContentPayload;
}

// ─── TOOL_CALL_START ──────────────────────────────────────────────────────────
export interface ToolCallStartPayload {
  tool_call_id: UUIDv7;
  tool_name: string;
  tool_class: "read" | "propose" | "commit";
  arguments: Record<string, unknown>;
  estimated_duration_ms?: number;
}
export interface ToolCallStartEvent extends BaseEnvelope {
  event_type: "TOOL_CALL_START";
  payload: ToolCallStartPayload;
}

// ─── TOOL_CALL_END ────────────────────────────────────────────────────────────
export type ToolCallStatus = "success" | "error" | "cancelled" | "pending_approval";
export interface ToolCallEndPayload {
  tool_call_id: UUIDv7;
  tool_name: string;
  status: ToolCallStatus;
  result?: unknown;
  error?: { code: string; message: string };
  duration_ms: number;
}
export interface ToolCallEndEvent extends BaseEnvelope {
  event_type: "TOOL_CALL_END";
  payload: ToolCallEndPayload;
}

// ─── STATE_SNAPSHOT ───────────────────────────────────────────────────────────
export interface StateSnapshotPayload {
  state: Record<string, unknown>;
  checkpoint_id: UUIDv7;
}
export interface StateSnapshotEvent extends BaseEnvelope {
  event_type: "STATE_SNAPSHOT";
  payload: StateSnapshotPayload;
}

// ─── STATE_DELTA ──────────────────────────────────────────────────────────────
// JSON Patch (RFC 6902)
export interface JsonPatchOp {
  op: "add" | "remove" | "replace" | "move" | "copy" | "test";
  path: string;
  value?: unknown;
  from?: string;
}
export interface StateDeltaPayload {
  base_checkpoint_id: UUIDv7;
  patch: JsonPatchOp[];
}
export interface StateDeltaEvent extends BaseEnvelope {
  event_type: "STATE_DELTA";
  payload: StateDeltaPayload;
}

// ─── INTERRUPT ────────────────────────────────────────────────────────────────
export type InterruptReason =
  | "approval_required"
  | "clarification_required"
  | "sanctions_check"
  | "manual_override"
  | "sla_approaching";

export interface InterruptPayload {
  interrupt_id: UUIDv7;
  reason: InterruptReason;
  blocking: boolean;        // false = advisory; true = workflow paused
  prompt?: string;          // human-readable ask
  resume_schema?: Record<string, unknown>; // JSON Schema for expected resume payload
  deadline: ISO8601Micro;
}
export interface InterruptEvent extends BaseEnvelope {
  event_type: "INTERRUPT";
  payload: InterruptPayload;
}

// ─── RESUME ───────────────────────────────────────────────────────────────────
export interface ResumePayload {
  interrupt_id: UUIDv7;
  resolution: "proceed" | "abort" | "modify";
  data?: Record<string, unknown>;
}
export interface ResumeEvent extends BaseEnvelope {
  event_type: "RESUME";
  payload: ResumePayload;
}
```

## 1.4 Finance-Specific HITL Extensions

```typescript
// ─── APPROVAL_REQUEST ─────────────────────────────────────────────────────────
export interface ApprovalRequestPayload {
  gate_id: UUIDv7;
  subject: string;              // human-readable summary
  amount?: { value: number; currency: string };
  required_roles: string[];
  quorum: number;               // N of M
  total_approvers: number;      // M
  deadline: ISO8601Micro;
  policy_ref: string;           // e.g. "pol:settlement:above-1m-usd"
  evidence_ids: UUIDv7[];       // pinned evidence references
  risk_score?: number;          // 0.0 – 1.0
}
export interface ApprovalRequestEvent extends BaseEnvelope {
  event_type: "APPROVAL_REQUEST";
  payload: ApprovalRequestPayload;
}

// ─── APPROVAL_GRANTED ─────────────────────────────────────────────────────────
export interface ApprovalGrantedPayload {
  gate_id: UUIDv7;
  approver_user_id: string;
  approver_roles: string[];
  comment?: string;
  vote_number: number;    // 1st, 2nd, ... of quorum
  quorum_reached: boolean;
}
export interface ApprovalGrantedEvent extends BaseEnvelope {
  event_type: "APPROVAL_GRANTED";
  payload: ApprovalGrantedPayload;
}

// ─── APPROVAL_DENIED ──────────────────────────────────────────────────────────
export interface ApprovalDeniedPayload {
  gate_id: UUIDv7;
  denier_user_id: string;
  denier_roles: string[];
  reason: string;
  final: boolean;   // if any single denial is final, whole gate fails
}
export interface ApprovalDeniedEvent extends BaseEnvelope {
  event_type: "APPROVAL_DENIED";
  payload: ApprovalDeniedPayload;
}

// ─── OVERRIDE_INVOKED ─────────────────────────────────────────────────────────
export interface OverrideInvokedPayload {
  gate_id: UUIDv7;
  override_by: string;            // user_id
  override_role: string;          // must be "break-glass-officer"
  justification: string;          // mandatory free text, min 50 chars
  incident_ticket: string;        // JIRA / ServiceNow reference
  post_review_deadline: ISO8601Micro;
}
export interface OverrideInvokedEvent extends BaseEnvelope {
  event_type: "OVERRIDE_INVOKED";
  payload: OverrideInvokedPayload;
}

// ─── COUNTERPARTY_INPUT_REQUIRED ──────────────────────────────────────────────
export interface CounterpartyInputRequiredPayload {
  counterparty_id: string;
  channel: "swift_mt" | "email_verified" | "api_webhook" | "portal";
  request_type: string;
  reference_data: Record<string, unknown>;
  deadline: ISO8601Micro;
  fallback_action: "escalate" | "abort" | "use_default";
}
export interface CounterpartyInputRequiredEvent extends BaseEnvelope {
  event_type: "COUNTERPARTY_INPUT_REQUIRED";
  payload: CounterpartyInputRequiredPayload;
}

// ─── REG_FILING_DRAFTED ───────────────────────────────────────────────────────
export interface RegFilingDraftedPayload {
  filing_id: UUIDv7;
  regulation: string;             // e.g. "EMIR", "CFTC_Part43", "MiFID2"
  form_type: string;
  jurisdiction: string;
  draft_content_ref: string;      // S3/GCS URI — never inline
  review_required_by: ISO8601Micro;
  estimated_submission: ISO8601Micro;
}
export interface RegFilingDraftedEvent extends BaseEnvelope {
  event_type: "REG_FILING_DRAFTED";
  payload: RegFilingDraftedPayload;
}

// ─── EVIDENCE_PINNED ──────────────────────────────────────────────────────────
export interface EvidencePinnedPayload {
  evidence_id: UUIDv7;
  kind: "document" | "screenshot" | "api_response" | "calculation" | "market_data_snapshot";
  content_hash: string;           // SHA-256 of the artifact
  storage_uri: string;            // immutable URI
  description: string;
  related_tool_call_id?: UUIDv7;
}
export interface EvidencePinnedEvent extends BaseEnvelope {
  event_type: "EVIDENCE_PINNED";
  payload: EvidencePinnedPayload;
}

// ─── WORKFLOW_STATE_CHANGED ───────────────────────────────────────────────────
export type WorkflowState =
  | "initializing" | "running" | "awaiting_approval" | "awaiting_counterparty"
  | "awaiting_data" | "executing_commit" | "completed" | "failed"
  | "escalated" | "overridden" | "cancelled";

export interface WorkflowStateChangedPayload {
  from_state: WorkflowState;
  to_state: WorkflowState;
  reason: string;
  blocking_on?: UUIDv7;   // gate_id or interrupt_id causing the block
}
export interface WorkflowStateChangedEvent extends BaseEnvelope {
  event_type: "WORKFLOW_STATE_CHANGED";
  payload: WorkflowStateChangedPayload;
}

// ─── ESCALATION_TRIGGERED ────────────────────────────────────────────────────
export interface EscalationTriggeredPayload {
  escalation_id: UUIDv7;
  from_tier: number;       // 0 = original assignee
  to_tier: number;
  reason: "sla_breach" | "no_quorum" | "override_requested" | "sanctions_hit";
  escalated_to_roles: string[];
  original_deadline: ISO8601Micro;
  new_deadline: ISO8601Micro;
}
export interface EscalationTriggeredEvent extends BaseEnvelope {
  event_type: "ESCALATION_TRIGGERED";
  payload: EscalationTriggeredPayload;
}

// ─── SLA_BREACH_WARNING ───────────────────────────────────────────────────────
export interface SlaBreachWarningPayload {
  gate_id?: UUIDv7;
  interrupt_id?: UUIDv7;
  deadline: ISO8601Micro;
  time_remaining_seconds: number;
  severity: "warning" | "critical";   // warning=30min, critical=5min
}
export interface SlaBreachWarningEvent extends BaseEnvelope {
  event_type: "SLA_BREACH_WARNING";
  payload: SlaBreachWarningPayload;
}

// ─── SANCTIONS_HIT ────────────────────────────────────────────────────────────
export interface SanctionsHitPayload {
  scan_id: UUIDv7;
  hit_entity: string;
  list_source: string;      // e.g. "OFAC_SDN", "EU_CONSOLIDATED", "UN_LIST"
  match_score: number;      // 0.0 – 1.0
  match_type: "exact" | "fuzzy" | "alias";
  auto_blocked: boolean;
  compliance_ref: string;
}
export interface SanctionsHitEvent extends BaseEnvelope {
  event_type: "SANCTIONS_HIT";
  payload: SanctionsHitPayload;
}

// ─── RULES_VIOLATION ──────────────────────────────────────────────────────────
export interface RulesViolationPayload {
  violation_id: UUIDv7;
  rule_id: string;
  rule_set: string;         // e.g. "risk_limits_v3", "basel3_rwa"
  severity: "warning" | "error" | "fatal";
  description: string;
  auto_blocked: boolean;
  remediation_hint?: string;
}
export interface RulesViolationEvent extends BaseEnvelope {
  event_type: "RULES_VIOLATION";
  payload: RulesViolationPayload;
}

// ─── Master Discriminated Union ───────────────────────────────────────────────
export type FinanceEvent =
  | RunStartedEvent
  | RunFinishedEvent
  | TextMessageContentEvent
  | ToolCallStartEvent
  | ToolCallEndEvent
  | StateSnapshotEvent
  | StateDeltaEvent
  | InterruptEvent
  | ResumeEvent
  | ApprovalRequestEvent
  | ApprovalGrantedEvent
  | ApprovalDeniedEvent
  | OverrideInvokedEvent
  | CounterpartyInputRequiredEvent
  | RegFilingDraftedEvent
  | EvidencePinnedEvent
  | WorkflowStateChangedEvent
  | EscalationTriggeredEvent
  | SlaBreachWarningEvent
  | SanctionsHitEvent
  | RulesViolationEvent;

// Exhaustive type guard helper
export function assertNever(x: never): never {
  throw new Error(`Unhandled event type: ${(x as BaseEnvelope).event_type}`);
}

export function handleFinanceEvent(event: FinanceEvent): void {
  switch (event.event_type) {
    case "RUN_STARTED": break;
    case "RUN_FINISHED": break;
    case "TEXT_MESSAGE_CONTENT": break;
    case "TOOL_CALL_START": break;
    case "TOOL_CALL_END": break;
    case "STATE_SNAPSHOT": break;
    case "STATE_DELTA": break;
    case "INTERRUPT": break;
    case "RESUME": break;
    case "APPROVAL_REQUEST": break;
    case "APPROVAL_GRANTED": break;
    case "APPROVAL_DENIED": break;
    case "OVERRIDE_INVOKED": break;
    case "COUNTERPARTY_INPUT_REQUIRED": break;
    case "REG_FILING_DRAFTED": break;
    case "EVIDENCE_PINNED": break;
    case "WORKFLOW_STATE_CHANGED": break;
    case "ESCALATION_TRIGGERED": break;
    case "SLA_BREACH_WARNING": break;
    case "SANCTIONS_HIT": break;
    case "RULES_VIOLATION": break;
    default: assertNever(event);
  }
}
```

## 1.5 Python Pydantic Models (Finance Extensions)

```python
from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, Field


class ApprovalRequestPayload(BaseModel):
    gate_id: UUID
    subject: str
    amount: Optional[dict[str, Any]] = None
    required_roles: list[str]
    quorum: int = Field(ge=1)
    total_approvers: int = Field(ge=1)
    deadline: datetime
    policy_ref: str
    evidence_ids: list[UUID] = Field(default_factory=list)
    risk_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class ApprovalRequestEvent(BaseEnvelope):
    event_type: Literal["APPROVAL_REQUEST"] = "APPROVAL_REQUEST"
    payload: ApprovalRequestPayload


class ApprovalGrantedPayload(BaseModel):
    gate_id: UUID
    approver_user_id: str
    approver_roles: list[str]
    comment: Optional[str] = None
    vote_number: int = Field(ge=1)
    quorum_reached: bool


class ApprovalGrantedEvent(BaseEnvelope):
    event_type: Literal["APPROVAL_GRANTED"] = "APPROVAL_GRANTED"
    payload: ApprovalGrantedPayload


class SanctionsHitPayload(BaseModel):
    scan_id: UUID
    hit_entity: str
    list_source: str
    match_score: float = Field(ge=0.0, le=1.0)
    match_type: Literal["exact", "fuzzy", "alias"]
    auto_blocked: bool
    compliance_ref: str


class SanctionsHitEvent(BaseEnvelope):
    event_type: Literal["SANCTIONS_HIT"] = "SANCTIONS_HIT"
    payload: SanctionsHitPayload


class RulesViolationPayload(BaseModel):
    violation_id: UUID
    rule_id: str
    rule_set: str
    severity: Literal["warning", "error", "fatal"]
    description: str
    auto_blocked: bool
    remediation_hint: Optional[str] = None


class RulesViolationEvent(BaseEnvelope):
    event_type: Literal["RULES_VIOLATION"] = "RULES_VIOLATION"
    payload: RulesViolationPayload


# Master discriminated union — Pydantic v2 style
FinanceEvent = Annotated[
    Union[
        RunStartedEvent,
        RunFinishedEvent,
        ApprovalRequestEvent,
        ApprovalGrantedEvent,
        SanctionsHitEvent,
        RulesViolationEvent,
        # ... all other event types
    ],
    Field(discriminator="event_type"),
]


def parse_event(raw: dict) -> FinanceEvent:
    """Entry point for all inbound events. Validates schema_version first."""
    from pydantic import TypeAdapter
    ta = TypeAdapter(FinanceEvent)
    return ta.validate_python(raw)
```

## 1.6 Transport Layer

### SSE (Server → Client)

SSE is the default for all server-to-client streaming. Each event is serialized as:

```
id: <event_id>
event: <event_type>
data: <JSON-encoded FinanceEvent>\n\n
```

Key SSE contract rules:
- The SSE gateway MUST set `Last-Event-ID` header support so clients reconnect without data loss.
- Heartbeat `comment` lines (`: ping`) every 15 seconds prevent proxy timeouts.
- Each SSE connection is scoped to `(tenant_id, workflow_id)` — enforced via JWT claim check at connection open.
- Max event payload: 256 KB. Larger artifacts use `content_ref` (storage URI) instead of inline data.
- Back-pressure: the gateway applies a 10 000-event in-memory buffer per connection; if exceeded, the connection is closed and the client must re-subscribe.

### REST Commands (Client → Server)

```
POST /api/v1/events/commands
Authorization: Bearer <jwt>
Content-Type: application/json

{
  "command_type": "SUBMIT_APPROVAL_VOTE",
  "workflow_id": "...",
  "payload": { ... }
}
```

Commands return HTTP 202 Accepted with a `command_id`. The corresponding state change arrives via SSE. Commands are idempotent — clients MUST include a `idempotency_key` header.

### WebSocket — When to Use

WebSocket is ONLY justified when:
1. Sub-100ms round-trip latency is contractual (live trading rooms, real-time risk dashboards).
2. Client needs to send a high volume of small updates server-side (e.g., partial form data saves).

For standard HITL workflows, SSE + REST is mandatory. WebSocket introduces stateful connection management complexity that has caused multiple production incidents.

## 1.7 Correlation / Causation Chain

Every event carries three IDs that together allow reconstruction of any causal tree:

```
correlation_id  — The root event_id of the originating RUN_STARTED event.
                  Every event in a workflow shares the same correlation_id.
                  Never changes. Use this to pull all events for a workflow.

causation_id    — The event_id of the direct parent that caused this event.
                  Forms a tree (not a chain) — multiple events can share a causation_id.

event_id        — This event's own unique ID. Used as causation_id by children.
```

Trace reconstruction for a 12-hop chain:

```
SELECT * FROM audit_events
WHERE correlation_id = $1
ORDER BY timestamp ASC;

-- Then reconstruct the DAG:
-- node.causation_id → node.event_id edges
```

ASCII diagram of a causal tree:

```
RUN_STARTED (correlation=A, causation=A, id=E1)
  └─ TOOL_CALL_START (correlation=A, causation=E1, id=E2)
       └─ SANCTIONS_HIT (correlation=A, causation=E2, id=E3)
            └─ INTERRUPT (correlation=A, causation=E3, id=E4)
                 ├─ SLA_BREACH_WARNING (correlation=A, causation=E4, id=E5)
                 └─ APPROVAL_REQUEST (correlation=A, causation=E4, id=E6)
                      ├─ APPROVAL_GRANTED (correlation=A, causation=E6, id=E7)
                      ├─ APPROVAL_GRANTED (correlation=A, causation=E6, id=E8)
                      └─ ESCALATION_TRIGGERED (correlation=A, causation=E6, id=E9)
                           └─ APPROVAL_GRANTED (correlation=A, causation=E9, id=E10)
                                └─ RESUME (correlation=A, causation=E10, id=E11)
                                     └─ TOOL_CALL_END (correlation=A, causation=E11, id=E12)
                                          └─ RUN_FINISHED (correlation=A, causation=E12, id=E13)
```

## 1.8 Schema Versioning Strategy

### Versioning Rules

| Change Type | Version Bump | Backward Compatible? | Strategy |
|---|---|---|---|
| Add optional field | PATCH | Yes | Add with default/null |
| Add required field | MINOR | No for old clients | Gate behind feature flag, run both schemas in parallel for 30 days |
| Remove field | MAJOR | No | New major topic, deprecation notice, 90-day sunset |
| Rename field | MAJOR | No | New major topic; bridge adapter in gateway |
| Change field type | MAJOR | No | New major topic |

### Topic Versioning

Kafka topics use major version in the name: `fin.events.v1.{tenant}`, `fin.events.v2.{tenant}`. During migration, a bridge consumer reads v1 and publishes transformed events to v2. Once all consumers are confirmed migrated (90-day window), v1 is sunset.

### Client-Side Handling

```typescript
function processRawEvent(raw: unknown): FinanceEvent | null {
  const envelope = raw as { schema_version?: string };
  const [major] = (envelope.schema_version ?? "0.0.0").split(".").map(Number);
  if (major > SUPPORTED_MAJOR_VERSION) {
    console.warn("Received event from future schema version — dropping");
    return null;
  }
  // Parse with Zod or class-validator
  return parseFinanceEvent(raw);
}
```

## 1.9 Anti-Patterns

### Anti-Pattern 1: Free-Form JSON Payloads

**What it looks like:**
```typescript
// WRONG
interface BadEvent {
  type: string;
  data: Record<string, any>;  // <── free-form, no schema
}
```
**Why it's fatal:** You cannot write an exhaustive switch, cannot generate API docs, cannot validate at the boundary, and cannot enforce PII scrubbing rules selectively. At scale, teams publish subtly incompatible payloads and the audit chain breaks.

### Anti-Pattern 2: Per-Team Event Schemas

Each team defining their own event shapes creates an undocumented private protocol. Regulators examining audit logs cannot reconstruct a cross-team workflow. Enforce the central registry — any new event_type requires an architecture review.

### Anti-Pattern 3: Embedding Secrets in Event Payloads

Events flow through Kafka, SSE gateways, audit stores, and potentially third-party SIEM tools. Passwords, tokens, and PII belong in referenced storage (vault URI, S3 URI), never inline.

### Anti-Pattern 4: Using Timestamps as Event IDs

Wall-clock timestamps have millisecond resolution and are not unique under concurrent writes. Use UUIDv7 which encodes a monotonic timestamp in the high bits for time-ordering with guaranteed uniqueness.

### Anti-Pattern 5: Skipping causation_id

Many teams only set correlation_id. Without causation_id you cannot reconstruct the causal tree — you only know that events belong to the same workflow, not why each event was emitted. This makes post-incident root cause analysis impossible.

---

# Standard 2: Approval Contract

## Contract Summary

```
┌─────────────────────────────────────────────────────────────────┐
│                   APPROVAL CONTRACT SUMMARY                      │
│                                                                  │
│  An approval gate is a durable, policy-driven checkpoint.        │
│  It CANNOT be bypassed by code — only by break-glass with        │
│  mandatory post-incident review.                                 │
│                                                                  │
│  Five Enforcement Rules (all must pass):                         │
│    1. Role gate        — voter must hold a required_role         │
│    2. Anti-self        — originator cannot approve own request   │
│    3. Dedup           — one vote per user per gate               │
│    4. N-of-M quorum   — exactly N approvals needed from M pool   │
│    5. Instant denial  — any denial immediately fails the gate    │
│                                                                  │
│  Timeout cascade:                                                │
│    T+0    Gate opens, SLA timer starts                           │
│    T+30m  SLA_BREACH_WARNING (severity=warning)                  │
│    T+55m  SLA_BREACH_WARNING (severity=critical)                 │
│    T+60m  Escalate to tier-1 (desk head)                         │
│    T+90m  Escalate to tier-2 (risk officer)                      │
│    T+120m Auto-deny + RULES_VIOLATION(fatal)                     │
└─────────────────────────────────────────────────────────────────┘
```

## 2.1 Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       Approval Service (standalone)                      │
│                                                                          │
│  ┌──────────────┐   ┌──────────────────┐   ┌────────────────────────┐   │
│  │ Gate Manager │   │  Policy Registry  │   │   Escalation Engine    │   │
│  │              │──▶│  (data-driven)    │   │   (Temporal workflow)  │   │
│  │ - open_gate  │   │                  │   │                        │   │
│  │ - cast_vote  │   │  pol:settle:>1m  │   │  - SLA timers          │   │
│  │ - get_status │   │  pol:trade:>10m  │   │  - tier escalation     │   │
│  │ - override   │   │  pol:fx:any      │   │  - auto-deny           │   │
│  └──────┬───────┘   └──────────────────┘   └────────────────────────┘   │
│         │                                                                │
│  ┌──────▼───────────────────────────────────────────────────────────┐   │
│  │                    Gate Store (PostgreSQL)                        │   │
│  │                                                                   │   │
│  │  approval_gates       approval_votes       escalation_log         │   │
│  │  (one row per gate)   (one row per vote)   (one row per tier)     │   │
│  └───────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

## 2.2 Gate Schema

### TypeScript

```typescript
export type GateStatus =
  | "open"
  | "approved"
  | "denied"
  | "timed_out"
  | "overridden"
  | "escalated";

export interface ApprovalVote {
  vote_id: UUIDv7;
  gate_id: UUIDv7;
  voter_user_id: string;
  voter_roles: string[];
  decision: "approve" | "deny";
  comment?: string;
  voted_at: ISO8601Micro;
  ip_address: string;       // for audit
}

export interface EscalationStep {
  tier: number;
  roles: string[];
  deadline: ISO8601Micro;
  notified_at?: ISO8601Micro;
  resolved_by?: string;
}

export interface ApprovalGate {
  gate_id: UUIDv7;
  workflow_id: UUIDv7;
  tenant_id: TenantId;
  required_roles: string[];   // ANY of these roles may vote
  quorum: number;             // N — number of approvals needed
  total_approvers: number;    // M — size of eligible pool
  deadline: ISO8601Micro;
  policy_ref: string;
  approvals: ApprovalVote[];
  denials: ApprovalVote[];
  status: GateStatus;
  escalation_path: EscalationStep[];
  opened_at: ISO8601Micro;
  closed_at?: ISO8601Micro;
  originator_user_id: string; // cannot vote on own gate
  override?: OverrideRecord;
  receipt?: ApprovalReceipt;
}

export interface OverrideRecord {
  override_id: UUIDv7;
  override_by: string;
  override_role: string;
  justification: string;
  incident_ticket: string;
  overridden_at: ISO8601Micro;
  post_review_deadline: ISO8601Micro;
  post_review_completed: boolean;
}

export interface ApprovalReceipt {
  receipt_id: UUIDv7;
  gate_id: UUIDv7;
  workflow_id: UUIDv7;
  outcome: "approved" | "denied" | "overridden" | "timed_out";
  quorum_achieved: number;
  quorum_required: number;
  approvals: ApprovalVote[];
  denials: ApprovalVote[];
  override?: OverrideRecord;
  policy_ref: string;
  opened_at: ISO8601Micro;
  closed_at: ISO8601Micro;
  duration_seconds: number;
  receipt_hash: string;    // SHA-256 of canonical JSON of receipt (without this field)
}
```

### Python (Pydantic)

```python
from __future__ import annotations
from datetime import datetime
from typing import Literal, Optional
from uuid import UUID
import hashlib, json

from pydantic import BaseModel, Field, model_validator


class ApprovalVote(BaseModel):
    vote_id: UUID
    gate_id: UUID
    voter_user_id: str
    voter_roles: list[str]
    decision: Literal["approve", "deny"]
    comment: Optional[str] = None
    voted_at: datetime
    ip_address: str


class EscalationStep(BaseModel):
    tier: int = Field(ge=0)
    roles: list[str]
    deadline: datetime
    notified_at: Optional[datetime] = None
    resolved_by: Optional[str] = None


class OverrideRecord(BaseModel):
    override_id: UUID
    override_by: str
    override_role: str
    justification: str = Field(min_length=50)  # enforced minimum
    incident_ticket: str
    overridden_at: datetime
    post_review_deadline: datetime
    post_review_completed: bool = False


class ApprovalReceipt(BaseModel):
    receipt_id: UUID
    gate_id: UUID
    workflow_id: UUID
    outcome: Literal["approved", "denied", "overridden", "timed_out"]
    quorum_achieved: int
    quorum_required: int
    approvals: list[ApprovalVote]
    denials: list[ApprovalVote]
    override: Optional[OverrideRecord] = None
    policy_ref: str
    opened_at: datetime
    closed_at: datetime
    duration_seconds: float
    receipt_hash: str = ""

    def compute_hash(self) -> str:
        """Canonical hash of receipt (excluding receipt_hash field itself)."""
        data = self.model_dump(exclude={"receipt_hash"}, mode="json")
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @model_validator(mode="after")
    def set_hash(self) -> "ApprovalReceipt":
        if not self.receipt_hash:
            self.receipt_hash = self.compute_hash()
        return self


class ApprovalGate(BaseModel):
    gate_id: UUID
    workflow_id: UUID
    tenant_id: str
    required_roles: list[str]
    quorum: int = Field(ge=1)
    total_approvers: int = Field(ge=1)
    deadline: datetime
    policy_ref: str
    approvals: list[ApprovalVote] = Field(default_factory=list)
    denials: list[ApprovalVote] = Field(default_factory=list)
    status: Literal["open", "approved", "denied", "timed_out", "overridden", "escalated"]
    escalation_path: list[EscalationStep] = Field(default_factory=list)
    opened_at: datetime
    closed_at: Optional[datetime] = None
    originator_user_id: str
    override: Optional[OverrideRecord] = None
    receipt: Optional[ApprovalReceipt] = None

    @model_validator(mode="after")
    def quorum_le_total(self) -> "ApprovalGate":
        if self.quorum > self.total_approvers:
            raise ValueError("quorum cannot exceed total_approvers")
        return self
```

## 2.3 Five Enforcement Rules

```python
from dataclasses import dataclass
from typing import Optional
import uuid
from datetime import datetime, timezone


@dataclass
class VoteResult:
    accepted: bool
    rejection_reason: Optional[str]
    gate_status: str


class ApprovalEngine:
    """
    Enforces all five rules before recording a vote.
    All checks run inside a single database transaction.
    """

    def cast_vote(
        self,
        gate: ApprovalGate,
        voter_user_id: str,
        voter_roles: list[str],
        decision: Literal["approve", "deny"],
        comment: Optional[str],
        ip_address: str,
    ) -> VoteResult:

        # ── Rule 1: Role Gate ─────────────────────────────────────────────────
        eligible = set(voter_roles) & set(gate.required_roles)
        if not eligible:
            return VoteResult(
                accepted=False,
                rejection_reason=(
                    f"Voter roles {voter_roles} do not satisfy "
                    f"required_roles {gate.required_roles}"
                ),
                gate_status=gate.status,
            )

        # ── Rule 2: Anti-Self-Approval ────────────────────────────────────────
        if voter_user_id == gate.originator_user_id:
            return VoteResult(
                accepted=False,
                rejection_reason="Originator cannot approve their own request",
                gate_status=gate.status,
            )

        # ── Rule 3: Duplicate Vote Prevention ─────────────────────────────────
        all_votes = gate.approvals + gate.denials
        if any(v.voter_user_id == voter_user_id for v in all_votes):
            return VoteResult(
                accepted=False,
                rejection_reason=f"User {voter_user_id} has already voted on this gate",
                gate_status=gate.status,
            )

        # Record the vote
        vote = ApprovalVote(
            vote_id=uuid.uuid4(),
            gate_id=gate.gate_id,
            voter_user_id=voter_user_id,
            voter_roles=voter_roles,
            decision=decision,
            comment=comment,
            voted_at=datetime.now(timezone.utc),
            ip_address=ip_address,
        )

        # ── Rule 4: Immediate Denial ──────────────────────────────────────────
        # Any single deny vote immediately closes the gate as denied.
        if decision == "deny":
            gate.denials.append(vote)
            gate.status = "denied"
            gate.closed_at = datetime.now(timezone.utc)
            return VoteResult(accepted=True, rejection_reason=None, gate_status="denied")

        # ── Rule 5: N-of-M Quorum ─────────────────────────────────────────────
        gate.approvals.append(vote)
        if len(gate.approvals) >= gate.quorum:
            gate.status = "approved"
            gate.closed_at = datetime.now(timezone.utc)
            return VoteResult(accepted=True, rejection_reason=None, gate_status="approved")

        return VoteResult(accepted=True, rejection_reason=None, gate_status="open")
```

## 2.4 Quorum Policy Registry

Policies are data-driven rows in a `approval_policies` table — not hardcoded in the engine.

```sql
CREATE TABLE approval_policies (
    policy_ref          TEXT PRIMARY KEY,
    workflow_type       TEXT NOT NULL,
    amount_min          NUMERIC,
    amount_max          NUMERIC,
    currency            TEXT,
    required_roles      TEXT[] NOT NULL,
    quorum              INT NOT NULL CHECK (quorum >= 1),
    total_approvers     INT NOT NULL,
    sla_minutes         INT NOT NULL DEFAULT 60,
    escalation_path     JSONB NOT NULL DEFAULT '[]',
    effective_from      TIMESTAMPTZ NOT NULL,
    effective_to        TIMESTAMPTZ,
    created_by          TEXT NOT NULL,
    approved_by         TEXT NOT NULL,
    version             INT NOT NULL DEFAULT 1
);

-- Example rows
INSERT INTO approval_policies VALUES
  ('pol:settlement:below-1m-usd',   'settlement', 0,       999999.99, 'USD',
   ARRAY['approver-l1'], 1, 2, 60,
   '[{"tier":1,"roles":["desk-head"],"sla_minutes":30}]',
   NOW(), NULL, 'policy-admin', 'compliance-officer', 1),

  ('pol:settlement:1m-to-10m-usd',  'settlement', 1000000, 9999999.99, 'USD',
   ARRAY['approver-l1','approver-l2'], 2, 3, 45,
   '[{"tier":1,"roles":["desk-head"],"sla_minutes":20},
     {"tier":2,"roles":["risk-officer"],"sla_minutes":30}]',
   NOW(), NULL, 'policy-admin', 'compliance-officer', 1),

  ('pol:settlement:above-10m-usd',  'settlement', 10000000, NULL, 'USD',
   ARRAY['approver-l2','approver-l3'], 3, 4, 30,
   '[{"tier":1,"roles":["risk-officer"],"sla_minutes":15},
     {"tier":2,"roles":["cro-delegate"],"sla_minutes":15}]',
   NOW(), NULL, 'policy-admin', 'cro', 1);
```

Policy resolution at runtime:

```python
def resolve_policy(
    workflow_type: str,
    amount: Optional[float],
    currency: Optional[str],
    db,
) -> dict:
    """
    Returns the most specific matching policy row.
    Amount-based policies are sorted most-specific first (narrowest range).
    """
    query = """
        SELECT * FROM approval_policies
        WHERE workflow_type = :workflow_type
          AND (currency IS NULL OR currency = :currency)
          AND (amount_min IS NULL OR :amount >= amount_min)
          AND (amount_max IS NULL OR :amount <= amount_max)
          AND effective_from <= NOW()
          AND (effective_to IS NULL OR effective_to > NOW())
        ORDER BY
          (amount_max - COALESCE(amount_min, 0)) ASC NULLS LAST,
          effective_from DESC
        LIMIT 1
    """
    row = db.execute(query, {
        "workflow_type": workflow_type,
        "currency": currency,
        "amount": amount or 0,
    }).first()
    if not row:
        raise ValueError(f"No approval policy found for {workflow_type} / {amount} {currency}")
    return dict(row)
```

## 2.5 Timeout and Escalation Chain

```python
from temporalio import workflow, activity
from datetime import timedelta


@workflow.defn
class ApprovalGateWorkflow:
    """
    Temporal workflow manages the SLA timer and escalation cascade.
    State lives in Temporal — survives process restarts.
    """

    @workflow.run
    async def run(self, gate_id: str, policy: dict) -> dict:
        sla_minutes = policy["sla_minutes"]
        escalation_path = policy["escalation_path"]

        # Wait for approval or timeout
        approved = await workflow.wait_condition(
            lambda: self._gate_closed,
            timeout=timedelta(minutes=sla_minutes * 0.5),  # 50% warning
        )

        if not self._gate_closed:
            await activity.execute_activity(
                send_sla_warning, gate_id, severity="warning",
                schedule_to_close_timeout=timedelta(seconds=30),
            )

        # Wait remaining time
        approved = await workflow.wait_condition(
            lambda: self._gate_closed,
            timeout=timedelta(minutes=sla_minutes * 0.5 * 0.9),  # 95% warning
        )

        if not self._gate_closed:
            await activity.execute_activity(
                send_sla_warning, gate_id, severity="critical",
                schedule_to_close_timeout=timedelta(seconds=30),
            )

        # Full SLA reached — begin escalation cascade
        if not self._gate_closed:
            for step in escalation_path:
                await activity.execute_activity(
                    escalate_to_tier,
                    gate_id,
                    step["tier"],
                    step["roles"],
                    schedule_to_close_timeout=timedelta(seconds=30),
                )
                approved = await workflow.wait_condition(
                    lambda: self._gate_closed,
                    timeout=timedelta(minutes=step["sla_minutes"]),
                )
                if self._gate_closed:
                    break

        if not self._gate_closed:
            # Final auto-deny
            await activity.execute_activity(
                auto_deny_gate, gate_id, reason="sla_exhausted",
                schedule_to_close_timeout=timedelta(seconds=30),
            )

        return await activity.execute_activity(
            build_receipt, gate_id,
            schedule_to_close_timeout=timedelta(seconds=10),
        )
```

## 2.6 Override / Break-Glass Path

```python
def invoke_override(
    gate: ApprovalGate,
    override_by: str,
    override_role: str,
    justification: str,
    incident_ticket: str,
    db,
    event_bus,
) -> ApprovalReceipt:
    # 1. Verify break-glass role
    if override_role != "break-glass-officer":
        raise PermissionError("Override requires break-glass-officer role")

    # 2. Justification must be substantive (min 50 chars enforced by Pydantic)
    override = OverrideRecord(
        override_id=uuid4(),
        override_by=override_by,
        override_role=override_role,
        justification=justification,   # Pydantic min_length=50
        incident_ticket=incident_ticket,
        overridden_at=utcnow(),
        post_review_deadline=utcnow() + timedelta(hours=24),
        post_review_completed=False,
    )

    # 3. Close the gate as overridden
    gate.status = "overridden"
    gate.closed_at = utcnow()
    gate.override = override

    # 4. Persist atomically (one transaction)
    with db.begin():
        db.update(gate)
        db.insert_audit_event("OVERRIDE_INVOKED", override)

    # 5. Emit OVERRIDE_INVOKED event to all subscribers including compliance
    event_bus.publish(OverrideInvokedEvent(
        gate_id=gate.gate_id,
        override_by=override_by,
        override_role=override_role,
        justification=justification,
        incident_ticket=incident_ticket,
        post_review_deadline=override.post_review_deadline,
    ))

    # 6. Schedule mandatory post-incident review task
    schedule_post_review(gate.gate_id, override.post_review_deadline)

    return build_receipt(gate)
```

## 2.7 REST API Contract

```
# Open a gate
POST /api/v1/approval/gates
Request:
  {
    "workflow_id": "<uuid>",
    "workflow_type": "settlement",
    "amount": { "value": 5000000, "currency": "USD" },
    "subject": "FX settlement JPY→USD 5M — trade ref TRD-2026-00432",
    "evidence_ids": ["<uuid>", "<uuid>"]
  }
Response 201:
  { "gate_id": "<uuid>", "policy_ref": "pol:settlement:1m-to-10m-usd",
    "quorum": 2, "total_approvers": 3, "deadline": "..." }

# Cast a vote
POST /api/v1/approval/gates/{gate_id}/votes
Request:
  { "decision": "approve", "comment": "Verified against Bloomberg ref rate" }
Response 200:
  { "vote_id": "<uuid>", "gate_status": "open", "approvals_so_far": 1,
    "quorum_required": 2 }
  OR gate_status: "approved" when quorum reached

# Get gate status
GET /api/v1/approval/gates/{gate_id}
Response 200: ApprovalGate

# Get receipt (only available when gate is closed)
GET /api/v1/approval/gates/{gate_id}/receipt
Response 200: ApprovalReceipt

# Invoke override
POST /api/v1/approval/gates/{gate_id}/override
Request:
  { "justification": "...", "incident_ticket": "INC-2026-4421" }
Response 200: ApprovalReceipt
```

## 2.8 Sequence Diagram — 2-of-3 Approval with Timeout Escalation

```
Orchestrator     ApprovalService    Temporal(SLA)    ApproverA    ApproverB    DeskHead
     │                  │                │               │            │            │
     │──open_gate()────▶│                │               │            │            │
     │                  │──start_timer()─▶               │            │            │
     │                  │                │               │            │            │
     │◀──gate_id────────│                │               │            │            │
     │                  │                │               │            │            │
     │                  │──notify()──────────────────────▶            │            │
     │                  │──notify()───────────────────────────────────▶            │
     │                  │                │               │            │            │
     │                  │                │  [T+30min no quorum]       │            │
     │                  │◀──SLA_WARNING──│               │            │            │
     │◀──SLA_BREACH_WARNING(warning)─────│               │            │            │
     │                  │                │               │            │            │
     │                  │◀──cast_vote(approve)───────────│            │            │
     │                  │   Rule 1: role ✓               │            │            │
     │                  │   Rule 2: not originator ✓     │            │            │
     │                  │   Rule 3: no dup vote ✓        │            │            │
     │                  │   Rule 5: 1 < quorum(2) → open │            │            │
     │◀──APPROVAL_GRANTED(vote=1, quorum_reached=false)──│            │            │
     │                  │                │               │            │            │
     │                  │                │  [T+55min]    │            │            │
     │                  │◀──SLA_WARNING──│               │            │            │
     │◀──SLA_BREACH_WARNING(critical)────│               │            │            │
     │                  │                │               │            │            │
     │                  │                │  [T+60min: SLA breached]   │            │
     │                  │◀──ESCALATE─────│               │            │            │
     │◀──ESCALATION_TRIGGERED(tier=1)────│               │            │            │
     │                  │──notify()──────────────────────────────────────────────▶│
     │                  │                │               │            │            │
     │                  │◀──cast_vote(approve)────────────────────────────────────│
     │                  │   Rule 5: 2 >= quorum(2) → APPROVED        │            │
     │                  │──cancel_timer()▶               │            │            │
     │◀──APPROVAL_GRANTED(vote=2, quorum_reached=true)               │            │
     │◀──WORKFLOW_STATE_CHANGED(awaiting_approval→executing_commit)              │
     │                  │                │               │            │            │
     │──get_receipt()──▶│                │               │            │            │
     │◀──ApprovalReceipt(outcome=approved, hash=...)─────│            │            │
```

## 2.9 Anti-Patterns

### Anti-Pattern 1: Buried Approval Logic

```python
# WRONG — approval baked into the tool function
async def execute_settlement(amount, approver_id):
    if approver_id and amount < 1_000_000:   # <── ad-hoc, untested, bypassed by flag
        return do_settlement()
```

Every approval decision must go through the Approval Service. The gate must be opened and closed as a discrete event — not an inline conditional.

### Anti-Pattern 2: Flag-Based Approval

```python
# WRONG
payload = { "requires_approval": False, "reason": "low risk" }  # who decided?
```

The approval requirement is determined by the Policy Registry based on workflow_type and amount. It is never a field in the payload that a calling service can set to False.

### Anti-Pattern 3: Single Approver by Default

Defaulting to quorum=1 for all workflows is a compliance violation for material transactions. The Policy Registry must define per-workflow quorum requirements, and the Approval Service must enforce them. "We'll add more approvers later" is not an acceptable state.

### Anti-Pattern 4: Not Recording Denials

Some implementations only record approvals. Denials are equally material for audit — they show the workflow was reviewed, considered, and rejected. Every denial vote must produce an `APPROVAL_DENIED` event and an `ApprovalVote` record.

---

# Standard 3: Audit Contract

## Contract Summary

```
┌─────────────────────────────────────────────────────────────────┐
│                    AUDIT CONTRACT SUMMARY                        │
│                                                                  │
│  The audit ledger is APPEND-ONLY. No UPDATE. No DELETE.         │
│  Every record carries a SHA-256 hash of its content and         │
│  the hash of its parent — forming a tamper-evident chain.       │
│                                                                  │
│  Mandatory fields on every entry:                                │
│    id, parent_hash, hash, event_type, workflow_id,              │
│    actor, timestamp, schema_version, pii_scrubbed               │
│                                                                  │
│  LLM I/O is mandatory for any AI-generated action.             │
│  Large prompts (>64 KB) are stored by reference, not inline.   │
│                                                                  │
│  Retention: 7 years                                              │
│    Hot  (0–90 days)   : PostgreSQL + Redis cache                 │
│    Warm (90d–2 years) : Parquet on S3/GCS                       │
│    Cold (2–7 years)   : WORM object storage (S3 Object Lock)    │
│                                                                  │
│  Regulator exports: JSON-L with chain proof OR PDF              │
└─────────────────────────────────────────────────────────────────┘
```

## 3.1 Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          Audit Service                                        │
│                                                                               │
│  ┌────────────────────┐    ┌─────────────────┐    ┌──────────────────────┐   │
│  │  Write API         │    │  PII Scrubber   │    │  Chain Verifier      │   │
│  │                    │───▶│                 │───▶│                      │   │
│  │  POST /audit/append│    │  Field-level    │    │  GET /audit/verify   │   │
│  │  (append-only)     │    │  redaction      │    │  Recomputes hashes   │   │
│  └────────────────────┘    └─────────────────┘    └──────────────────────┘   │
│            │                                                                  │
│  ┌─────────▼──────────────────────────────────────────────────────────────┐  │
│  │                    Audit Store (PostgreSQL — hot tier)                  │  │
│  │                                                                         │  │
│  │  audit_events: id, parent_hash, hash, event_type, workflow_id,         │  │
│  │                payload, llm_input_ref, llm_output_ref, actor,          │  │
│  │                timestamp, schema_version, pii_scrubbed                  │  │
│  │                                                                         │  │
│  │  CONSTRAINT: no UPDATE trigger, no DELETE privilege on app role         │  │
│  └─────────────────────────────────────────────────────────────────────────┘  │
│            │                                                                  │
│  ┌─────────▼──────────────────────────────────────────────────────────────┐  │
│  │                    Tiering Job (nightly)                                │  │
│  │                                                                         │  │
│  │  Hot → Warm: pg_dump | parquet → S3                                    │  │
│  │  Warm → Cold: S3 lifecycle rule → S3 Object Lock (WORM, 7yr)          │  │
│  └─────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────┘
```

## 3.2 Audit Event Schema

### TypeScript

```typescript
export interface LlmIoRef {
  kind: "reference";
  storage_uri: string;    // immutable S3/GCS URI
  size_bytes: number;
  content_hash: string;   // SHA-256 of the raw content
}

export interface LlmIoInline {
  kind: "inline";
  content: string;
}

export type LlmIo = LlmIoRef | LlmIoInline;

export interface AuditEntry {
  id: UUIDv7;
  parent_id: UUIDv7 | null;       // null for chain root
  parent_hash: string | null;     // SHA-256 of parent entry's canonical form
  hash: string;                   // SHA-256 of this entry's canonical form
  event_type: string;             // from the event registry
  workflow_id: UUIDv7;
  payload: Record<string, unknown>;
  llm_input?: LlmIo;             // present when actor.kind === "agent"
  llm_output?: LlmIo;            // present when actor.kind === "agent"
  actor: Actor;
  timestamp: ISO8601Micro;
  schema_version: SemVer;
  pii_scrubbed: boolean;          // true = payload has been field-redacted
  tenant_id: TenantId;
}
```

### Python (Pydantic)

```python
from __future__ import annotations
import hashlib, json
from datetime import datetime
from typing import Literal, Optional, Union
from uuid import UUID
from pydantic import BaseModel, Field, model_validator


class LlmIoRef(BaseModel):
    kind: Literal["reference"] = "reference"
    storage_uri: str
    size_bytes: int
    content_hash: str       # SHA-256


class LlmIoInline(BaseModel):
    kind: Literal["inline"] = "inline"
    content: str = Field(max_length=65536)  # 64 KB max inline


LlmIo = Union[LlmIoRef, LlmIoInline]


class AuditEntry(BaseModel):
    id: UUID
    parent_id: Optional[UUID] = None
    parent_hash: Optional[str] = None
    hash: str = ""
    event_type: str
    workflow_id: UUID
    payload: dict
    llm_input: Optional[LlmIo] = None
    llm_output: Optional[LlmIo] = None
    actor: Actor
    timestamp: datetime
    schema_version: str
    pii_scrubbed: bool = False
    tenant_id: str

    def canonical_bytes(self) -> bytes:
        """
        Deterministic serialization for hashing.
        Fields: id, parent_hash, event_type, workflow_id, payload,
                actor, timestamp, schema_version, pii_scrubbed, tenant_id
        Hash field itself is excluded.
        llm_input/llm_output included as their content_hash references.
        """
        data = {
            "id": str(self.id),
            "parent_hash": self.parent_hash,
            "event_type": self.event_type,
            "workflow_id": str(self.workflow_id),
            "payload": self.payload,
            "llm_input_hash": (
                self.llm_input.content_hash
                if isinstance(self.llm_input, LlmIoRef)
                else (hashlib.sha256(self.llm_input.content.encode()).hexdigest()
                      if self.llm_input else None)
            ),
            "llm_output_hash": (
                self.llm_output.content_hash
                if isinstance(self.llm_output, LlmIoRef)
                else (hashlib.sha256(self.llm_output.content.encode()).hexdigest()
                      if self.llm_output else None)
            ),
            "actor": self.actor.model_dump(mode="json"),
            "timestamp": self.timestamp.isoformat(),
            "schema_version": self.schema_version,
            "pii_scrubbed": self.pii_scrubbed,
            "tenant_id": self.tenant_id,
        }
        return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()

    def compute_hash(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @model_validator(mode="after")
    def set_hash(self) -> "AuditEntry":
        if not self.hash:
            self.hash = self.compute_hash()
        return self
```

## 3.3 Append-Only Write Contract

```python
from contextlib import contextmanager
import sqlite3  # illustrative — production uses PostgreSQL


class AuditLedger:
    """
    Append-only audit ledger.

    Design decisions:
    - BEGIN IMMEDIATE transaction: acquires a write lock immediately, preventing
      concurrent writers from inserting between our SELECT (last entry) and our
      INSERT (new entry). This ensures the parent_hash chain has no gaps.
    - The application DB role has INSERT + SELECT only. UPDATE and DELETE are
      revoked and enforced at the database level, not just by convention.
    - The chain root (first entry) has parent_id=NULL, parent_hash=NULL.
    """

    def append(self, entry: AuditEntry, db_conn) -> AuditEntry:
        # BEGIN IMMEDIATE — critical: prevents gap between last-entry lookup and insert
        with db_conn.transaction(isolation_level="IMMEDIATE"):

            # Fetch the current chain tip
            last = db_conn.execute(
                "SELECT id, hash FROM audit_events "
                "WHERE tenant_id = %s "
                "ORDER BY timestamp DESC, id DESC LIMIT 1",
                (entry.tenant_id,)
            ).first()

            if last:
                entry.parent_id = last["id"]
                entry.parent_hash = last["hash"]
            else:
                entry.parent_id = None
                entry.parent_hash = None

            # Compute hash now that parent_hash is set
            entry.hash = entry.compute_hash()

            db_conn.execute(
                """
                INSERT INTO audit_events
                  (id, parent_id, parent_hash, hash, event_type, workflow_id,
                   payload, llm_input, llm_output, actor, timestamp,
                   schema_version, pii_scrubbed, tenant_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                entry.model_dump(mode="json"),
            )

        return entry
```

Why `BEGIN IMMEDIATE`:
- A standard `BEGIN` (deferred) transaction in PostgreSQL's default READ COMMITTED isolation would allow another writer to insert between our tip lookup and our insert, creating two entries with the same parent — breaking the hash chain.
- `BEGIN IMMEDIATE` (or `FOR UPDATE` on the tip row) serialises chain writes per tenant.
- At JPMorganChase scale, writes are sharded by tenant_id — each shard has its own chain root, and the lock contention is bounded per shard.

## 3.4 PII Scrubbing Pipeline

```python
import re
from typing import Any
from dataclasses import dataclass, field


@dataclass
class ScrubRule:
    field_path: str          # dot-separated: "payload.counterparty.ssn"
    strategy: str            # "redact" | "hash" | "mask"
    pattern: re.Pattern | None = None   # for string-match redaction


# Registry of PII fields — maintained by the Data Governance team
PII_SCRUB_RULES: list[ScrubRule] = [
    ScrubRule("payload.counterparty.ssn",       "redact"),
    ScrubRule("payload.counterparty.passport",  "redact"),
    ScrubRule("payload.counterparty.dob",       "hash"),
    ScrubRule("payload.actor.ip_address",       "mask",
              re.compile(r"(\d+\.\d+)\.\d+\.\d+")),
    ScrubRule("payload.email",                  "redact"),
    ScrubRule("payload.phone",                  "redact"),
    ScrubRule("llm_input.content",              "scan_and_redact"),
    ScrubRule("llm_output.content",             "scan_and_redact"),
]

# Regex patterns for scanning free-text fields
PII_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN-REDACTED]"),       # US SSN
    (re.compile(r"\b[A-Z]{2}\d{6,9}\b"), "[PASSPORT-REDACTED]"),    # Passport
    (re.compile(r"\b\d{16}\b"), "[CARD-REDACTED]"),                  # Card number
    (re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"),
     "[EMAIL-REDACTED]"),
]


def scrub_entry(entry: AuditEntry) -> AuditEntry:
    """
    Returns a new AuditEntry with PII fields redacted.
    The original (pre-scrub) entry's hash is preserved as parent proof.

    CRITICAL: Scrubbing happens BEFORE the entry is hashed and written to the ledger.
    We do not scrub already-written entries (that would break the chain).
    For real-time LLM output, scrubbing runs in the write path (<5ms target).
    For bulk back-scrubbing of historical records, use a separate reconciliation
    entry that records which fields were scrubbed, without modifying the original.
    """
    import copy
    scrubbed = copy.deepcopy(entry)

    for rule in PII_SCRUB_RULES:
        _apply_rule(scrubbed, rule)

    scrubbed.pii_scrubbed = True
    # Hash is recomputed in model_validator after mutation
    scrubbed.hash = scrubbed.compute_hash()
    return scrubbed


def _apply_rule(entry: AuditEntry, rule: ScrubRule) -> None:
    parts = rule.field_path.split(".")
    obj = entry
    for part in parts[:-1]:
        obj = getattr(obj, part, None) or (obj.get(part) if isinstance(obj, dict) else None)
        if obj is None:
            return

    leaf = parts[-1]
    current_val = obj.get(leaf) if isinstance(obj, dict) else getattr(obj, leaf, None)
    if current_val is None:
        return

    if rule.strategy == "redact":
        new_val = "[REDACTED]"
    elif rule.strategy == "hash":
        new_val = "[HASHED:" + hashlib.sha256(str(current_val).encode()).hexdigest()[:12] + "]"
    elif rule.strategy == "mask" and rule.pattern:
        new_val = rule.pattern.sub(r"\1.x.x", str(current_val))
    elif rule.strategy == "scan_and_redact":
        new_val = current_val
        for pattern, replacement in PII_PATTERNS:
            new_val = pattern.sub(replacement, str(new_val))
    else:
        new_val = "[REDACTED]"

    if isinstance(obj, dict):
        obj[leaf] = new_val
    else:
        object.__setattr__(obj, leaf, new_val)
```

## 3.5 Chain Verification Endpoint

```python
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class ChainProof(BaseModel):
    tenant_id: str
    chain_length: int
    root_id: str
    root_hash: str
    tip_id: str
    tip_hash: str
    broken_at_id: str | None         # None = chain intact
    broken_at_position: int | None
    verified_at: str
    integrity: bool


@router.get("/api/v1/audit/verify")
async def verify_chain(
    tenant_id: str,
    from_id: str | None = None,
    to_id: str | None = None,
    db = Depends(get_db),
) -> ChainProof:
    """
    Recomputes every hash in the chain and checks parent linkage.
    For full-chain verification at scale, this runs as a background job
    rather than synchronously — the endpoint returns a job_id and the
    result is available at /api/v1/audit/verify/{job_id}.
    """
    entries = db.execute(
        "SELECT * FROM audit_events WHERE tenant_id = %s ORDER BY timestamp ASC",
        (tenant_id,)
    ).all()

    broken_at = None
    prev_hash = None

    for i, row in enumerate(entries):
        entry = AuditEntry(**row)
        recomputed = entry.compute_hash()

        if recomputed != entry.hash:
            broken_at = (entry.id, i)
            break

        if prev_hash is not None and entry.parent_hash != prev_hash:
            broken_at = (entry.id, i)
            break

        prev_hash = entry.hash

    return ChainProof(
        tenant_id=tenant_id,
        chain_length=len(entries),
        root_id=str(entries[0].id) if entries else "",
        root_hash=entries[0].hash if entries else "",
        tip_id=str(entries[-1].id) if entries else "",
        tip_hash=entries[-1].hash if entries else "",
        broken_at_id=str(broken_at[0]) if broken_at else None,
        broken_at_position=broken_at[1] if broken_at else None,
        verified_at=utcnow().isoformat(),
        integrity=broken_at is None,
    )
```

## 3.6 Regulator Export Format

```python
import json
import gzip
from pathlib import Path


def export_jsonl(
    workflow_id: str,
    tenant_id: str,
    db,
    output_path: Path,
) -> dict:
    """
    Produces a JSON-Lines file where each line is an AuditEntry.
    The last line is the ChainProof — allowing a regulator to verify
    integrity without a live database connection.

    File format:
      Line 1..N : AuditEntry JSON
      Line N+1  : ChainProof JSON (sentinel: "record_type": "CHAIN_PROOF")
    """
    entries = db.execute(
        "SELECT * FROM audit_events "
        "WHERE workflow_id = %s AND tenant_id = %s ORDER BY timestamp ASC",
        (workflow_id, tenant_id),
    ).all()

    proof = verify_chain_for_entries(entries)

    with gzip.open(output_path, "wt", encoding="utf-8") as f:
        for row in entries:
            entry = AuditEntry(**row)
            f.write(json.dumps(entry.model_dump(mode="json")) + "\n")

        f.write(json.dumps({
            "record_type": "CHAIN_PROOF",
            **proof.model_dump(),
        }) + "\n")

    return {
        "file_path": str(output_path),
        "entry_count": len(entries),
        "chain_integrity": proof.integrity,
        "export_hash": hashlib.sha256(output_path.read_bytes()).hexdigest(),
    }
```

## 3.7 Retention Policy

```
┌─────────────────────────────────────────────────────────────────────┐
│                       7-Year Retention Tiers                         │
│                                                                       │
│  Hot  (days 0–90)                                                     │
│    Storage : PostgreSQL (primary + 2 read replicas)                   │
│    Cache   : Redis (LRU, TTL=1h for hot workflow queries)             │
│    Access  : Direct SQL + REST API                                    │
│    Cost    : High (fast NVMe)                                         │
│                                                                       │
│  Warm  (days 90 – 730)                                                │
│    Storage : Apache Parquet on S3/GCS, partitioned by date+tenant    │
│    Access  : Athena/BigQuery SQL, REST API via query translator       │
│    Cost    : Medium                                                   │
│    SLA     : Query returns in <30 seconds                             │
│                                                                       │
│  Cold  (days 730 – 2555 / 7 years)                                   │
│    Storage : S3 with Object Lock (WORM, Compliance mode, 7yr)        │
│    Format  : Parquet + JSON-L export bundle                           │
│    Access  : Restore request → available within 12 hours             │
│    Cost    : Minimal (Glacier Flexible Retrieval)                     │
│    WORM    : No delete, no overwrite, not even by root/admin          │
└───────────────────────────────────────────────────────────────────────┘
```

S3 Object Lock configuration:

```json
{
  "ObjectLockEnabled": "Enabled",
  "Rule": {
    "DefaultRetention": {
      "Mode": "COMPLIANCE",
      "Years": 7
    }
  }
}
```

## 3.8 Query Patterns

```sql
-- Pattern 1: All events for a workflow (most common — post-trade review)
SELECT * FROM audit_events
WHERE workflow_id = $1 AND tenant_id = $2
ORDER BY timestamp ASC;

-- Pattern 2: All actions by a specific actor (compliance investigation)
SELECT * FROM audit_events
WHERE tenant_id = $1
  AND actor->>'user_id' = $2
  AND timestamp BETWEEN $3 AND $4
ORDER BY timestamp DESC;

-- Pattern 3: All events of a type in a time range (regulatory sweep)
SELECT * FROM audit_events
WHERE tenant_id = $1
  AND event_type = $2
  AND timestamp BETWEEN $3 AND $4
ORDER BY timestamp ASC;

-- Pattern 4: Full-text search on payload (incident investigation)
-- Requires GIN index on payload
SELECT * FROM audit_events
WHERE tenant_id = $1
  AND payload @@ to_tsquery('english', $2)
ORDER BY timestamp DESC
LIMIT 1000;

-- Indexes (critical for performance at millions of rows/month)
CREATE INDEX idx_audit_workflow   ON audit_events (tenant_id, workflow_id, timestamp);
CREATE INDEX idx_audit_actor      ON audit_events (tenant_id, (actor->>'user_id'), timestamp);
CREATE INDEX idx_audit_event_type ON audit_events (tenant_id, event_type, timestamp);
CREATE INDEX idx_audit_payload_ft ON audit_events USING GIN (payload jsonb_path_ops);
```

## 3.9 LLM I/O Logging Contract

```python
class LlmIoLogger:
    """
    Mandatory logging contract for all LLM calls in the platform.

    Mandatory fields:
      - model: exact model identifier (including version/date suffix)
      - prompt_tokens: from the API response
      - completion_tokens: from the API response
      - temperature, top_p: reproducibility parameters
      - tool_calls: structured list of tool calls requested by the model
      - finish_reason: "stop" | "length" | "tool_calls" | "content_filter"

    Size handling:
      - Inline if total serialized size <= 64 KB
      - Reference (S3 URI) if > 64 KB
      - Content is hashed (SHA-256) before storage for integrity verification

    Redaction:
      - PII scrubber runs on llm_input and llm_output before storage
      - Original unredacted content is NEVER written to audit store
      - If redaction fails, the entire entry is rejected (fail-closed)
    """
    INLINE_MAX_BYTES = 65536

    def log_llm_call(
        self,
        workflow_id: str,
        agent_actor: AgentActor,
        prompt_messages: list[dict],
        response: dict,         # raw API response
        tool_calls: list[dict],
        storage_client,
        scrubber: PiiScrubber,
    ) -> tuple[LlmIo, LlmIo]:
        """Returns (llm_input, llm_output) for inclusion in AuditEntry."""

        # Serialize
        input_json = json.dumps(prompt_messages, sort_keys=True).encode()
        output_json = json.dumps({
            "content": response.get("choices", [{}])[0].get("message", {}).get("content"),
            "tool_calls": tool_calls,
            "finish_reason": response.get("choices", [{}])[0].get("finish_reason"),
            "model": response.get("model"),
            "usage": response.get("usage"),
        }, sort_keys=True).encode()

        # Scrub PII (fail-closed)
        input_scrubbed = scrubber.scrub_text(input_json.decode()).encode()
        output_scrubbed = scrubber.scrub_text(output_json.decode()).encode()

        # Decide inline vs reference
        llm_input = self._pack(input_scrubbed, f"llm/input/{workflow_id}", storage_client)
        llm_output = self._pack(output_scrubbed, f"llm/output/{workflow_id}", storage_client)

        return llm_input, llm_output

    def _pack(self, data: bytes, key_prefix: str, storage_client) -> LlmIo:
        content_hash = hashlib.sha256(data).hexdigest()
        if len(data) <= self.INLINE_MAX_BYTES:
            return LlmIoInline(content=data.decode())
        else:
            uri = storage_client.put(
                key=f"{key_prefix}/{content_hash}.json.gz",
                data=gzip.compress(data),
                content_type="application/json",
                immutable=True,
            )
            return LlmIoRef(
                storage_uri=uri,
                size_bytes=len(data),
                content_hash=content_hash,
            )
```

## 3.10 Anti-Patterns

### Anti-Pattern 1: Mutable Audit Tables

```sql
-- WRONG — allows evidence tampering
UPDATE audit_events SET payload = $1 WHERE id = $2;
DELETE FROM audit_events WHERE timestamp < NOW() - INTERVAL '30 days';
```

Revoke UPDATE and DELETE from the application role at the database level. Corrections are made by appending a new `CORRECTION` event that references the original entry's id, never by modifying the original.

### Anti-Pattern 2: Separate Logs Per Team

Each team maintaining a private audit log means there is no single source of truth for a cross-team workflow. Regulators need a unified view. All teams MUST write to the central Audit Service via its REST API — no local databases, no syslog files, no CloudWatch as the primary audit store.

### Anti-Pattern 3: Missing LLM I/O

If an AI agent takes a financial action and the LLM prompt + response are not in the audit log, you cannot explain the decision to a regulator, a court, or your own risk team. The `llm_input` and `llm_output` fields are MANDATORY when `actor.kind === "agent"`.

### Anti-Pattern 4: No Hash Chain

Storing audit records without a hash chain means you cannot prove the records haven't been tampered with after the fact. Any sophisticated attacker (including a disgruntled employee with database access) could modify records. The chain makes tampering detectable.

---

# Standard 4: Tool Classification

## Contract Summary

```
┌─────────────────────────────────────────────────────────────────┐
│                 TOOL CLASSIFICATION CONTRACT                      │
│                                                                   │
│  Three classes — the class determines who can call it:           │
│                                                                   │
│  READ    — Agent may call freely. No side effects.               │
│            Examples: get_position, fetch_market_data,            │
│                      check_credit_limit                           │
│                                                                   │
│  PROPOSE — Agent may call. Produces a draft/plan.                │
│            No real-world mutation. Requires human review.        │
│            Examples: draft_settlement, calculate_margin,         │
│                      generate_reg_filing                          │
│                                                                   │
│  COMMIT  — Orchestrator ONLY. Agent NEVER calls directly.        │
│            Real-world mutation. Approval gate REQUIRED.          │
│            Must declare compensating_tool (saga pattern).        │
│            Examples: execute_settlement, submit_reg_filing,      │
│                      release_collateral                           │
│                                                                   │
│  LLM calling a COMMIT tool directly = immediate P0 incident.    │
└─────────────────────────────────────────────────────────────────┘
```

## 4.1 Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Orchestrator                                    │
│                                                                          │
│  ┌──────────────────┐  ┌─────────────────────────────────────────────┐  │
│  │   Agent Runtime  │  │              Tool Router                    │  │
│  │   (LangGraph)    │  │                                             │  │
│  │                  │  │  tool_call → lookup class in registry       │  │
│  │  Can call:       │  │                                             │  │
│  │    READ tools    │  │  READ:    pass through to tool executor     │  │
│  │    PROPOSE tools │  │  PROPOSE: pass through to tool executor     │  │
│  │                  │  │  COMMIT:  !! BLOCKED — raise PolicyError !! │  │
│  │  Cannot call:    │  └──────────────────┬──────────────────────────┘  │
│  │    COMMIT tools  │                     │ (only orchestrator itself)   │
│  └──────────────────┘  ┌──────────────────▼──────────────────────────┐  │
│                         │           Commit Executor                   │  │
│                         │                                             │  │
│                         │  1. Fetch approval receipt                  │  │
│                         │  2. Verify receipt hash                     │  │
│                         │  3. Check idempotency key                   │  │
│                         │  4. Execute tool                            │  │
│                         │  5. On failure: invoke compensating_tool    │  │
│                         └─────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

## 4.2 Tool Registration Schema

### TypeScript

```typescript
export type ToolClass = "read" | "propose" | "commit";

export interface PolicyRef {
  required_roles: string[];
  quorum: number;
  max_amount?: number;
  currency?: string;
  escalate_above?: number;  // amount threshold for escalation
}

export interface ToolRegistration {
  name: string;                       // globally unique, kebab-case
  class: ToolClass;
  description: string;                // human-readable, shown in UI
  version: string;                    // semver
  owner_team: string;
  allowed_roles: string[];            // roles that may invoke (empty = all)
  approval_policy_ref?: string;       // required for "commit" class
  approval_policy?: PolicyRef;        // inline policy (alternative to ref)
  max_amount?: number;                // for financial commit tools
  currency?: string;
  idempotency_key_pattern?: string;   // e.g. "{trade_id}:{action}:{date}"
  compensating_tool?: string;         // tool name — required for "commit"
  timeout_ms: number;                 // execution timeout
  retry_policy: RetryPolicy;
  tags: string[];
  created_at: ISO8601Micro;
  approved_by: string;                // must be reviewed by architecture team
}

export interface RetryPolicy {
  max_attempts: number;
  backoff: "fixed" | "exponential" | "none";
  backoff_ms: number;
  retryable_errors: string[];   // error codes that trigger retry
}
```

### Python (Pydantic)

```python
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator


class PolicyRef(BaseModel):
    required_roles: list[str]
    quorum: int = Field(ge=1)
    max_amount: Optional[float] = None
    currency: Optional[str] = None
    escalate_above: Optional[float] = None


class RetryPolicy(BaseModel):
    max_attempts: int = Field(ge=1, le=10)
    backoff: Literal["fixed", "exponential", "none"]
    backoff_ms: int = Field(ge=0)
    retryable_errors: list[str] = Field(default_factory=list)


class ToolRegistration(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9\-]+$")
    tool_class: Literal["read", "propose", "commit"] = Field(alias="class")
    description: str = Field(min_length=20)
    version: str
    owner_team: str
    allowed_roles: list[str] = Field(default_factory=list)
    approval_policy_ref: Optional[str] = None
    approval_policy: Optional[PolicyRef] = None
    max_amount: Optional[float] = None
    currency: Optional[str] = None
    idempotency_key_pattern: Optional[str] = None
    compensating_tool: Optional[str] = None
    timeout_ms: int = Field(default=30000, ge=100, le=300000)
    retry_policy: RetryPolicy
    tags: list[str] = Field(default_factory=list)
    created_at: str
    approved_by: str

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def commit_requires_compensating(self) -> "ToolRegistration":
        if self.tool_class == "commit":
            if not self.compensating_tool:
                raise ValueError("commit tools MUST declare compensating_tool")
            if not (self.approval_policy_ref or self.approval_policy):
                raise ValueError("commit tools MUST declare approval_policy_ref or approval_policy")
            if not self.idempotency_key_pattern:
                raise ValueError("commit tools MUST declare idempotency_key_pattern")
        return self
```

## 4.3 Tool Composition Rules

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Composition Matrix                               │
│                                                                          │
│  Caller \ Callee  │  READ   │  PROPOSE  │  COMMIT                       │
│  ─────────────────┼─────────┼───────────┼─────────                      │
│  Agent            │   ✓     │    ✓      │   ✗  (PolicyError)            │
│  PROPOSE tool     │   ✓     │    ✗      │   ✗  (PolicyError)            │
│  READ tool        │   ✓     │    ✗      │   ✗  (PolicyError)            │
│  Orchestrator     │   ✓     │    ✓      │   ✓  (with receipt)           │
│  Commit Executor  │   ✓     │    ✗      │   ✗  (no chaining)            │
└─────────────────────────────────────────────────────────────────────────┘

Rules:
1. PROPOSE tools may call READ tools — they need data to build a proposal.
2. PROPOSE tools may NOT call other PROPOSE tools — no proposal chains.
   (Rationale: a propose-of-propose creates a hidden approval surface.)
3. READ tools may call READ tools — pure data aggregation is fine.
4. No tool may call a COMMIT tool — only the Orchestrator's Commit Executor.
5. COMMIT tools are one-shot: they execute, succeed or fail, and invoke their
   compensating tool on failure. They do not call other tools.
```

## 4.4 Orchestrator Enforcement

```python
from enum import Enum


class PolicyError(Exception):
    pass


class ToolRouter:
    """
    Central enforcement point. All tool_call requests from the agent
    pass through here. Commit tools are blocked at this layer.
    """

    def __init__(self, registry: ToolRegistry, commit_executor: CommitExecutor):
        self.registry = registry
        self.commit_executor = commit_executor

    def route(
        self,
        tool_name: str,
        arguments: dict,
        caller_context: CallerContext,  # includes: caller_kind, workflow_id, receipt
    ) -> dict:
        reg = self.registry.get(tool_name)
        if reg is None:
            raise ValueError(f"Unknown tool: {tool_name}")

        # ── Enforce class-based routing ────────────────────────────────────────
        if reg.tool_class == "commit":
            if caller_context.caller_kind != "orchestrator_commit_executor":
                raise PolicyError(
                    f"Tool '{tool_name}' is class=commit and may only be invoked "
                    f"by the Commit Executor, not by '{caller_context.caller_kind}'. "
                    f"This is a P0 policy violation."
                )
            # Commit path requires a valid approval receipt
            if not caller_context.receipt:
                raise PolicyError(f"Commit tool '{tool_name}' requires an approval receipt")
            receipt = ApprovalReceipt.model_validate(caller_context.receipt)
            if not self._verify_receipt(receipt, tool_name):
                raise PolicyError(f"Receipt verification failed for tool '{tool_name}'")
            return self.commit_executor.execute(reg, arguments, receipt)

        if reg.tool_class == "propose" and caller_context.caller_kind == "propose_tool":
            raise PolicyError(
                f"PROPOSE tool '{tool_name}' may not be called from another propose tool. "
                f"Caller: {caller_context.caller_tool}"
            )

        # ── Role check ────────────────────────────────────────────────────────
        if reg.allowed_roles:
            if not set(caller_context.actor_roles) & set(reg.allowed_roles):
                raise PolicyError(
                    f"Actor roles {caller_context.actor_roles} cannot invoke "
                    f"'{tool_name}' (allowed_roles={reg.allowed_roles})"
                )

        # READ or PROPOSE — execute directly
        return self._execute_tool(reg, arguments, caller_context)

    def _verify_receipt(self, receipt: ApprovalReceipt, tool_name: str) -> bool:
        expected_hash = receipt.compute_hash()
        return receipt.receipt_hash == expected_hash and receipt.outcome == "approved"
```

## 4.5 Idempotency Contract

```python
import hashlib
from datetime import datetime, timezone


class CommitExecutor:
    """
    Guarantees exactly-once execution of commit tools despite
    network retries and workflow replays.
    """

    def execute(
        self,
        reg: ToolRegistration,
        arguments: dict,
        receipt: ApprovalReceipt,
        idempotency_store,  # Redis or DynamoDB
    ) -> dict:
        # 1. Compute idempotency key
        idem_key = self._compute_idempotency_key(reg, arguments)

        # 2. Check for prior execution (network retry / workflow replay)
        prior = idempotency_store.get(idem_key)
        if prior is not None:
            # Return the cached result — do NOT re-execute
            return {**prior, "idempotent_replay": True}

        # 3. Lock the key (atomic set-if-not-exists)
        acquired = idempotency_store.set_nx(
            idem_key,
            value={"status": "in_progress", "started_at": utcnow().isoformat()},
            ttl_seconds=300,   # 5-minute lock window
        )
        if not acquired:
            raise ConflictError(f"Concurrent execution of {reg.name} with key {idem_key}")

        # 4. Execute
        try:
            result = self._call_tool_function(reg, arguments)
            idempotency_store.set(
                idem_key,
                value={"status": "success", "result": result, "completed_at": utcnow().isoformat()},
                ttl_seconds=86400 * 7,  # keep for 7 days
            )
            return result

        except Exception as exc:
            # 5. Compensate on failure (saga pattern)
            idempotency_store.set(
                idem_key,
                value={"status": "failed", "error": str(exc)},
                ttl_seconds=86400,
            )
            if reg.compensating_tool:
                self._invoke_compensation(reg.compensating_tool, arguments, exc)
            raise

    def _compute_idempotency_key(self, reg: ToolRegistration, arguments: dict) -> str:
        """
        Derives idempotency key from the tool's declared pattern.
        Pattern example: "{trade_id}:{action}:{date}"
        """
        pattern = reg.idempotency_key_pattern or "{tool_name}:{arg_hash}"
        if "{arg_hash}" in pattern:
            arg_hash = hashlib.sha256(
                json.dumps(arguments, sort_keys=True).encode()
            ).hexdigest()[:16]
            key = pattern.replace("{arg_hash}", arg_hash)
        else:
            key = pattern.format(**arguments, tool_name=reg.name)
        return f"idem:{reg.name}:{key}"
```

## 4.6 Compensating Transactions (Saga Pattern)

Every COMMIT tool must declare a `compensating_tool` that undoes its effects. This is enforced at registration time (see Pydantic validator above).

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     Settlement Saga Example                              │
│                                                                          │
│  Commit Tool            Compensating Tool                                │
│  ─────────────────────  ────────────────────────────────                 │
│  execute-settlement  →  reverse-settlement                               │
│  submit-reg-filing   →  withdraw-reg-filing                              │
│  release-collateral  →  re-pledge-collateral                             │
│  debit-nostro        →  credit-nostro                                    │
│                                                                          │
│  Saga sequence:                                                          │
│                                                                          │
│  Step 1: execute-settlement     ✓ committed                              │
│  Step 2: debit-nostro           ✓ committed                              │
│  Step 3: submit-reg-filing      ✗ FAILED                                 │
│                                                                          │
│  Compensation (reverse order):                                           │
│  Step 3: withdraw-reg-filing    (noop — step 3 never committed)          │
│  Step 2: credit-nostro          ✓ compensated                            │
│  Step 1: reverse-settlement     ✓ compensated                            │
└─────────────────────────────────────────────────────────────────────────┘
```

## 4.7 Tool Onboarding Checklist

A team must answer all questions before a new tool is accepted into the registry:

```
Tool Onboarding Checklist
=========================
Tool name (kebab-case):  _______________
Proposed class:          [ ] read  [ ] propose  [ ] commit
Owner team:              _______________
Architecture reviewer:   _______________

CLASSIFICATION GATE (answer all):
  □ Does this tool mutate any external system (database, API, ledger)?
    → If YES, it is at minimum PROPOSE (if reversible without ceremony)
      or COMMIT (if requires approval before execution)
  □ Does this tool send any message to a counterparty (SWIFT, email, API)?
    → If YES, it is COMMIT (even if read-receipt only — see §4.8)
  □ Does this tool move money, securities, or collateral?
    → If YES, it is COMMIT
  □ Does this tool file anything with a regulator?
    → If YES, it is COMMIT
  □ Does this tool query only internal read-replicas with no write path?
    → If YES AND all above are NO, it may be READ

FOR COMMIT TOOLS ONLY:
  □ Approval policy ref:          _______________
  □ Compensating tool name:       _______________
  □ Idempotency key pattern:      _______________
  □ Max execution amount:         _______________
  □ Saga rollback tested?         [ ] Yes (test name: _________)
  □ Load tested at 10x normal?    [ ] Yes

SECURITY:
  □ Allowed roles defined?        [ ] Yes (list: _________)
  □ PII in arguments?             [ ] Yes → field-level encryption required
  □ External API called?          [ ] Yes → credentials in Vault, not code
  □ Timeout set?                  _____ ms

SIGN-OFF:
  □ Tool author:                  _______________
  □ Security review:              _______________
  □ Architecture review:          _______________
  □ Compliance review (commit):   _______________
```

## 4.8 Classification Edge Cases

### Edge Case: Side-Effectful Read Tools

A read-receipt notification to a counterparty ("we have received your SWIFT message") looks like a read but is a COMMIT because:
1. It sends an external message that cannot be unsent.
2. It starts a legal timer (SWIFT acknowledgement SLAs).
3. The counterparty will rely on it to proceed with their side of the trade.

**Resolution:** Any tool that causes an observable side effect in an external system — no matter how "minor" — is COMMIT. When in doubt, classify up.

### Edge Case: Conditional Side Effects

```python
# WRONG — conditional commit embedded in a read tool
def check_credit_limit(counterparty_id, amount):
    limit = fetch_credit_limit(counterparty_id)
    if limit < amount:
        send_decline_notice(counterparty_id)  # <── side effect!
    return limit
```

Split into two tools: `check-credit-limit` (READ) and `send-decline-notice` (COMMIT). The orchestrator decides whether to invoke the second tool based on the first tool's output.

### Edge Case: Idempotent Commit Tools

A tool that can be safely re-run (e.g., `set-settlement-status`) might seem like it could bypass the approval gate on replay. It cannot. The idempotency key prevents duplicate execution — the approval gate is checked once, and the receipt is stored alongside the idempotency record. Replays return the cached result without re-opening a gate.

## 4.9 Anti-Patterns

### Anti-Pattern 1: Tools That Are "Sort of Commit"

```python
# WRONG — propose tool that sometimes commits
def draft_and_maybe_execute_settlement(trade_id, auto_execute=False):
    draft = _build_draft(trade_id)
    if auto_execute:                 # <── hidden commit path
        return _execute(draft)
    return draft
```

A tool's class must be stable and declared at registration time. It cannot change at runtime based on flags. Split this into `draft-settlement` (PROPOSE) and `execute-settlement` (COMMIT).

### Anti-Pattern 2: Commit Flags on Propose Tools

```python
# WRONG
def generate_reg_filing(trade_id, submit=False):  # <── submit is a commit flag
    ...
```

The `submit` parameter makes this tool sometimes a PROPOSE and sometimes a COMMIT. The router cannot enforce policy on a dynamic class. Separate tools, separate contracts.

### Anti-Pattern 3: LLM Calling Commit Directly

The most dangerous anti-pattern. If the agent's tool manifest includes commit tools, the LLM can and will invoke them directly — either through hallucination or adversarial prompt injection.

**Prevention (defense in depth):**
1. The agent's tool manifest is generated at runtime and never includes commit tools.
2. The Tool Router blocks commit calls from any non-orchestrator context.
3. The commit executor verifies a valid approval receipt before execution.
4. The audit log records any attempt to call a commit tool improperly as a `RULES_VIOLATION(fatal)` event.

---

# How the Four Standards Compose

## End-to-End: Single Settlement Workflow

This section traces a single FX settlement workflow — $5M JPY→USD — through all four standards from initiation to completion.

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│              Settlement Workflow: FX $5M JPY→USD (TRD-2026-00432)                │
│                                                                                   │
│  Standards touched per step:                                                      │
│    E = Event Protocol    A = Approval Contract                                    │
│    U = Audit Contract    T = Tool Classification                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

## Composite Sequence Diagram

```
Trader UI   Orchestrator    Agent      ToolRouter   ApprovalSvc   AuditSvc   ExternalSys
    │             │            │            │             │            │            │
    │─submit()───▶│            │            │             │            │            │
    │             │            │            │             │            │            │
    │  [E] RUN_STARTED published to Kafka / SSE                        │            │
    │             │─audit()───────────────────────────────────────────▶│            │
    │             │            │            │             │            │            │
    │             │─run(agent)─▶│            │             │            │            │
    │             │            │            │             │            │            │
    │  [T] Agent calls READ tool: fetch-market-data                    │            │
    │             │            │─tool_call()─▶│            │            │            │
    │             │            │  class=READ │             │            │            │
    │             │            │  → PASS     │             │            │            │
    │             │            │◀─result─────│             │            │            │
    │  [E] TOOL_CALL_START / TOOL_CALL_END published                   │            │
    │             │─audit()───────────────────────────────────────────▶│            │
    │             │            │            │             │            │            │
    │  [T] Agent calls PROPOSE tool: draft-settlement                  │            │
    │             │            │─tool_call()─▶│            │            │            │
    │             │            │  class=PROPOSE            │            │            │
    │             │            │  → PASS     │             │            │            │
    │             │            │◀─draft──────│             │            │            │
    │  [E] TOOL_CALL_START / TOOL_CALL_END / EVIDENCE_PINNED           │            │
    │             │─audit()───────────────────────────────────────────▶│            │
    │             │            │            │             │            │            │
    │  [T] Agent ATTEMPTS to call COMMIT tool: execute-settlement      │            │
    │             │            │─tool_call()─▶│            │            │            │
    │             │            │  class=COMMIT             │            │            │
    │             │            │  !! BLOCKED !!            │            │            │
    │             │            │◀─PolicyError─│            │            │            │
    │  [E] RULES_VIOLATION(fatal, "agent attempted commit") published               │
    │             │─audit()───────────────────────────────────────────▶│            │
    │             │            │            │             │            │            │
    │  [A] Orchestrator opens approval gate                            │            │
    │             │─open_gate()────────────────────────────▶│            │            │
    │  [E] APPROVAL_REQUEST published                      │            │            │
    │  [E] INTERRUPT(reason=approval_required, blocking=true)          │            │
    │  [E] WORKFLOW_STATE_CHANGED(running→awaiting_approval)           │            │
    │             │─audit()───────────────────────────────────────────▶│            │
    │◀──────────────────────────────────────SSE: APPROVAL_REQUEST──────│            │
    │             │            │            │             │            │            │
    │  [SLA timer starts at T+0]                          │            │            │
    │             │            │            │  [T+30min]  │            │            │
    │  [E] SLA_BREACH_WARNING(warning)                    │            │            │
    │◀──────────────────────────────────────SSE: SLA_BREACH_WARNING────│            │
    │             │            │            │             │            │            │
    │─approve()──▶│            │            │             │            │            │
    │  [A] Approver-L1 casts vote 1/2                     │            │            │
    │             │─cast_vote()────────────────────────────▶│            │            │
    │             │  Rule 1: role ✓                       │            │            │
    │             │  Rule 2: not originator ✓             │            │            │
    │             │  Rule 3: no dup ✓                     │            │            │
    │             │  Rule 5: 1 < quorum(2) → still open   │            │            │
    │  [E] APPROVAL_GRANTED(vote=1, quorum_reached=false) │            │            │
    │             │─audit()───────────────────────────────────────────▶│            │
    │             │            │            │             │            │            │
    │─approve()──▶│  (Approver-L2)         │             │            │            │
    │  [A] Approver-L2 casts vote 2/2                     │            │            │
    │             │─cast_vote()────────────────────────────▶│            │            │
    │             │  Rule 5: 2 >= quorum(2) → APPROVED    │            │            │
    │             │◀─receipt───────────────────────────────│            │            │
    │  [E] APPROVAL_GRANTED(vote=2, quorum_reached=true)  │            │            │
    │  [E] WORKFLOW_STATE_CHANGED(awaiting_approval→executing_commit)               │
    │             │─audit()───────────────────────────────────────────▶│            │
    │             │            │            │             │            │            │
    │  [T] Orchestrator (Commit Executor) calls COMMIT tool            │            │
    │             │─commit_execute()────────▶│            │            │            │
    │             │  caller=orchestrator_commit_executor               │            │
    │             │  receipt attached        │             │            │            │
    │             │  receipt.hash verified   │             │            │            │
    │             │  idempotency_key checked─▶            │            │            │
    │             │  → execute_settlement()  │────────────────────────────────────▶│
    │             │                          │            │            │  external  │
    │             │                          │            │            │  system    │
    │             │                          │◀─result────────────────────────────│
    │  [E] TOOL_CALL_END(status=success)                  │            │            │
    │  [E] RUN_FINISHED(outcome=completed)                │            │            │
    │  [U] AuditEntry with llm_input, llm_output, receipt │            │            │
    │             │─audit()───────────────────────────────────────────▶│            │
    │             │            │            │             │            │[U] hash    │
    │             │            │            │             │            │   chain    │
    │             │            │            │             │            │   updated  │
    │◀──────────────────────────────────────SSE: RUN_FINISHED──────────│            │
```

## Cross-Standard Data Flow

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                        Cross-Standard Binding Points                              │
│                                                                                   │
│  Standard 1 (Event Protocol)                                                      │
│    ├── Carries gate_id → links to Standard 2 (Approval)                          │
│    ├── Carries workflow_id → links to Standard 3 (Audit)                         │
│    ├── Carries tool_call_id → links to Standard 4 (Tool)                         │
│    └── correlation_id threads through ALL standards                               │
│                                                                                   │
│  Standard 2 (Approval Contract)                                                   │
│    ├── receipt.gate_id referenced in APPROVAL_GRANTED events (→ Standard 1)      │
│    ├── receipt stored as AuditEntry payload (→ Standard 3)                       │
│    └── receipt required before CommitExecutor fires (→ Standard 4)               │
│                                                                                   │
│  Standard 3 (Audit Contract)                                                      │
│    ├── Every event from Standard 1 becomes an AuditEntry                         │
│    ├── ApprovalReceipt from Standard 2 stored as payload                         │
│    └── Every tool execution from Standard 4 logged with LLM I/O                 │
│                                                                                   │
│  Standard 4 (Tool Classification)                                                 │
│    ├── TOOL_CALL_START/END events published to Standard 1                        │
│    ├── Commit tools blocked until ApprovalReceipt from Standard 2                │
│    └── All tool executions logged to Standard 3                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

## Invariants That Must Hold Across All Four Standards

These invariants must be verifiable at any point in time by the audit team or a regulator:

1. **Every COMMIT tool execution has a valid ApprovalReceipt** — the receipt_hash in the AuditEntry must match the receipt stored in the Approval Service.

2. **Every AI-generated action has LLM I/O in the audit log** — if actor.kind is "agent", llm_input and llm_output must be non-null in the AuditEntry.

3. **The correlation_id in every event matches the workflow_id in the AuditEntry** — enabling cross-system join without an additional lookup.

4. **No event_type appears in the audit log that is not in the central event registry** — the registry is the single source of truth; unregistered events are rejected at the Audit Service write boundary.

5. **The hash chain is unbroken for every tenant** — the verify endpoint checks this and the result is published weekly to the compliance reporting dashboard.

6. **No OVERRIDE_INVOKED entry exists without a corresponding post_review_completed=true within 24 hours** — the Temporal escalation workflow enforces this with a supervisor task.

---

## Appendix A: Key Design Decisions and Rationale

| Decision | Alternative Considered | Rationale |
|---|---|---|
| UUIDv7 for event_id | Auto-increment integer | UUIDv7 is time-ordered (sortable), globally unique without coordination, safe to expose in URLs |
| SSE for streaming | WebSocket | SSE is unidirectional, stateless, works through HTTP proxies, trivially load-balanced; WebSocket requires sticky sessions |
| Temporal for SLA timers | Cron job + DB flags | Temporal survives process restarts, provides built-in retry, timeout, and cancellation semantics |
| SHA-256 hash chain | Merkle tree | Linear chain is simpler to verify and sufficient for sequential audit logs; Merkle is needed for parallel append which this design avoids |
| Policy Registry in DB | Hardcoded policies | DB-driven policies can be updated without code deployment; audit trail of policy changes; supports A/B testing of policies |
| BEGIN IMMEDIATE for audit writes | Optimistic locking | At audit scale, conflicts are rare but consequences (chain gap) are severe; pessimistic lock is correct |
| COMMIT tools blocked from agent manifest | Post-hoc enforcement | Defense-in-depth: never give the agent the capability; don't rely solely on runtime enforcement |

## Appendix B: Operational Runbook References

- Incident: Hash chain broken → `runbooks/audit-chain-repair.md`
- Incident: SLA breached with no escalation → `runbooks/approval-sla-oncall.md`
- Incident: Agent attempted commit tool call → `runbooks/policy-violation-p0.md`
- Incident: Override invoked, post-review overdue → `runbooks/override-review-escalation.md`
- Capacity: Audit service write throughput → `runbooks/audit-scaling.md`
