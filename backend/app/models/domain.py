"""Core domain models for the HITL financial workflow system."""
from __future__ import annotations
import enum
from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field
import uuid


def new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class WorkflowType(str, enum.Enum):
    SETTLEMENT = "SETTLEMENT"
    RECONCILIATION = "RECONCILIATION"
    DISPUTE = "DISPUTE"
    MARGIN_CALL = "MARGIN_CALL"
    REGULATORY_FILING = "REGULATORY_FILING"


class WorkflowState(str, enum.Enum):
    OPEN = "OPEN"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    DISPUTED = "DISPUTED"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"


class ToolClass(str, enum.Enum):
    READ = "read"
    PROPOSE = "propose"
    COMMIT = "commit"


class ApprovalStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"


class UserRole(str, enum.Enum):
    TRADER = "TRADER"
    RISK = "RISK"
    COMPLIANCE = "COMPLIANCE"
    OPERATIONS = "OPERATIONS"
    LEGAL = "LEGAL"
    ADMIN = "ADMIN"


# ---------------------------------------------------------------------------
# AG-UI Event types (finance-extended)
# ---------------------------------------------------------------------------

class AGUIEventType(str, enum.Enum):
    # Standard AG-UI
    RUN_STARTED = "RUN_STARTED"
    RUN_FINISHED = "RUN_FINISHED"
    TEXT_MESSAGE_CONTENT = "TEXT_MESSAGE_CONTENT"
    TOOL_CALL_START = "TOOL_CALL_START"
    TOOL_CALL_END = "TOOL_CALL_END"
    STATE_SNAPSHOT = "STATE_SNAPSHOT"
    STATE_DELTA = "STATE_DELTA"
    # Finance-specific HITL extensions
    APPROVAL_REQUEST = "APPROVAL_REQUEST"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    OVERRIDE_INVOKED = "OVERRIDE_INVOKED"
    COUNTERPARTY_INPUT_REQUIRED = "COUNTERPARTY_INPUT_REQUIRED"
    REG_FILING_DRAFTED = "REG_FILING_DRAFTED"
    EVIDENCE_PINNED = "EVIDENCE_PINNED"
    WORKFLOW_STATE_CHANGED = "WORKFLOW_STATE_CHANGED"
    AUDIT_EVENT = "AUDIT_EVENT"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class WorkflowInstance(BaseModel):
    id: str = Field(default_factory=new_id)
    type: WorkflowType
    state: WorkflowState = WorkflowState.OPEN
    title: str
    description: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None
    created_by: str = "system"
    risk_score: float = 0.0
    llm_reasoning: str = ""
    llm_proposal: dict[str, Any] = Field(default_factory=dict)


class ApprovalRecord(BaseModel):
    actor: str
    role: UserRole
    decision: str  # "approve" | "deny"
    comment: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ApprovalGate(BaseModel):
    id: str = Field(default_factory=new_id)
    workflow_id: str
    required_roles: list[UserRole]
    quorum: int  # N of M
    deadline: Optional[datetime] = None
    approvals: list[ApprovalRecord] = Field(default_factory=list)
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AuditEvent(BaseModel):
    id: str = Field(default_factory=new_id)
    parent_hash: str  # "GENESIS" for first event
    hash: str = ""  # computed on creation
    event_type: str
    workflow_id: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    llm_input: Optional[str] = None
    llm_output: Optional[str] = None
    actor: str = "system"
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ToolDefinition(BaseModel):
    name: str
    description: str
    tool_class: ToolClass
    approval_policy: Optional[str] = None  # e.g. "2-of-3"
    allowed_roles: list[UserRole] = Field(default_factory=list)


class AGUIEvent(BaseModel):
    event_type: AGUIEventType
    workflow_id: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# API request/response models
# ---------------------------------------------------------------------------

class CreateWorkflowRequest(BaseModel):
    type: WorkflowType
    title: str
    description: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_by: str = "ops_user"


class ApprovalAction(BaseModel):
    actor: str
    role: UserRole
    decision: str  # "approve" or "deny"
    comment: str = ""


class WorkflowListItem(BaseModel):
    id: str
    type: WorkflowType
    state: WorkflowState
    title: str
    risk_score: float
    created_at: datetime
    updated_at: datetime
    pending_approvals: int = 0
