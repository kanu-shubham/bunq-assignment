"""Domain model for the incident auto-remediation system.

Design note worth saying out loud in a design review: the state object is the
API between agents. Agents share no conversation; they share *this*. So it is
typed, it is append-mostly, and every derived claim carries who produced it and
how confident they were. If an agent needs something, it must be a field here —
"the other agent mentioned it earlier" is not a mechanism.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    SEV1 = "SEV1"  # total outage / data loss risk — page, never auto-remediate blind
    SEV2 = "SEV2"  # major degradation, customer-visible
    SEV3 = "SEV3"  # partial degradation, workaround exists
    SEV4 = "SEV4"  # cosmetic / internal only

    @property
    def rank(self) -> int:
        return {"SEV1": 1, "SEV2": 2, "SEV3": 3, "SEV4": 4}[self.value]


class RiskTier(str, Enum):
    """How dangerous an action is, independent of how confident we are.

    The pairing rule the whole system rests on:
        auto-execute  iff  risk is low  AND  confidence is high  AND  blast
                           radius is bounded  AND  it is reversible.
    Drop any one and it needs a human.
    """

    READ_ONLY = "read_only"  # cannot change anything
    LOW = "low"  # reversible, single instance (restart one pod)
    MEDIUM = "medium"  # reversible, service-wide (scale, feature flag)
    HIGH = "high"  # hard to reverse or multi-service (rollback, failover)
    CRITICAL = "critical"  # data-affecting or irreversible (drop, truncate, delete)


class Phase(str, Enum):
    INGESTED = "ingested"
    TRIAGED = "triaged"
    DIAGNOSED = "diagnosed"
    PLANNED = "planned"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    RESOLVED = "resolved"
    ROLLED_BACK = "rolled_back"
    ESCALATED = "escalated"
    SUPPRESSED = "suppressed"


@dataclass
class Alert:
    id: str
    service: str
    title: str
    description: str
    signal: str  # "error_rate" | "latency_p99" | "saturation" | ...
    value: float
    threshold: float
    environment: str = "prod"
    fired_at: float = field(default_factory=time.time)
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        """Dedupe key. Alert storms are the normal case, not the exception —
        one bad deploy fires forty alerts, and forty parallel remediation runs
        fighting each other is a self-inflicted second outage."""
        return f"{self.service}:{self.signal}:{self.environment}"


@dataclass
class Finding:
    """One observation from one analyst. Evidence, not conclusion."""

    agent: str
    summary: str
    evidence: str
    signal_strength: float = 0.5  # 0..1
    tags: list[str] = field(default_factory=list)
    at: float = field(default_factory=time.time)

    @property
    def dedupe_key(self) -> str:
        return f"{self.agent}:{self.summary[:80]}"


@dataclass
class Hypothesis:
    """A candidate root cause with a confidence and the findings behind it."""

    cause: str
    confidence: float
    supporting: list[str] = field(default_factory=list)
    contradicting: list[str] = field(default_factory=list)
    remediation_hint: str = ""

    @property
    def dedupe_key(self) -> str:
        return self.cause.lower().strip()


@dataclass
class Action:
    """One remediation step. Deliberately not free-form.

    `tool` must name an entry in the action catalog (policy.ACTION_CATALOG).
    A planner that can emit arbitrary shell is a planner that can do anything;
    a planner that can only emit catalog entries has a bounded worst case, and
    that bound is what makes auto-remediation defensible to a risk team.
    """

    tool: str
    target: str  # "service/checkout-api", "deployment/checkout-api"
    params: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    @property
    def idempotency_key(self) -> str:
        """Same action, same target, same params -> same key.

        The executor refuses to run a key twice. Without this, any retry — of a
        node, a superstep, or the whole graph after a crash — re-applies side
        effects. Retries are certain; idempotency is not optional.
        """
        items = ",".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        return f"{self.tool}|{self.target}|{items}"


@dataclass
class Plan:
    actions: list[Action] = field(default_factory=list)
    rationale: str = ""
    rollback: list[Action] = field(default_factory=list)
    expected_effect: str = ""
    author: str = "planner"
    revision: int = 0


@dataclass
class ExecutionRecord:
    action_id: str
    tool: str
    target: str
    status: str  # "succeeded" | "failed" | "skipped" | "blocked"
    detail: str = ""
    duration_ms: float = 0.0
    at: float = field(default_factory=time.time)

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


@dataclass
class ApprovalRequest:
    reason: str
    risk: RiskTier
    blast_radius: int
    actions: list[str]
    requested_at: float = field(default_factory=time.time)
    deadline_seconds: float = 900.0
    on_expiry: str = "escalate"


def new_state(alert: Alert, **overrides) -> dict[str, Any]:
    """Initial graph state. Keys here must match the reducers in incident.graph."""
    state: dict[str, Any] = {
        "alert": alert,
        "incident_id": f"INC-{uuid.uuid4().hex[:8].upper()}",
        "phase": Phase.INGESTED.value,
        "severity": None,
        "findings": [],
        "hypotheses": [],
        "plan": None,
        "critiques": [],
        "plan_revisions": 0,
        "approval": None,
        "executions": [],
        "verification": None,
        "attempts": 0,
        "events": [],
        "notes": [],
        "cost_usd": 0.0,
    }
    state.update(overrides)
    return state


def to_jsonable(value: Any) -> Any:
    """Checkpoints must serialize. Dataclasses and enums are the two things that
    trip this up, so normalize both in one place."""
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {k: to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return value
