"""Wiring: the incident auto-remediation graph.

    ingest -> triage -> [suppressed | escalate | diagnose]
                             |
              diagnose (fan-out, parallel)
              +-- metrics_analyst
              +-- logs_analyst
              +-- deploy_analyst
                             |
                        synthesize            <- MoA-style aggregation
                             |
                          plan                <- constrained to the action catalog
                             |
                     safety_review            <- critic; loops back to plan (bounded)
                             |
                     approval_gate            <- interrupt if policy demands a human
                             |
                        execute               <- locks + idempotency + captured inverses
                             |
                         verify
                        /    |    \\
                 resolved  replan  rollback
                             |
                        postmortem            <- memory write path

Control-flow rules that make it safe rather than merely clever:
  * Every loop is bounded (`plan_revisions`, `attempts`) and every bound has a
    terminal branch. There is no path that can spin.
  * The approval gate interrupts *before* the node runs, so the checkpoint is a
    pre-action state that a human can inspect and patch.
  * Verification failure is a first-class outcome with three exits: try again,
    give up and roll back, or escalate. "Assume it worked" is not one of them.
"""

from __future__ import annotations

import time
from dataclasses import replace

from magents.coordination import DeadlockError, LockTimeout, ResourceManager
from magents.graph import END, InMemoryCheckpointer, StateGraph, add, append, unique_append
from magents.observability import Tracer

from . import agents, policy
from .agents import AgentContext
from .state import (
    Action,
    ApprovalRequest,
    ExecutionRecord,
    Finding,
    Hypothesis,
    Phase,
    Plan,
    Severity,
)
from .tools import inverse_of, write_tools

MAX_PLAN_REVISIONS = 2
MAX_REMEDIATION_ATTEMPTS = 2

REDUCERS = {
    # Analysts run in parallel and each returns findings; without an append
    # reducer the last one to merge would silently erase the other two. This one
    # line is the difference between a working fan-out and a mystifying bug.
    "findings": unique_append(key=lambda f: f.dedupe_key),
    "hypotheses": unique_append(key=lambda h: h.dedupe_key),
    "executions": append,
    "critiques": append,
    "events": append,
    "notes": append,
    "attempts": add,
    "plan_revisions": add,
    "cost_usd": add,
}


def build(
    ctx: AgentContext,
    guardrails: policy.Guardrails | None = None,
    locks: ResourceManager | None = None,
    tracer: Tracer | None = None,
    active_fingerprints: set[str] | None = None,
    approval_deadline_seconds: float = 900.0,
    settle_seconds: float = 0.0,
):
    guardrails = guardrails or policy.Guardrails()
    locks = locks or ResourceManager(ordered=True, default_timeout=5.0)
    tracer = tracer or Tracer()
    active_fingerprints = active_fingerprints if active_fingerprints is not None else set()
    executed_keys: set[str] = set()  # idempotency ledger, survives retries in-process

    def event(state: dict, text: str) -> str:
        return f"[{time.strftime('%H:%M:%S')}] {text}"

    # -- nodes -------------------------------------------------------------
    def ingest(state: dict) -> dict:
        alert = state["alert"]
        return {
            "phase": Phase.INGESTED.value,
            "events": event(state, f"ingested {alert.title} on {alert.service}"),
        }

    def triage_node(state: dict) -> dict:
        alert = state["alert"]
        with tracer.span("triage", service=alert.service) as span:
            result = agents.triage(alert, ctx, active_fingerprints)
            span.attributes["severity"] = result["severity"].value
        if result["suppress"]:
            return {
                "phase": Phase.SUPPRESSED.value,
                "severity": result["severity"],
                "events": event(state, f"suppressed: {result['reasoning']}"),
            }
        active_fingerprints.add(alert.fingerprint)
        tracer.decision("triage", result["severity"].value, result["reasoning"])
        return {
            "phase": Phase.TRIAGED.value,
            "severity": result["severity"],
            "notes": f"triage: {result['reasoning']} (impact: {result['customer_impact']})",
            "events": event(state, f"triaged {result['severity'].value}"),
        }

    def make_analyst(name: str):
        def _node(state: dict) -> dict:
            with tracer.span(f"analyst:{name}"):
                findings: list[Finding] = agents.ANALYSTS[name](state["alert"], ctx)
            return {"findings": findings, "events": event(state, f"{name}: {len(findings)} finding(s)")}

        return _node

    def synthesize_node(state: dict) -> dict:
        with tracer.span("synthesize") as span:
            hypotheses: list[Hypothesis] = agents.synthesize(state["alert"], state["findings"], ctx)
            span.attributes["n_hypotheses"] = len(hypotheses)
        top = hypotheses[0] if hypotheses else None
        if top:
            tracer.decision("root_cause", top.cause, f"confidence {top.confidence:.2f}")
        return {
            "phase": Phase.DIAGNOSED.value,
            "hypotheses": hypotheses,
            "events": event(
                state,
                f"top hypothesis: {top.cause} ({top.confidence:.2f})" if top else "no hypothesis",
            ),
        }

    def plan_node(state: dict) -> dict:
        last_critique = state["critiques"][-1] if state["critiques"] else None
        prior = ""
        if last_critique and last_critique.get("verdict") != "pass":
            prior = last_critique.get("summary", "") + "\n" + "\n".join(
                f"- {i.get('what')}: {i.get('fix')}" for i in last_critique.get("issues", [])
            )
        with tracer.span("plan", revision=state["plan_revisions"]):
            plan = agents.plan_remediation(
                state["alert"], state["hypotheses"], ctx,
                prior_critique=prior, revision=state["plan_revisions"],
            )
        return {
            "phase": Phase.PLANNED.value,
            "plan": plan,
            "events": event(
                state,
                f"plan r{plan.revision}: " + ", ".join(a.tool for a in plan.actions) or "plan: (empty)",
            ),
        }

    def safety_review(state: dict) -> dict:
        plan: Plan = state["plan"]
        severity: Severity = state["severity"] or Severity.SEV3
        confidence = state["hypotheses"][0].confidence if state["hypotheses"] else 0.0

        # Deterministic policy first — it is authoritative, and it gives the LLM
        # critic real information instead of asking it to guess the rules.
        per_action, verdict = policy.evaluate_plan(
            plan.actions, severity=severity, confidence=confidence,
            guardrails=guardrails, attempts=state["attempts"],
        )
        summary = policy.summarize(per_action)
        with tracer.span("safety_review", allowed=verdict.allowed):
            critique = agents.critique_plan(state["alert"], plan, state["hypotheses"], summary, ctx)

        # Policy can only ever be more restrictive than the critic, never less.
        if not verdict.allowed:
            critique["verdict"] = "reject"
        approval = None
        if verdict.allowed and verdict.requires_approval:
            approval = ApprovalRequest(
                reason=verdict.reason,
                risk=verdict.risk,
                blast_radius=verdict.blast_radius,
                actions=[f"{a.tool}({a.params})" for a in plan.actions],
                deadline_seconds=approval_deadline_seconds,
            )
        tracer.decision(
            "safety", critique["verdict"],
            f"policy: {verdict.reason}", blast_radius=verdict.blast_radius,
        )
        return {
            "critiques": critique,
            "policy": summary,
            "approval": approval,
            "events": event(
                state,
                f"safety: {critique['verdict']} (score {critique.get('score', 0):.1f}); "
                f"policy: {verdict.reason}",
            ),
        }

    def approval_gate(state: dict) -> dict:
        """Runs only once a decision exists.

        The pause itself is the graph's `interrupt_before` on this node — see
        `route_after_review`. This node handles what happens *after* a human
        answers, or after the deadline passes with no answer.
        """
        request: ApprovalRequest | None = state.get("approval")
        decision = state.get("approval_decision")
        if request is None:
            return {"events": event(state, "no approval required")}

        if decision is None:
            # No decision and we are past the interrupt: nobody answered. See
            # magents.coordination.Deadline for the general shape — the default
            # here is `escalate`, never `proceed`. Defaulting a production write
            # to "proceed" because nobody replied would be the single most
            # dangerous line in the system.
            waited = time.time() - request.requested_at
            return {
                "phase": Phase.ESCALATED.value,
                "approval_decision": "expired",
                "events": event(
                    state,
                    f"no approval after {waited:.0f}s (deadline "
                    f"{request.deadline_seconds:.0f}s) -> {request.on_expiry}",
                ),
            }
        return {
            "phase": Phase.EXECUTING.value if decision == "granted" else Phase.ESCALATED.value,
            "events": event(state, f"approval {decision} by {state.get('approver', 'unknown')}"),
        }

    def execute(state: dict) -> dict:
        plan: Plan = state["plan"]
        alert = state["alert"]
        records: list[ExecutionRecord] = []
        rollback: list[Action] = []
        tools = write_tools(ctx.cluster)

        # One lock per affected service, acquired in canonical order as a set.
        # Prevents two concurrent incidents from both remediating checkout-api,
        # and prevents deadlock between them (sorted acquisition => no cycle).
        resources = sorted({f"service:{a.params.get('service', alert.service)}" for a in plan.actions})
        agent_id = state["incident_id"]

        try:
            with locks.hold_all(agent_id, resources, timeout=5.0):
                for action in plan.actions:
                    if action.idempotency_key in executed_keys:
                        records.append(ExecutionRecord(
                            action.id, action.tool, action.target, "skipped",
                            "already applied (idempotency key seen)",
                        ))
                        continue
                    tool = tools.get(action.tool)
                    if tool is None:
                        records.append(ExecutionRecord(
                            action.id, action.tool, action.target, "blocked",
                            "tool not in the write catalog",
                        ))
                        continue

                    inverse = inverse_of(action, ctx.cluster)  # capture BEFORE mutating
                    started = time.perf_counter()
                    try:
                        with tracer.span(f"execute:{action.tool}", target=action.target):
                            result = tool(**action.params)
                        executed_keys.add(action.idempotency_key)
                        if inverse:
                            rollback.append(inverse)
                        records.append(ExecutionRecord(
                            action.id, action.tool, action.target, "succeeded",
                            str(result), (time.perf_counter() - started) * 1000,
                        ))
                    except Exception as exc:  # noqa: BLE001
                        records.append(ExecutionRecord(
                            action.id, action.tool, action.target, "failed", f"{type(exc).__name__}: {exc}",
                            (time.perf_counter() - started) * 1000,
                        ))
                        break  # stop the plan; do not compound a failure
        except (LockTimeout, DeadlockError) as exc:
            return {
                "phase": Phase.ESCALATED.value,
                "executions": [ExecutionRecord("-", "lock", ",".join(resources), "blocked", str(exc))],
                "events": event(state, f"could not acquire locks: {exc}"),
            }

        return {
            "phase": Phase.VERIFYING.value,
            "executions": records,
            "plan": replace(plan, rollback=rollback),
            "attempts": 1,
            "events": event(
                state, "executed: " + ", ".join(f"{r.tool}={r.status}" for r in records)
            ),
        }

    def verify_node(state: dict) -> dict:
        with tracer.span("verify"):
            result = agents.verify(state["alert"], ctx, settle_seconds=settle_seconds)
        tracer.decision(
            "verify", "recovered" if result["recovered"] else "not recovered",
            ", ".join(result["failed"]) or "all checks pass",
        )
        return {
            "verification": result,
            "events": event(
                state,
                "verified: recovered" if result["recovered"]
                else f"verification failed on {', '.join(result['failed'])}",
            ),
        }

    def rollback_node(state: dict) -> dict:
        plan: Plan = state["plan"]
        tools = write_tools(ctx.cluster)
        records: list[ExecutionRecord] = []
        # Reverse order: undo the last change first, same as a transaction log.
        for action in reversed(plan.rollback):
            tool = tools.get(action.tool)
            if tool is None:
                continue
            try:
                result = tool(**action.params)
                records.append(ExecutionRecord(action.id, action.tool, action.target, "succeeded", str(result)))
            except Exception as exc:  # noqa: BLE001
                records.append(ExecutionRecord(action.id, action.tool, action.target, "failed", str(exc)))
        tools["page_oncall"](
            service=state["alert"].service,
            message=f"{state['incident_id']}: auto-remediation failed and was rolled back",
            severity=(state["severity"] or Severity.SEV2).value,
        )
        return {
            "phase": Phase.ROLLED_BACK.value,
            "executions": records,
            "events": event(state, f"rolled back {len(records)} action(s); on-call paged"),
        }

    def escalate(state: dict) -> dict:
        write_tools(ctx.cluster)["page_oncall"](
            service=state["alert"].service,
            message=f"{state['incident_id']}: {state['alert'].title} — automation standing down",
            severity=(state["severity"] or Severity.SEV2).value,
        )
        return {
            "phase": Phase.ESCALATED.value,
            "events": event(state, "escalated to on-call"),
        }

    def resolve(state: dict) -> dict:
        active_fingerprints.discard(state["alert"].fingerprint)
        return {"phase": Phase.RESOLVED.value, "events": event(state, "resolved")}

    def postmortem(state: dict) -> dict:
        outcome = state["phase"]
        with tracer.span("postmortem"):
            result = agents.write_postmortem(
                state["incident_id"], state["alert"], state["events"], outcome, ctx
            )
        active_fingerprints.discard(state["alert"].fingerprint)
        return {
            "notes": f"postmortem: {result['summary']}",
            "events": event(
                state,
                f"postmortem written: {result['facts_written']} fact(s), "
                f"runbook={'proposed' if result['runbook_proposed'] else 'none'}",
            ),
            "cost_usd": tracer.total_cost_usd,
        }

    # -- routers -----------------------------------------------------------
    def route_after_triage(state: dict) -> list[str] | str:
        if state["phase"] == Phase.SUPPRESSED.value:
            return "postmortem"
        if (state["severity"] or Severity.SEV3) is Severity.SEV1:
            # Diagnose anyway — the analysis is useful to the human — but never
            # let a SEV1 walk into automated action.
            return ["metrics_analyst", "logs_analyst", "deploy_analyst"]
        return ["metrics_analyst", "logs_analyst", "deploy_analyst"]

    def route_after_review(state: dict) -> str:
        critique = state["critiques"][-1]
        if critique["verdict"] == "reject":
            return "escalate"
        if critique["verdict"] == "revise" and state["plan_revisions"] < MAX_PLAN_REVISIONS:
            return "replan"
        if not state["plan"].actions:
            return "escalate"
        return "approval_gate" if state.get("approval") else "execute"

    def route_after_gate(state: dict) -> str:
        if state.get("approval_decision") == "granted":
            return "execute"
        return "escalate"

    def route_after_verify(state: dict) -> str:
        if state["verification"]["recovered"]:
            return "resolve"
        if state["attempts"] < MAX_REMEDIATION_ATTEMPTS:
            return "replan"  # different hypothesis, bounded retries
        if state["plan"].rollback:
            return "rollback"
        return "escalate"

    def bump_revision(state: dict) -> dict:
        return {
            "plan_revisions": 1,
            "events": event(state, f"replanning (revision {state['plan_revisions'] + 1})"),
        }

    # -- assembly ----------------------------------------------------------
    g = StateGraph(reducers=REDUCERS)
    g.add_node("ingest", ingest)
    g.add_node("triage", triage_node)
    for name in agents.ANALYSTS:
        g.add_node(name, make_analyst(name))
    g.add_node("synthesize", synthesize_node)
    g.add_node("plan", plan_node)
    g.add_node("safety_review", safety_review)
    g.add_node("replan", bump_revision)
    g.add_node("approval_gate", approval_gate)
    g.add_node("execute", execute)
    g.add_node("verify", verify_node)
    g.add_node("rollback", rollback_node)
    g.add_node("escalate", escalate)
    g.add_node("resolve", resolve)
    g.add_node("postmortem", postmortem)

    g.set_entry_point("ingest")
    g.add_edge("ingest", "triage")
    g.add_conditional_edges(
        "triage", route_after_triage, targets=[*agents.ANALYSTS, "postmortem"]
    )
    for name in agents.ANALYSTS:
        g.add_edge(name, "synthesize")
    g.add_edge("synthesize", "plan")
    g.add_edge("plan", "safety_review")
    g.add_conditional_edges(
        "safety_review", route_after_review,
        targets=["replan", "escalate", "approval_gate", "execute"],
    )
    g.add_edge("replan", "plan")
    g.add_conditional_edges("approval_gate", route_after_gate, targets=["execute", "escalate"])
    g.add_edge("execute", "verify")
    g.add_conditional_edges(
        "verify", route_after_verify, targets=["resolve", "replan", "rollback", "escalate"]
    )
    g.add_edge("rollback", "postmortem")
    g.add_edge("escalate", "postmortem")
    g.add_edge("resolve", "postmortem")
    g.add_edge("postmortem", END)

    return g, tracer


def compile_graph(
    ctx: AgentContext,
    guardrails: policy.Guardrails | None = None,
    checkpointer=None,
    require_approval_pause: bool = True,
    **kwargs,
):
    """Compile with the approval gate wired as a real interrupt.

    `interrupt_before=["approval_gate"]` is what turns "the agent asks for
    permission" into a durable pause: state is checkpointed, the process can
    exit, and `resume(thread_id, {"approval_decision": "granted"})` picks it up
    — possibly hours later, possibly in a different process.
    """
    graph, tracer = build(ctx, guardrails=guardrails, **kwargs)
    compiled = graph.compile(
        checkpointer=checkpointer or InMemoryCheckpointer(),
        interrupt_before=["approval_gate"] if require_approval_pause else [],
        recursion_limit=40,
    )
    return compiled, tracer
