"""Prototype 2 — the three collaboration patterns, on one shared task.

    python demos/demo2_patterns.py

Same task, three topologies, so the differences are about structure rather than
about the model:

  A. Orchestrator + specialists — decompose, fan out, synthesize.
  B. Critic + refiner           — one artifact, iterated against a rubric.
  C. Mixture of agents          — N answers to the SAME question, aggregated.
  D. All three composed         — which is what a real system looks like.

Runs on a ScriptedLLM so the output is deterministic. Set ANTHROPIC_API_KEY and
pass --live to run the identical code against Claude Opus 5.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from magents.llm import AnthropicLLM, ScriptedLLM  # noqa: E402
from magents.patterns import (  # noqa: E402
    CriticRefiner,
    MixtureOfAgents,
    Orchestrator,
    Proposer,
    Specialist,
    json_verifier,
    predicate_verifier,
)

RULE = "=" * 74
TASK = (
    "checkout-api error rate jumped from 0.1% to 24% at 14:02. A deploy shipped "
    "at 13:55. Produce a remediation recommendation."
)


def header(text: str) -> None:
    print(f"\n{RULE}\n{text}\n{RULE}")


def scripted() -> ScriptedLLM:
    return ScriptedLLM(
        routes=[
            # -- orchestrator planner
            (
                lambda s, u: s.startswith("You decompose"),
                """{"subtasks": [
                  {"id": "s1", "specialist": "metrics", "wave": 0,
                   "task": "Quantify the error rate change for checkout-api since 13:50.",
                   "expects": "one paragraph with numbers"},
                  {"id": "s2", "specialist": "logs", "wave": 0,
                   "task": "Identify the dominant error signature in checkout-api logs since 14:00.",
                   "expects": "the signature and its count"},
                  {"id": "s3", "specialist": "deploys", "wave": 0,
                   "task": "List checkout-api deploys in the last 2 hours with timestamps.",
                   "expects": "version, time, author"}]}""",
            ),
            (lambda s, u: s.startswith("You are the orchestrator"),
             "Roll back checkout-api to v1.0.0. The 13:55 deploy correlates with onset at 14:02, "
             "the logs show a NullPointerException in the new pricing path, and error rate is 240x "
             "baseline. Rollback is reversible and addresses the stated cause."),
            # -- specialists
            (lambda s, u: "metrics analyst" in s,
             "error_rate went 0.001 -> 0.24 (240x) at 14:02; p99 1400ms against a 180ms baseline."),
            (lambda s, u: "logs analyst" in s,
             "Dominant signature: NullPointerException at PricingEngine.applyDiscount, 36 occurrences "
             "since 14:00, zero before."),
            (lambda s, u: "deploy analyst" in s,
             "v1.1.0 deployed 13:55 by a.dev ('new pricing engine'). Previous v1.0.0 stable for 2h."),
            # -- critic / refiner
            (lambda s, u: s.startswith("You review a draft") and "restart_pods" in u,
             '{"score": 3.0, "verdict": "revise", "issues": [{"severity": "high", '
             '"what": "restart_pods does not address a code defect; the null pointer returns '
             'immediately after restart", "fix": "roll back to v1.0.0 instead"}, '
             '{"severity": "medium", "what": "no verification step", '
             '"fix": "state which metric must recover and by when"}], '
             '"summary": "wrong remediation for the stated cause"}'),
            (lambda s, u: s.startswith("You review a draft"),
             '{"score": 9.0, "verdict": "pass", "issues": [], '
             '"summary": "action follows from the cause, reversible, has a verification step"}'),
            (lambda s, u: s.startswith("Revise the draft"),
             '{"action": "rollback_deploy", "target": "checkout-api", "to_version": "v1.0.0", '
             '"verify": "error_rate below 0.01 within 5 minutes"}'),
            # -- MoA proposers
            (lambda s, u: "cautious SRE" in s,
             "Roll back to v1.0.0. Confidence: high. The temporal correlation is tight (7 min) and "
             "the log signature names the new code path. Roll back rather than fix forward."),
            (lambda s, u: "capacity engineer" in s,
             "Check saturation first. Roll back to v1.0.0. Confidence: high. Nothing here suggests a "
             "load problem — CPU and memory are nominal, so this is not a scaling event."),
            (lambda s, u: "application developer" in s,
             "Roll back to v1.0.0, then fix the null discount code path in v1.1.1. Confidence: high. "
             "The NullPointerException is in applyDiscount and will recur on redeploy without a fix."),
            (lambda s, u: s.startswith("You synthesize"),
             "Roll back checkout-api to v1.0.0 now, then ship v1.1.1 with a null-check in "
             "applyDiscount.\n\nAgreement: all three proposers independently chose rollback, on "
             "different evidence (timing, absence of a load signal, the exception itself). The "
             "follow-up fix is single-sourced from the application developer, and is right."),
        ],
        fallback="(no scripted response)",
    )


# ---------------------------------------------------------------------------
def part_a(llm) -> None:
    header("A. Orchestrator + specialists — decompose, fan out, synthesize")

    orchestrator = Orchestrator(
        llm,
        [
            Specialist("metrics", "Quantifies metric changes over a time window. Read-only.",
                       "You are a metrics analyst. Answer with numbers.", effort="low"),
            Specialist("logs", "Finds dominant error signatures in logs. Read-only.",
                       "You are a logs analyst. Report the signature and its count.", effort="low"),
            Specialist("deploys", "Lists recent deploys and change metadata. Read-only.",
                       "You are a deploy analyst. Report version, time, author.", effort="low"),
        ],
    )
    result = orchestrator.run(TASK)

    print("  plan:")
    for st in result.plan:
        print(f"    wave {st.wave}  {st.specialist:8} <- {st.task[:52]}...")
    print("\n  specialist reports (each ran in an isolated context):")
    for r in result.results:
        print(f"    [{r.specialist:8}] {r.output[:64]}...")
    print(f"\n  synthesis:\n    {result.answer[:210]}...")
    print(f"\n  failures: {len(result.failures)} (one specialist failing degrades, "
          f"it does not abort)")
    print("\n  Note the three specialists answered DIFFERENT questions. That is")
    print("  decomposition, and its purpose is coverage plus context isolation.")


# ---------------------------------------------------------------------------
def part_b(llm) -> None:
    header("B. Critic + refiner — one artifact, iterated against a rubric")

    refiner = CriticRefiner(
        llm,
        rubric=(
            "1. The action must address the stated root cause, not a symptom.\n"
            "2. Prefer reversible actions.\n"
            "3. Must state a verification condition.\n"
            "4. Must be valid JSON with keys: action, target, verify."
        ),
        max_iterations=3,
        pass_score=8.0,
        verifiers=[
            # Deterministic gates run before the model critic. If the JSON is
            # malformed there is nothing for an LLM to have an opinion about.
            json_verifier(required_keys=("action", "target")),
            predicate_verifier(
                "action is in the catalog",
                lambda d: any(t in d for t in ("rollback_deploy", "scale", "restart_pods",
                                               "set_feature_flag", "resize_connection_pool")),
            ),
        ],
    )

    bad_first_draft = '{"action": "restart_pods", "target": "checkout-api", "count": 3}'
    result = refiner.run(TASK, draft=bad_first_draft)

    for rnd in result.rounds:
        print(f"  round {rnd.iteration}: score {rnd.critique.score:.1f} "
              f"[{rnd.critique.verdict}] {rnd.critique.summary}")
        for issue in rnd.critique.issues:
            print(f"           - [{issue['severity']}] {issue['what'][:60]}")
    print(f"\n  trajectory : {result.trajectory()}")
    print(f"  stopped    : {result.stopped_because}")
    print(f"  best draft : {result.output}")
    print("\n  It returns the BEST draft, not the last. Refinement is not monotone —")
    print("  v3 routinely fixes A while reintroducing B.")

    print("\n  Deterministic verifier catching malformed output before any model call:")
    broken = refiner.critique(TASK, "here is my plan: restart everything")
    print(f"    score {broken.score:.1f}, {broken.summary}")


# ---------------------------------------------------------------------------
def part_c(llm) -> None:
    header("C. Mixture of agents — N answers to the SAME question")

    moa = MixtureOfAgents(
        llm,
        [
            Proposer("cautious_sre", "You are a cautious SRE. Prefer the lowest-risk action.",
                     effort="medium"),
            Proposer("capacity", "You are a capacity engineer. Think about load and saturation first.",
                     effort="medium"),
            Proposer("app_dev", "You are the application developer who owns this service.",
                     effort="medium"),
        ],
        layers=1,
    )
    result = moa.run(TASK)

    for proposal in result.proposals:
        print(f"    [{proposal.proposer:12}] {proposal.text[:60]}...")
    print(f"\n  pairwise agreement: {result.agreement:.2f}")
    print(f"\n  aggregated:\n{chr(10).join('    ' + l for l in result.answer.splitlines())}")

    # The cheap aggregator: no model call at all when the answer space is discrete.
    def extract_action(text: str) -> str:
        match = re.search(r"\b(roll ?back|scale|restart|failover)\b", text.lower())
        return match.group(1).replace(" ", "") if match else ""

    winner, distribution = moa.vote(TASK, extract_action)
    print(f"\n  weighted vote (no aggregator model call): {winner!r} {distribution}")
    print("\n  Use the vote when the answer space is discrete — it is free,")
    print("  deterministic, and auditable. Use the LLM aggregator when the")
    print("  proposals need to be *combined* rather than picked between.")

    print("\n  Diversity alarm: agreement near 1.0 means the proposers are not")
    print("  independent, so MoA is buying latency and nothing else. Correlated")
    print("  error is the failure mode this pattern is most exposed to.")


# ---------------------------------------------------------------------------
def part_d(llm) -> None:
    header("D. Composed — how they actually fit together")
    print("""
  A production incident agent is not one pattern, it is all three at once:

      orchestrator            decomposes the incident
        |-- metrics analyst   \\
        |-- logs analyst       }  specialists: different questions, parallel
        |-- deploy analyst    /
              |
      MoA aggregation         several analysts, one root-cause question
              |
      planner                 produces a remediation plan
              |
      critic + refiner        bounded loop against policy + a rubric
              |
      deterministic policy    the actual decision to execute

  See incident/graph.py for exactly this, wired as a state graph with
  checkpointing, an approval interrupt, locks, and verification.

  The judgement call is where each belongs:
    - orchestrator  : when sub-tasks are independent and context-heavy
    - MoA           : when a single wrong answer is expensive and errors are
                      plausibly independent
    - critic/refiner: when quality is checkable and the loop can be bounded
    - none of them  : when one agent with four tools would do. Most of the time.
""")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="use Claude Opus 5")
    args = parser.parse_args()
    llm = AnthropicLLM() if args.live else scripted()

    part_a(llm)
    part_b(llm)
    part_c(llm)
    part_d(llm)
    print(f"\n{RULE}\ndone\n{RULE}")


if __name__ == "__main__":
    main()
