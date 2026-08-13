"""The agents.

Roles and why each one exists as a separate agent rather than one big prompt:

  triage        classify + dedupe. Cheap model, runs on every alert, so it must
                be fast. Its job is mostly to *stop* work (suppression).
  analysts x3   metrics / logs / deploys+deps. Read-only tools, isolated
                contexts, run in parallel. Specialists: different questions.
  synthesizer   ranks root-cause hypotheses across analysts. Aggregator.
  planner       hypothesis -> catalog actions. Constrained generation.
  safety critic reviews the plan against policy and the evidence. Critic+refiner.
  executor      runs actions under locks with idempotency. No model involved.
  verifier      did the SLO recover? Deterministic, time-windowed.
  scribe        writes episodic + semantic + candidate procedural memory.

Every LLM-using agent has a deterministic fallback. That is not a testing
convenience — it is the answer to "what happens when the model is down, rate
limited, or refuses?" An auto-remediation system that stops working when the
model does is worse than no automation, because the on-call has stopped
watching.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from magents.llm import LLM
from magents.memory import (
    EpisodicStore,
    MemoryRecord,
    ProceduralStore,
    Provenance,
    Scope,
    SemanticStore,
    Tier,
)
from .policy import describe_catalog
from .state import Action, Alert, Finding, Hypothesis, Plan, Severity
from .tools import SimulatedCluster

# --------------------------------------------------------------------------
# Prompts. Kept as module constants so their fingerprints are stable and
# cacheable — a prompt built by string concatenation at call time silently
# breaks prompt caching and costs real money in a loop like this.
# --------------------------------------------------------------------------
TRIAGE_SYSTEM = """You triage production alerts for a payments platform.

Assign a severity:
- SEV1: total outage, or money/data at risk. Humans are already awake.
- SEV2: major customer-visible degradation, revenue impact.
- SEV3: partial degradation with a workaround.
- SEV4: internal or cosmetic only.

Also decide whether this alert should be suppressed (a duplicate of an active
incident, a known-flapping signal, or a non-prod environment).

Return JSON: {"severity": "SEV1|SEV2|SEV3|SEV4", "suppress": bool,
"customer_impact": "...", "reasoning": "one sentence"}"""

SYNTH_SYSTEM = """You rank root-cause hypotheses from independent analyst findings.

Rules that matter here:
- Correlation is not cause. A deploy 6 minutes before the alert is strong
  evidence; a deploy 6 hours before is weak.
- Symptoms in service X often originate in a service X depends on. Check the
  dependency findings before blaming the service that alerted.
- If two analysts independently point at the same cause, say so — that is your
  strongest signal. If only one does, mark it single-sourced.
- Confidence must reflect the evidence you were given, not how plausible the
  story sounds.

Return JSON: {"hypotheses": [{"cause": "...", "confidence": 0.0-1.0,
"supporting": ["..."], "contradicting": ["..."], "remediation_hint": "..."}]}
Ranked by confidence, at most 4."""

PLANNER_SYSTEM = """You write a remediation plan for a production incident.

You may ONLY use these actions:
<<CATALOG>>

Rules:
- Mitigate first, fix later. The goal is to stop customer impact, not to
  correct the underlying bug.
- Pick the smallest action that addresses the *stated root cause*. Restarting
  pods does not fix a bad deploy, and scaling does not fix a null pointer.
- Prefer reversible actions. Provide a rollback action for each one where an
  inverse exists.
- Two actions maximum unless you can justify more.
- If the evidence does not support any action, return an empty action list and
  say why. An empty plan is a valid, sometimes correct, answer.

Return JSON: {"actions": [{"tool": "...", "target": "service/NAME",
"params": {...}, "reason": "..."}], "rationale": "...",
"expected_effect": "what metric should move, and by when"}"""

CRITIC_SYSTEM = """You are the safety reviewer for an automated remediation plan.
You are the last check before this touches production.

Reject or flag when:
- The action does not follow from the root cause (the most common failure).
- Blast radius is larger than the incident being fixed.
- The action is irreversible and the evidence is thin.
- The plan treats a symptom in service A when the evidence points at service B.
- Two actions could interact badly (scaling while rolling back, for instance).

Report every concern with a severity. Return verdict "pass" with an empty issues
list when the plan is sound — do not manufacture objections to seem careful.

Return JSON: {"verdict": "pass|revise|reject", "score": 0-10,
"issues": [{"severity": "high|medium|low", "what": "...", "fix": "..."}],
"summary": "one sentence"}"""

SCRIBE_SYSTEM = """Write a postmortem record from an incident timeline.

Return JSON:
{"summary": "2-3 sentences: what broke, why, what fixed it",
 "facts": [{"key": "stable-slug", "content": "durable fact", "confidence": 0.0-1.0}],
 "runbook": {"name": "slug", "when": "trigger condition", "steps": ["..."]} or null}

A fact is durable only if it will still be true and useful next month. Do not
record the current error rate. Do record "checkout-api pricing-v2 path throws on
null discount codes".

Propose a runbook only if this incident is likely to recur in the same shape."""


@dataclass
class AgentContext:
    """Everything an agent may touch. Passing this explicitly rather than reaching
    for globals is what makes the whole system testable with a fake cluster and a
    scripted model."""

    llm: LLM
    cluster: SimulatedCluster
    episodic: EpisodicStore
    semantic: SemanticStore
    procedural: ProceduralStore
    worker_llm: LLM | None = None  # cheap tier for high-volume analysts
    tracer: Any = None

    def cheap(self) -> LLM:
        return self.worker_llm or self.llm


def _json_call(
    llm: LLM, system: str, user: str, default: Any, *, effort: str = "high", max_tokens: int = 2048
) -> Any:
    """One place where model output becomes typed data — and where a refusal,
    a truncation, or unparseable output turns into a documented default instead
    of an exception three frames deeper."""
    try:
        out = llm.complete(system, [{"role": "user", "content": user}],
                           max_tokens=max_tokens, effort=effort)
    except Exception:  # noqa: BLE001 - model outage must not crash remediation
        return default
    if out.stop_reason == "refusal" or not out.text.strip():
        return default
    parsed = out.json(None)
    return default if parsed is None else parsed


# --------------------------------------------------------------------------
# 1. Triage
# --------------------------------------------------------------------------
def triage(alert: Alert, ctx: AgentContext, active_fingerprints: set[str]) -> dict[str, Any]:
    if alert.fingerprint in active_fingerprints:
        # Dedupe before the model call. Alert storms are the common case and
        # paying for an LLM call per duplicate is both slow and pointless.
        return {
            "severity": Severity.SEV4,
            "suppress": True,
            "reasoning": f"duplicate of active incident ({alert.fingerprint})",
            "customer_impact": "none (deduplicated)",
        }

    prior = ctx.episodic.search(f"{alert.service} {alert.signal}", k=3)
    history = "\n".join(f"- {r.content}" for r in prior) or "- none"
    alert_json = json.dumps(
        {
            "service": alert.service,
            "title": alert.title,
            "description": alert.description,
            "signal": alert.signal,
            "value": alert.value,
            "threshold": alert.threshold,
            "environment": alert.environment,
        },
        indent=2,
    )
    payload = _json_call(
        ctx.cheap(),
        TRIAGE_SYSTEM,
        f"Alert:\n{alert_json}\n\nSimilar past incidents:\n{history}",
        default=None,
        effort="low",
    )
    if payload is None:
        # Fallback: threshold ratio. Crude, but it keeps triage alive when the
        # model is unavailable and it never suppresses anything on its own.
        ratio = alert.value / max(alert.threshold, 1e-9)
        severity = (
            Severity.SEV1 if ratio > 50 else
            Severity.SEV2 if ratio > 10 else
            Severity.SEV3 if ratio > 2 else Severity.SEV4
        )
        return {
            "severity": severity,
            "suppress": False,
            "reasoning": f"heuristic fallback: value is {ratio:.1f}x threshold",
            "customer_impact": "unknown (model unavailable)",
        }
    try:
        severity = Severity(payload.get("severity", "SEV3"))
    except ValueError:
        severity = Severity.SEV3
    return {
        "severity": severity,
        "suppress": bool(payload.get("suppress", False)),
        "reasoning": str(payload.get("reasoning", "")),
        "customer_impact": str(payload.get("customer_impact", "")),
    }


# --------------------------------------------------------------------------
# 2. Analysts — deterministic tool users, one narrow question each
# --------------------------------------------------------------------------
def metrics_analyst(alert: Alert, ctx: AgentContext) -> list[Finding]:
    m = ctx.cluster.get_metrics(alert.service)
    out: list[Finding] = []
    err_ratio = m["error_rate"] / max(m["error_rate_baseline"], 1e-9)
    lat_ratio = m["latency_p99_ms"] / max(m["latency_p99_baseline_ms"], 1e-9)

    if err_ratio > 5:
        out.append(Finding(
            "metrics_analyst",
            f"error rate is {err_ratio:.0f}x baseline ({m['error_rate']:.3f})",
            json.dumps(m), min(1.0, err_ratio / 200), ["errors"],
        ))
    if lat_ratio > 3:
        out.append(Finding(
            "metrics_analyst",
            f"p99 latency is {lat_ratio:.1f}x baseline ({m['latency_p99_ms']:.0f}ms)",
            json.dumps(m), min(1.0, lat_ratio / 15), ["latency"],
        ))
    if m["cpu_saturation"] > 0.85 or m["memory_saturation"] > 0.85:
        out.append(Finding(
            "metrics_analyst",
            f"resource saturation: cpu={m['cpu_saturation']:.0%} mem={m['memory_saturation']:.0%}",
            json.dumps(m), 0.9, ["saturation", "resources"],
        ))
    if not out:
        out.append(Finding("metrics_analyst", "metrics within normal range", json.dumps(m), 0.1, ["clean"]))
    return out


def logs_analyst(alert: Alert, ctx: AgentContext) -> list[Finding]:
    logs = ctx.cluster.get_logs(alert.service)
    out: list[Finding] = []
    if not logs["top_patterns"]:
        return [Finding("logs_analyst", "no error patterns in logs", "", 0.1, ["clean"])]

    top, count = logs["top_patterns"][0]
    strength = min(1.0, count / 30)
    out.append(Finding(
        "logs_analyst",
        f"dominant log pattern ({count} occurrences): {top}",
        "\n".join(logs["sample"][:5]), strength, ["logs"],
    ))
    # Pattern -> hypothesis mapping. Deterministic on purpose: these signatures
    # are unambiguous and asking a model to classify "OOMKilled" is a waste of a
    # call. Reserve the model for the genuinely ambiguous residue.
    blob = " ".join(logs["sample"]).lower()
    for needle, summary, tags in [
        ("oomkilled", "container was OOMKilled — memory limit exceeded", ["memory", "resources"]),
        ("connection is not available", "connection pool exhausted", ["connections"]),
        ("could not acquire connection", "connection pool exhausted", ["connections"]),
        ("nullpointerexception", "unhandled null pointer in application code", ["code_defect"]),
        ("upstream timeout", "timeouts calling a downstream dependency", ["dependency"]),
    ]:
        if needle in blob:
            out.append(Finding("logs_analyst", summary, needle, 0.85, tags))
    return out


def deploy_analyst(alert: Alert, ctx: AgentContext) -> list[Finding]:
    deploys = ctx.cluster.get_deployments(alert.service)
    deps = ctx.cluster.get_dependencies(alert.service)
    conns = ctx.cluster.get_connections(alert.service)
    out: list[Finding] = []

    for d in deploys["recent"]:
        # Temporal proximity is the signal. A deploy 8 minutes before the alert
        # is the prime suspect; one from this morning is background noise.
        if d["minutes_ago"] <= 30:
            out.append(Finding(
                "deploy_analyst",
                f"deploy {d['version']} shipped {d['minutes_ago']}min before the alert "
                f"by {d['author']} ({d['changes']})",
                json.dumps(deploys), 0.9, ["deploy", "recent_change"],
            ))
        elif d["minutes_ago"] <= 240:
            out.append(Finding(
                "deploy_analyst",
                f"deploy {d['version']} {d['minutes_ago']}min ago — too old to be a likely cause",
                json.dumps(deploys), 0.35, ["deploy_stale"],
            ))

    for name, health in deps["health"].items():
        if health["cpu_saturation"] > 0.9 or health["error_rate"] > 0.05:
            out.append(Finding(
                "deploy_analyst",
                f"downstream dependency {name} is unhealthy "
                f"(cpu={health['cpu_saturation']:.0%}, errors={health['error_rate']:.1%}) — "
                f"symptoms here may originate there",
                json.dumps(deps), 0.88, ["dependency", "downstream"],
            ))

    if conns["utilization"] >= 0.95:
        out.append(Finding(
            "deploy_analyst",
            f"connection pool at {conns['utilization']:.0%} of {conns['pool_size']}",
            json.dumps(conns), 0.85, ["connections"],
        ))
    if not out:
        out.append(Finding("deploy_analyst", "no recent changes or unhealthy dependencies", "", 0.1, ["clean"]))
    return out


ANALYSTS: dict[str, Callable[[Alert, AgentContext], list[Finding]]] = {
    "metrics_analyst": metrics_analyst,
    "logs_analyst": logs_analyst,
    "deploy_analyst": deploy_analyst,
}


# --------------------------------------------------------------------------
# 3. Synthesizer
# --------------------------------------------------------------------------
def synthesize(alert: Alert, findings: list[Finding], ctx: AgentContext) -> list[Hypothesis]:
    runbooks = ctx.semantic.search(
        f"{alert.service} {alert.signal} " + " ".join(t for f in findings for t in f.tags), k=3
    )
    prior = "\n".join(f"- {r.content}" for r in runbooks) or "- none"
    evidence = "\n".join(
        f"- [{f.agent}, strength {f.signal_strength:.2f}] {f.summary}" for f in findings
    )
    payload = _json_call(
        ctx.llm, SYNTH_SYSTEM,
        f"Alert: {alert.title} on {alert.service} ({alert.signal}={alert.value}, "
        f"threshold={alert.threshold})\n\nFindings:\n{evidence}\n\n"
        f"Relevant prior knowledge:\n{prior}",
        default=None, effort="high",
    )
    if payload is None:
        return _heuristic_hypotheses(findings)

    hypotheses = [
        Hypothesis(
            cause=str(h.get("cause", "")).strip(),
            confidence=max(0.0, min(1.0, float(h.get("confidence", 0.0)))),
            supporting=[str(s) for s in h.get("supporting", [])],
            contradicting=[str(s) for s in h.get("contradicting", [])],
            remediation_hint=str(h.get("remediation_hint", "")),
        )
        for h in payload.get("hypotheses", [])
        if str(h.get("cause", "")).strip()
    ]
    return sorted(hypotheses, key=lambda h: -h.confidence)[:4] or _heuristic_hypotheses(findings)


def _heuristic_hypotheses(findings: list[Finding]) -> list[Hypothesis]:
    """Tag-driven fallback. Also the reference the LLM output is sanity-checked
    against in tests — if the model's top hypothesis never matches the obvious
    tag-based one on unambiguous cases, the prompt has regressed."""
    tags = {t for f in findings for t in f.tags}
    rules = [
        ({"dependency", "downstream"}, "downstream dependency is saturated and shedding load", 0.85,
         "fix or shed load from the downstream service; the alerting service is a victim"),
        ({"deploy", "recent_change"}, "the recent deploy introduced a regression", 0.82,
         "roll back to the previous version"),
        ({"code_defect"}, "unhandled exception in the new code path", 0.78,
         "roll back, or disable the offending feature flag"),
        ({"connections"}, "connection pool exhaustion", 0.8, "resize the connection pool"),
        ({"memory", "resources", "saturation"}, "resource exhaustion under current load", 0.7,
         "scale out, then restart to clear accumulated state"),
    ]
    out = [
        Hypothesis(cause, conf,
                   supporting=[f.summary for f in findings if trigger & set(f.tags)],
                   remediation_hint=hint)
        for trigger, cause, conf, hint in rules
        if trigger & tags
    ]
    if not out:
        out = [Hypothesis("unknown — evidence is insufficient", 0.2,
                          remediation_hint="escalate to a human")]
    return sorted(out, key=lambda h: -h.confidence)


# --------------------------------------------------------------------------
# 4. Planner
# --------------------------------------------------------------------------
def plan_remediation(
    alert: Alert, hypotheses: list[Hypothesis], ctx: AgentContext,
    prior_critique: str = "", revision: int = 0,
) -> Plan:
    if not hypotheses:
        return Plan(rationale="no hypotheses to act on", author="planner", revision=revision)

    top = hypotheses[0]
    runbook = ctx.procedural.search(top.cause, k=1)
    runbook_text = f"\n\nApproved runbook:\n{runbook[0].content}" if runbook else ""
    critique_text = f"\n\nA previous version of this plan was rejected:\n{prior_critique}" if prior_critique else ""

    payload = _json_call(
        ctx.llm,
        PLANNER_SYSTEM.replace("<<CATALOG>>", describe_catalog()),
        f"Incident on {alert.service} ({alert.environment}).\n"
        f"Top hypothesis: {top.cause} (confidence {top.confidence:.2f})\n"
        f"Hint: {top.remediation_hint}\n"
        f"Supporting evidence:\n" + "\n".join(f"- {s}" for s in top.supporting)
        + runbook_text + critique_text,
        default=None, effort="high",
    )
    if payload is None:
        return _heuristic_plan(alert, top, ctx, revision)

    actions = []
    for raw in payload.get("actions", [])[:5]:
        tool = str(raw.get("tool", "")).strip()
        if not tool:
            continue
        params = dict(raw.get("params", {}))
        params.setdefault("service", alert.service)
        actions.append(Action(
            tool=tool,
            target=str(raw.get("target") or f"service/{params['service']}"),
            params=params,
            reason=str(raw.get("reason", "")),
        ))
    if not actions:
        return _heuristic_plan(alert, top, ctx, revision)
    return Plan(
        actions=actions,
        rationale=str(payload.get("rationale", "")),
        expected_effect=str(payload.get("expected_effect", "")),
        author="planner",
        revision=revision,
    )


def _heuristic_plan(alert: Alert, top: Hypothesis, ctx: AgentContext, revision: int) -> Plan:
    """Runbook-style mapping from cause to action.

    Not a lesser option: for known causes this is *better* than a model call —
    deterministic, instant, free, and auditable. The model earns its place on
    causes the runbook does not cover.
    """
    svc = alert.service
    cause = top.cause.lower()
    if "deploy" in cause or "regression" in cause or "exception" in cause or "code" in cause:
        actions = [Action("rollback_deploy", f"service/{svc}", {"service": svc},
                          "revert the change correlated with the onset")]
    elif "connection pool" in cause:
        current = ctx.cluster.get_connections(svc)["pool_size"]
        actions = [Action("resize_connection_pool", f"service/{svc}",
                          {"service": svc, "size": current * 2}, "double the exhausted pool")]
    elif "resource" in cause or "memory" in cause or "saturation" in cause:
        current = ctx.cluster.get_metrics(svc)["replicas"]
        actions = [Action("scale", f"service/{svc}", {"service": svc, "replicas": current * 2},
                          "add headroom while the leak is investigated")]
    elif "downstream" in cause or "dependency" in cause:
        # Deliberately escalate rather than act: the evidence points at another
        # service, and acting on this one is the classic wrong remediation.
        actions = [Action("page_oncall", f"service/{svc}",
                          {"service": svc, "message": f"{svc} degraded by downstream saturation; "
                                                      f"needs a human to decide where to act"},
                          "root cause is outside this service's blast radius")]
    else:
        actions = [Action("page_oncall", f"service/{svc}",
                          {"service": svc, "message": f"{svc}: cause unclear, automation standing down"},
                          "insufficient evidence to act safely")]
    return Plan(actions=actions, rationale=f"runbook mapping for: {top.cause}",
                expected_effect="error rate returns to baseline within 5 minutes",
                author="planner(runbook)", revision=revision)


# --------------------------------------------------------------------------
# 5. Safety critic
# --------------------------------------------------------------------------
def critique_plan(
    alert: Alert, plan: Plan, hypotheses: list[Hypothesis],
    policy_summary: list[dict], ctx: AgentContext,
) -> dict[str, Any]:
    plan_json = json.dumps(
        [
            {"tool": a.tool, "target": a.target, "params": a.params, "reason": a.reason}
            for a in plan.actions
        ],
        indent=2,
    )
    payload = _json_call(
        ctx.llm, CRITIC_SYSTEM,
        f"Incident: {alert.title} on {alert.service}\n"
        f"Hypotheses:\n" + "\n".join(f"- {h.cause} ({h.confidence:.2f})" for h in hypotheses)
        + f"\n\nProposed plan:\n{plan_json}"
        f"\n\nRationale: {plan.rationale}"
        f"\n\nPolicy evaluation:\n{json.dumps(policy_summary, indent=2)}",
        default=None, effort="high",
    )
    if payload is None:
        # No model: fall back to policy alone. Policy already blocked anything
        # disallowed, so "pass" here means "deterministic checks are satisfied".
        blocked = [d for d in policy_summary if not d["allowed"]]
        return {
            "verdict": "reject" if blocked else "pass",
            "score": 0.0 if blocked else 7.0,
            "issues": [{"severity": "high", "what": d["reason"], "fix": "revise the plan"} for d in blocked],
            "summary": "policy-only review (model unavailable)",
        }
    return {
        "verdict": str(payload.get("verdict", "revise")),
        "score": float(payload.get("score", 0.0)),
        "issues": list(payload.get("issues", [])),
        "summary": str(payload.get("summary", "")),
    }


# --------------------------------------------------------------------------
# 6. Verifier — deterministic, and time-windowed on purpose
# --------------------------------------------------------------------------
def verify(alert: Alert, ctx: AgentContext, settle_seconds: float = 0.0) -> dict[str, Any]:
    """Check the SLO actually recovered.

    Two things this must not do, both of which are real production bugs:
      * check instantly (metrics lag the action — you measure the old world), or
      * check only the signal that alerted (you fixed error rate by scaling and
        set the cluster on fire; latency and saturation must also be sane).
    """
    if settle_seconds:
        time.sleep(settle_seconds)
    m = ctx.cluster.get_metrics(alert.service)
    checks = {
        "error_rate": m["error_rate"] <= max(0.01, m["error_rate_baseline"] * 5),
        "latency_p99": m["latency_p99_ms"] <= m["latency_p99_baseline_ms"] * 3,
        "cpu_saturation": m["cpu_saturation"] < 0.9,
        "memory_saturation": m["memory_saturation"] < 0.9,
    }
    failed = [k for k, ok in checks.items() if not ok]
    return {
        "recovered": not failed,
        "checks": checks,
        "failed": failed,
        "metrics": m,
        "at": time.time(),
    }


# --------------------------------------------------------------------------
# 7. Scribe — the memory write path
# --------------------------------------------------------------------------
def write_postmortem(
    incident_id: str, alert: Alert, timeline: list[str], outcome: str, ctx: AgentContext
) -> dict[str, Any]:
    """Reflective write: episode -> episodic (always), semantic (filtered),
    procedural (proposed, never auto-activated).

    The asymmetry is the point. Episodic memory is a log and can accept anything.
    Semantic memory shapes future diagnoses, so it takes a confidence floor.
    Procedural memory shapes future *actions*, so it takes a human.
    """
    episode = f"Incident {incident_id} on {alert.service}: {alert.title}\n" + "\n".join(timeline)
    ctx.episodic.log(
        f"{incident_id} [{outcome}] {alert.service}/{alert.signal}: {alert.title}",
        author="scribe", source=incident_id,
        tags=[alert.service, alert.signal, outcome], incident_id=incident_id,
    )

    payload = _json_call(ctx.llm, SCRIBE_SYSTEM, episode, default=None, effort="medium")
    if payload is None:
        return {"summary": f"{incident_id} ended: {outcome}", "facts_written": 0, "runbook_proposed": None}

    written = 0
    for fact in payload.get("facts", []):
        confidence = float(fact.get("confidence", 0.0))
        content = str(fact.get("content", "")).strip()
        if not content or confidence < 0.6:
            continue  # write-around: speculation does not enter the knowledge base
        ctx.semantic.upsert(MemoryRecord(
            content=content, tier=Tier.SEMANTIC, scope=Scope.GLOBAL,
            key=str(fact.get("key") or "") or None,
            tags=[alert.service, alert.signal],
            provenance=Provenance("scribe", incident_id, confidence),
        ))
        written += 1

    proposed = None
    runbook = payload.get("runbook")
    if runbook and outcome == "resolved":
        # Only successful remediations may propose a runbook, and even then it
        # lands as a candidate. A runbook derived from a failed remediation is
        # a codified mistake.
        name = str(runbook.get("name", "")).strip()
        if name:
            steps = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(runbook.get("steps", [])))
            rec = ctx.procedural.propose(
                name, f"When: {runbook.get('when', '')}\n{steps}",
                author="scribe", rationale=f"derived from {incident_id}",
            )
            proposed = {"name": name, "id": rec.id, "status": "awaiting human approval"}

    return {
        "summary": str(payload.get("summary", "")),
        "facts_written": written,
        "runbook_proposed": proposed,
    }
