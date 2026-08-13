"""Runnable end-to-end scenarios.

    python -m incident.cli                      # all scenarios, offline
    python -m incident.cli bad_deploy           # one scenario
    python -m incident.cli --live bad_deploy    # use Claude Opus 5 (needs credentials)
    python -m incident.cli --graph              # print the topology as mermaid

Offline runs use a ScriptedLLM, so output is deterministic and there is no cost
or network dependency. `--live` swaps in Claude Opus 5 for triage, synthesis,
planning, critique, and the postmortem; everything else is unchanged, which is
the point of the model seam.
"""

from __future__ import annotations

import argparse
import sys
import textwrap

from magents.coordination import ResourceManager
from magents.graph import InMemoryCheckpointer, Interrupt
from magents.llm import AnthropicLLM, ScriptedLLM
from magents.memory import EpisodicStore, ProceduralStore, SemanticStore
from magents.observability import Tracer

from . import policy
from .agents import AgentContext
from .graph import build, compile_graph
from .state import Alert, new_state
from .tools import SimulatedCluster

SCENARIOS = {
    "bad_deploy": (
        "A deploy 7 minutes ago introduced a null-pointer in the pricing path. "
        "Correct remediation: roll back \u2014 but rollback is HIGH risk and needs "
        "confidence >= 0.85, and we only have 0.82. Expect: pause for approval, "
        "resume, resolve.",
        "bad_deploy",
        Alert(
            id="A-1001", service="checkout-api",
            title="checkout-api error rate above SLO",
            description="5xx rate breached the 1% error budget burn threshold",
            signal="error_rate", value=0.24, threshold=0.01,
        ),
    ),
    "resource_exhaustion": (
        "Memory leak, pods OOMKilled under load. Correct remediation: scale out "
        "(MEDIUM risk, confidence clears the bar, blast radius under the auto "
        "threshold). Expect: fully autonomous remediation, no human.",
        "resource_exhaustion",
        Alert(
            id="A-1002", service="checkout-api",
            title="checkout-api p99 latency degraded",
            description="p99 latency 2600ms against a 500ms objective",
            signal="latency_p99", value=2600, threshold=500,
        ),
    ),
    "connection_pool": (
        "Connection pool exhausted. Correct remediation: resize the pool. "
        "Expect: fully autonomous remediation.",
        "connection_pool_exhaustion",
        Alert(
            id="A-1003", service="checkout-api",
            title="checkout-api connection timeouts",
            description="requests failing to acquire a database connection",
            signal="error_rate", value=0.18, threshold=0.01,
        ),
    ),
    "downstream": (
        "THE TRAP. Symptoms are in checkout-api; the cause is a saturated "
        "ledger-db-proxy. Acting on checkout-api would be wrong. Expect: the "
        "system pages a human rather than acting, verification confirms nothing "
        "recovered, the bounded retry is exhausted, and it escalates.",
        "downstream_dependency",
        Alert(
            id="A-1004", service="checkout-api",
            title="checkout-api elevated errors and latency",
            description="upstream timeouts calling ledger-db-proxy",
            signal="error_rate", value=0.15, threshold=0.01,
        ),
    ),
    "change_freeze": (
        "Identical to resource_exhaustion, but a change freeze is active. Same "
        "incident, same plan, different policy \u2014 now it needs a human. This is "
        "the pair that shows policy, not the model, decides.",
        "resource_exhaustion",
        Alert(
            id="A-1005", service="checkout-api",
            title="checkout-api p99 latency degraded (change freeze active)",
            description="p99 latency 2600ms against a 500ms objective",
            signal="latency_p99", value=2600, threshold=500,
        ),
    ),
}


def offline_llm() -> ScriptedLLM:
    """Canned responses keyed on the system prompt.

    Note what this does *not* do: it does not stub out the analysts, the policy
    engine, the locks, the executor, or verification. Only the four genuinely
    model-shaped judgments are scripted, so the scenario still exercises the
    real control flow — including the failure path, where the scripted planner
    proposes the wrong action and verification catches it.
    """
    return ScriptedLLM(
        routes=[
            (lambda s, u: s.startswith("You triage"), ""),  # empty -> heuristic fallback
            (lambda s, u: s.startswith("You rank"), ""),
            (lambda s, u: s.startswith("You write a remediation"), ""),
            (
                lambda s, u: s.startswith("You are the safety reviewer"),
                '{"verdict": "pass", "score": 8.5, "issues": [], '
                '"summary": "action follows from the evidence and is reversible"}',
            ),
            (
                lambda s, u: s.startswith("Write a postmortem"),
                '{"summary": "Automated remediation completed.", '
                '"facts": [{"key": "checkout-api:remediation", '
                '"content": "checkout-api incidents are usually resolved by acting on the '
                'change correlated with onset", "confidence": 0.7}], "runbook": null}',
            ),
        ],
        fallback="",
    )


def run_scenario(name: str, live: bool = False, verbose: bool = True) -> dict:
    description, fault, alert = SCENARIOS[name]
    cluster = SimulatedCluster()
    cluster.inject_fault(fault)

    llm = AnthropicLLM() if live else offline_llm()
    ctx = AgentContext(
        llm=llm,
        cluster=cluster,
        episodic=EpisodicStore(),
        semantic=SemanticStore(),
        procedural=ProceduralStore(),
        worker_llm=AnthropicLLM(model="claude-haiku-4-5") if live else llm,
    )
    guardrails = policy.Guardrails(change_freeze=(name == "change_freeze"))
    checkpointer = InMemoryCheckpointer()
    graph, tracer = compile_graph(
        ctx,
        guardrails=guardrails,
        checkpointer=checkpointer,
        locks=ResourceManager(ordered=True, default_timeout=2.0),
    )

    if verbose:
        print("=" * 78)
        print(f"SCENARIO: {name}")
        print(textwrap.fill(description, 78, initial_indent="  ", subsequent_indent="  "))
        print("=" * 78)

    state = new_state(alert)
    thread_id = state["incident_id"]

    # A run can pause more than once: if a remediation fails verification, the
    # replanned attempt goes back through the same gate. Loop rather than
    # assuming a single approval.
    final = None
    pending = lambda: graph.invoke(state, thread_id=thread_id)  # noqa: E731
    for _ in range(4):
        try:
            final = pending()
            break
        except Interrupt as pause:
            request = pause.state["approval"]
            if verbose:
                print("\n  >>> PAUSED FOR HUMAN APPROVAL")
                print(f"      reason:       {request.reason}")
                print(f"      risk:         {request.risk.value}")
                print(f"      blast radius: {request.blast_radius:,} rpm")
                for action in request.actions:
                    print(f"      action:       {action}")
                print("      (state is checkpointed; the process could exit here and resume later)")
                print("  <<< approver grants it\n")
            # In production this is a separate process reacting to a Slack button
            # or a PagerDuty response, minutes or hours later.
            pending = lambda: graph.resume(  # noqa: E731
                thread_id,
                {"approval_decision": "granted", "approver": "sre-oncall@example.com"},
            )
    if final is None:
        raise RuntimeError("too many approval pauses")

    if verbose:
        print("TIMELINE")
        for line in final["events"]:
            print(f"  {line}")
        print()
        if final["hypotheses"]:
            print("HYPOTHESES")
            for h in final["hypotheses"]:
                print(f"  {h.confidence:.2f}  {h.cause}")
            print()
        if final["executions"]:
            print("ACTIONS")
            for record in final["executions"]:
                print(f"  [{record.status:9}] {record.tool} on {record.target}  {record.detail[:70]}")
            print()
        verification = final.get("verification")
        if verification:
            print("VERIFICATION")
            for check, ok in verification["checks"].items():
                print(f"  [{'PASS' if ok else 'FAIL'}] {check}")
            print()
        print(f"OUTCOME: {final['phase'].upper()}")
        print(f"cluster audit log: {cluster.audit}")
        print("\nTRACE")
        print(textwrap.indent(tracer.tree(), "  "))
        print()
    return final


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Incident auto-remediation demo")
    parser.add_argument("scenario", nargs="?", choices=sorted(SCENARIOS), help="scenario to run")
    parser.add_argument("--live", action="store_true", help="use Claude Opus 5 instead of scripted responses")
    parser.add_argument("--graph", action="store_true", help="print the graph topology and exit")
    args = parser.parse_args(argv)

    if args.graph:
        cluster = SimulatedCluster()
        ctx = AgentContext(offline_llm(), cluster, EpisodicStore(), SemanticStore(), ProceduralStore())
        graph, _ = build(ctx, tracer=Tracer())
        print(graph.compile(recursion_limit=40).mermaid())
        return 0

    names = [args.scenario] if args.scenario else list(SCENARIOS)
    outcomes = {}
    for name in names:
        outcomes[name] = run_scenario(name, live=args.live)["phase"]

    if len(names) > 1:
        print("=" * 78)
        print("SUMMARY")
        for name, phase in outcomes.items():
            print(f"  {name:22} -> {phase}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
