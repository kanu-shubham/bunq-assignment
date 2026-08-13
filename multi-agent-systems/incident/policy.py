"""Policy: what the system is allowed to do without asking.

This module is the reason an auto-remediation system is deployable. Everything
else is agents proposing things; this decides which proposals become actions.

Three principles it encodes:

  1. **Deterministic authority.** The decision to execute is made by Python, not
     by a model. A model proposes; policy disposes. A prompt-injected or simply
     confused agent still cannot exceed the catalog, and that property survives
     any model update.

  2. **Risk x confidence, not risk alone.** A low-risk action on a shaky
     hypothesis is still a bad idea; a medium-risk action on an obvious,
     well-evidenced cause is fine. The gate is a 2-D matrix, not a threshold.

  3. **Blast radius is computed, not asserted.** The planner does not get to
     claim its change is small. Blast radius is derived from the target's
     dependency fan-in and traffic share, on our side of the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .state import Action, RiskTier, Severity


@dataclass(frozen=True)
class ActionSpec:
    tool: str
    risk: RiskTier
    reversible: bool
    max_blast_radius: int  # requests/min; refuse outright above this, even with approval
    description: str
    required_params: tuple[str, ...] = ()


# The allow-list. An action outside this catalog cannot be executed — this is
# the hard boundary on the system's worst case.
ACTION_CATALOG: dict[str, ActionSpec] = {
    "restart_pods": ActionSpec(
        "restart_pods", RiskTier.LOW, False, 20_000,
        "Restart N pods. Clears transient state; not a fix for a code bug.",
        ("service", "count"),
    ),
    "scale": ActionSpec(
        "scale", RiskTier.MEDIUM, True, 30_000,
        "Change replica count. Buys headroom; does not fix a leak.",
        ("service", "replicas"),
    ),
    "set_feature_flag": ActionSpec(
        "set_feature_flag", RiskTier.MEDIUM, True, 50_000,
        "Toggle a feature flag. Fastest way to disable a bad code path.",
        ("service", "flag", "enabled"),
    ),
    "resize_connection_pool": ActionSpec(
        "resize_connection_pool", RiskTier.MEDIUM, True, 30_000,
        "Resize a connection pool. Watch the downstream's own limits.",
        ("service", "size"),
    ),
    "rollback_deploy": ActionSpec(
        "rollback_deploy", RiskTier.HIGH, True, 40_000,
        "Roll back to the previous version. High impact, usually correct after a bad deploy.",
        ("service",),
    ),
    "drain_and_failover": ActionSpec(
        "drain_and_failover", RiskTier.CRITICAL, False, 120_000,
        "Fail over to standby. Last resort; expensive to reverse.",
        ("service",),
    ),
    "page_oncall": ActionSpec(
        "page_oncall", RiskTier.READ_ONLY, False, 0,  # exempt: see the READ_ONLY carve-out below
        "Page a human. Always allowed — escalation must never be blocked by policy.",
        ("service", "message"),
    ),
}

# Requests per minute, as a stand-in for whatever your real traffic signal is.
SERVICE_TRAFFIC: dict[str, int] = {
    "checkout-api": 12_000,
    "payments-worker": 4_000,
    "ledger-db-proxy": 20_000,
}

DEPENDENTS: dict[str, list[str]] = {
    "ledger-db-proxy": ["checkout-api", "payments-worker"],
    "payments-worker": ["checkout-api"],
    "checkout-api": [],
}


@dataclass
class Guardrails:
    environment: str = "prod"
    change_freeze: bool = False  # e.g. Black Friday, quarter close
    allowed_services: tuple[str, ...] = tuple(SERVICE_TRAFFIC)
    max_actions_per_plan: int = 5
    max_auto_blast_radius: int = 15_000
    max_remediation_attempts: int = 2
    auto_approve_severities: tuple[Severity, ...] = (Severity.SEV2, Severity.SEV3, Severity.SEV4)
    # SEV1 is deliberately absent: a total outage is exactly when a wrong
    # automated action does the most damage, and exactly when a human is
    # already looking. Automate diagnosis, gate the action.


@dataclass
class Decision:
    allowed: bool
    requires_approval: bool
    reason: str
    risk: RiskTier = RiskTier.READ_ONLY
    blast_radius: int = 0
    violations: list[str] = field(default_factory=list)

    @property
    def auto_executable(self) -> bool:
        return self.allowed and not self.requires_approval


def blast_radius(action: Action) -> int:
    """Estimated requests-per-minute affected, including transitive dependents.

    Rough by design — the number does not need to be accurate, it needs to be
    *ordinal and computed by us*. What it must never be is a field the planner
    fills in.
    """
    service = action.params.get("service") or action.target.split("/")[-1]
    total = SERVICE_TRAFFIC.get(service, 1_000)
    for dependent in DEPENDENTS.get(service, []):
        total += SERVICE_TRAFFIC.get(dependent, 0) // 2  # partial impact
    if action.tool == "restart_pods":
        # A rolling restart of a few pods touches a slice, not the whole service.
        count = int(action.params.get("count", 1) or 1)
        total = int(total * min(1.0, count / 10))
    if action.tool == "drain_and_failover":
        total *= 2  # region-level event
    return total


def evaluate(
    action: Action,
    *,
    severity: Severity,
    confidence: float,
    guardrails: Guardrails,
    attempts: int = 0,
) -> Decision:
    spec = ACTION_CATALOG.get(action.tool)
    if spec is None:
        return Decision(False, False, f"{action.tool!r} is not in the action catalog")

    violations: list[str] = []
    service = action.params.get("service") or action.target.split("/")[-1]
    radius = blast_radius(action)

    missing = [p for p in spec.required_params if p not in action.params]
    if missing:
        violations.append(f"missing required params: {', '.join(missing)}")
    if service not in guardrails.allowed_services:
        violations.append(f"service {service!r} is not in scope for automation")
    # Escalation is exempt from the radius cap — paging a human has no blast
    # radius in the sense that matters, and a policy that can block escalation
    # is a policy that can trap the system with nobody watching.
    if spec.risk is not RiskTier.READ_ONLY and radius > spec.max_blast_radius:
        violations.append(
            f"blast radius {radius} exceeds hard cap {spec.max_blast_radius} for {action.tool}"
        )
    if attempts >= guardrails.max_remediation_attempts:
        violations.append(
            f"remediation attempt limit reached ({attempts}/{guardrails.max_remediation_attempts})"
        )
    if violations:
        return Decision(False, False, "; ".join(violations), spec.risk, radius, violations)

    # page_oncall is always permitted — never let policy block escalation.
    if spec.risk is RiskTier.READ_ONLY:
        return Decision(True, False, "read-only / escalation action", spec.risk, radius)

    if guardrails.change_freeze:
        return Decision(True, True, "change freeze in effect", spec.risk, radius)
    if severity not in guardrails.auto_approve_severities:
        return Decision(True, True, f"{severity.value} requires a human decision", spec.risk, radius)
    if radius > guardrails.max_auto_blast_radius:
        return Decision(
            True, True, f"blast radius {radius} above auto threshold "
            f"{guardrails.max_auto_blast_radius}", spec.risk, radius
        )
    if not spec.reversible and spec.risk not in (RiskTier.LOW, RiskTier.READ_ONLY):
        return Decision(True, True, "irreversible action above low risk", spec.risk, radius)

    # The risk x confidence matrix. Higher risk demands a better-evidenced cause.
    required_confidence = {
        RiskTier.LOW: 0.45,
        RiskTier.MEDIUM: 0.65,
        RiskTier.HIGH: 0.85,
        RiskTier.CRITICAL: 1.01,  # unreachable: never auto-execute critical
    }[spec.risk]
    if confidence < required_confidence:
        return Decision(
            True, True,
            f"confidence {confidence:.2f} below {required_confidence:.2f} required for "
            f"{spec.risk.value} risk",
            spec.risk, radius,
        )

    return Decision(
        True, False,
        f"{spec.risk.value} risk, confidence {confidence:.2f}, blast radius {radius}",
        spec.risk, radius,
    )


def evaluate_plan(
    actions: list[Action], *, severity: Severity, confidence: float,
    guardrails: Guardrails, attempts: int = 0,
) -> tuple[list[tuple[Action, Decision]], Decision]:
    """Per-action decisions plus the aggregate verdict for the plan.

    Aggregation is strict: the plan needs approval if *any* action does, and is
    rejected if any action is disallowed. Partial execution of a multi-step plan
    leaves the system in a state nobody designed.
    """
    if len(actions) > guardrails.max_actions_per_plan:
        overall = Decision(
            False, False,
            f"plan has {len(actions)} actions, cap is {guardrails.max_actions_per_plan}",
        )
        return [], overall

    per_action = [
        (a, evaluate(a, severity=severity, confidence=confidence,
                     guardrails=guardrails, attempts=attempts))
        for a in actions
    ]
    if not per_action:
        return [], Decision(False, False, "empty plan")

    blocked = [(a, d) for a, d in per_action if not d.allowed]
    if blocked:
        return per_action, Decision(
            False, False,
            "; ".join(f"{a.tool}: {d.reason}" for a, d in blocked),
            violations=[d.reason for _, d in blocked],
        )

    needs_approval = [(a, d) for a, d in per_action if d.requires_approval]
    worst = max((d.risk for _, d in per_action), key=lambda r: list(RiskTier).index(r))
    total_radius = max((d.blast_radius for _, d in per_action), default=0)

    if needs_approval:
        return per_action, Decision(
            True, True,
            "; ".join(f"{a.tool}: {d.reason}" for a, d in needs_approval),
            worst, total_radius,
        )
    return per_action, Decision(True, False, "all actions within auto-remediation policy", worst, total_radius)


def describe_catalog() -> str:
    """Rendered into the planner's prompt so it can only propose real actions."""
    return "\n".join(
        f"- {spec.tool}({', '.join(spec.required_params)}): {spec.description} "
        f"[risk={spec.risk.value}, reversible={spec.reversible}]"
        for spec in ACTION_CATALOG.values()
    )


def summarize(decisions: list[tuple[Action, Decision]]) -> list[dict[str, Any]]:
    return [
        {
            "action": f"{a.tool}({a.params})",
            "allowed": d.allowed,
            "requires_approval": d.requires_approval,
            "risk": d.risk.value,
            "blast_radius": d.blast_radius,
            "reason": d.reason,
        }
        for a, d in decisions
    ]
