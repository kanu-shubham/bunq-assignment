"""Prototype 1 — Memory architecture, working-memory budgeting, write strategies.

    python demos/demo1_memory.py

Shows, in order:
  A. the four tiers and what goes in each
  B. a token budget under pressure: reservation, eviction, compaction
  C. rolling compaction of a long transcript, with the tail kept verbatim
  D. structured state as an O(state) alternative to O(turns) prose summaries
  E. write strategies side by side (write-through / write-back / write-around)
  F. retrieval that is not just cosine similarity: recency and confidence too
  G. the blackboard: lost updates, CAS, and scope isolation
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from magents.memory import (  # noqa: E402
    HISTORY,
    RETRIEVED,
    SCRATCH,
    Blackboard,
    BudgetPolicy,
    ConflictError,
    EpisodicStore,
    MemoryRecord,
    ProceduralStore,
    Provenance,
    Scope,
    SemanticStore,
    StructuredState,
    Tier,
    WriteBackBuffer,
    compact_working_memory,
    make_working_memory,
    truncating_summarizer,
)

RULE = "=" * 74


def header(text: str) -> None:
    print(f"\n{RULE}\n{text}\n{RULE}")


# ---------------------------------------------------------------------------
def part_a_tiers() -> tuple[EpisodicStore, SemanticStore, ProceduralStore]:
    header("A. The four tiers")

    episodic = EpisodicStore()
    semantic = SemanticStore()
    procedural = ProceduralStore()

    episodic.log("INC-1001 checkout-api 5xx spike began at 14:02", author="triage",
                 source="alertmanager", tags=["checkout-api", "error_rate"])
    episodic.log("INC-1001 rolled back checkout-api v1.1.0 -> v1.0.0 at 14:09", author="executor",
                 source="INC-1001", tags=["checkout-api", "rollback"])
    episodic.log("INC-1001 error rate back to baseline at 14:11", author="verifier",
                 source="INC-1001", tags=["checkout-api", "resolved"])

    semantic.upsert(MemoryRecord(
        content="checkout-api pricing-v2 code path throws on null discount codes",
        tier=Tier.SEMANTIC, scope=Scope.GLOBAL, key="checkout-api:pricing-v2:defect",
        tags=["checkout-api", "code_defect"],
        provenance=Provenance("scribe", "INC-1001", 0.9),
    ))
    semantic.upsert(MemoryRecord(
        content="checkout-api serves ~12k rpm and has no downstream dependents",
        tier=Tier.SEMANTIC, scope=Scope.GLOBAL, key="checkout-api:traffic",
        tags=["checkout-api", "topology"],
        provenance=Provenance("platform-import", "service-catalog", 1.0),
    ))

    candidate = procedural.propose(
        "rollback-on-deploy-correlated-5xx",
        "When: 5xx spike within 15min of a deploy\n1. Confirm the deploy timestamp\n"
        "2. rollback_deploy to previous version\n3. Verify error rate for 5 minutes",
        author="scribe", rationale="derived from INC-1001",
    )
    print(f"  episodic   : {len(episodic)} events (append-only log)")
    print(f"  semantic   : {len(semantic)} facts (upsert by key)")
    print(f"  procedural : 1 candidate, {0} active  <- needs human promotion")

    procedural.promote("rollback-on-deploy-correlated-5xx", candidate.id, approver="sre-lead")
    print("  ...after human review: 1 active procedure")

    # Upsert, not append: the same fact learned twice does not become two rows.
    semantic.upsert(MemoryRecord(
        content="checkout-api serves ~12k rpm and has no downstream dependents",
        key="checkout-api:traffic", provenance=Provenance("scribe", "INC-1002", 0.9),
    ))
    print(f"  re-learning a known fact  -> semantic still holds {len(semantic)} facts")
    return episodic, semantic, procedural


# ---------------------------------------------------------------------------
def part_b_budget() -> None:
    header("B. Working memory under budget pressure")

    # A deliberately small window so the pressure is visible in one screen.
    policy = BudgetPolicy(total_tokens=4_000, reserve_output=1_000)
    wm = make_working_memory(
        system_prompt="You are an SRE agent. " + "Tool schemas and instructions. " * 20,
        task="Diagnose the checkout-api error spike and propose one remediation.",
        policy=policy,
        summarizer=truncating_summarizer(300),
    )

    for i in range(1, 9):
        # Tool results are the thing that actually floods an agent's context.
        wm.add(
            f"[tool_result {i}] " + f"metrics window {i}: " + "sample=0.24 " * 60,
            segment=HISTORY,
            priority=0.9 if i >= 7 else 0.3,  # recent results matter more
        )
    wm.add("Retrieved runbook: rollback on deploy-correlated 5xx. " * 12, segment=RETRIEVED, priority=0.9)
    wm.add("scratch: considered restart_pods, rejected. " * 30, segment=SCRATCH, priority=0.1)

    print("  before fit():")
    print("\n".join("    " + line for line in wm.usage().pretty().splitlines()))

    report = wm.fit()
    print("\n  after fit():")
    print("\n".join("    " + line for line in report.pretty().splitlines()))
    print(f"    compacted into a summary: {report.summarized} item(s)")

    survived = [i.text[:34].replace("\n", " ") for i in wm.items]
    print("\n  what survived (system + pinned task are never evicted):")
    for text in survived:
        print(f"    - {text}...")


# ---------------------------------------------------------------------------
def part_c_rolling_compaction() -> None:
    header("C. Rolling compaction — compress the head, keep the tail verbatim")

    wm = make_working_memory("system prompt", "task", BudgetPolicy(total_tokens=100_000))
    for i in range(1, 13):
        wm.add(f"turn {i}: agent called get_metrics and observed error_rate=0.2{i}", HISTORY)

    before = wm.usage().total_used
    reclaimed = compact_working_memory(wm, truncating_summarizer(240), keep_tail=3)
    after = wm.usage().total_used
    print(f"  {before} tokens -> {after} tokens (reclaimed {reclaimed})")
    print("  history now holds:")
    for item in [i for i in wm.items if i.segment == HISTORY]:
        print(f"    - {item.text[:66]}...")
    print("\n  The last 3 turns stay verbatim on purpose: the agent has to act on")
    print("  the exact text of the most recent tool result, and 'the API returned")
    print("  an error' is not something you can retry against.")


# ---------------------------------------------------------------------------
def part_d_structured_state() -> None:
    header("D. Structured state — O(state) instead of O(turns)")

    state = StructuredState(goal="Restore checkout-api to its error-rate SLO")
    for turn in range(1, 31):
        # Simulate 30 turns of investigation folding into a fixed-size document.
        if turn == 3:
            state.facts.append("error rate 0.24 (240x baseline), began 14:02")
        elif turn == 7:
            state.facts.append("deploy v1.1.0 shipped 14:55, 7 min before onset")
        elif turn == 11:
            state.attempted.append("restart_pods x2 — no effect")
            state.ruled_out.append("transient pod state")
        elif turn == 19:
            state.facts.append("NullPointerException in PricingEngine.applyDiscount")
        elif turn == 26:
            state.open_questions.append("is the null discount code reachable from the API?")

    print(state.render())
    print(f"\n  rendered size: {state.tokens()} tokens, and it does NOT grow with turn count.")
    print("  A prose summary of 30 turns would be several times this and would")
    print("  silently drop a field on each rewrite. This one is diffable.")


# ---------------------------------------------------------------------------
def part_e_write_strategies(episodic: EpisodicStore) -> None:
    header("E. Write strategies")

    # write-through: every observation hits the store immediately
    through = EpisodicStore()
    t0 = time.perf_counter()
    for i in range(200):
        through.log(f"observation {i}", author="agent", source="tool")
    through_ms = (time.perf_counter() - t0) * 1000

    # write-back: buffer, flush once, dedupe on flush
    buffered = EpisodicStore()
    buffer = WriteBackBuffer(buffered, author="agent")
    t0 = time.perf_counter()
    for i in range(200):
        buffer.record(f"observation {i}", source="tool")
    buffer.record("observation 0", source="tool")  # a retried turn re-records
    written = buffer.flush()
    back_ms = (time.perf_counter() - t0) * 1000

    print(f"  write-through : {len(through):3} records, {through_ms:.2f}ms, durable at every step")
    print(f"  write-back    : {written:3} records, {back_ms:.2f}ms, one flush, duplicate dropped")
    print(f"                  (recorded 201 items, wrote {written} — idempotent on retry)")

    # write-around: a salience filter before persisting
    salient = [
        e for e in ["cpu at 41%", "OOMKilled: container restarted", "request id 8f2a",
                    "deploy v1.1.0 at 14:55"]
        if any(k in e for k in ("OOMKilled", "deploy", "error", "failed"))
    ]
    print(f"  write-around  : 4 observations in, {len(salient)} persisted -> {salient}")
    print("\n  Rule of thumb: write-back for episodic, reflective for semantic,")
    print("  write-around for anything expensive. Never write-through from inside")
    print("  the agent loop into semantic memory — that is how a knowledge base")
    print("  fills up with the model's own hallucinations.")
    print(f"\n  (incident episodic log from part A still holds {len(episodic)} events)")


# ---------------------------------------------------------------------------
def part_f_retrieval(semantic: SemanticStore) -> None:
    header("F. Retrieval is scoring, not storage")

    now = time.time()
    for content, key, age_h, confidence in [
        ("checkout-api p99 regressed after the connection pool was reduced to 10", "pool:2024", 24 * 90, 0.9),
        ("checkout-api connection pool exhaustion resolved by doubling pool size", "pool:recent", 2, 0.95),
        ("checkout-api might have a connection leak, unconfirmed", "pool:rumour", 3, 0.35),
    ]:
        rec = MemoryRecord(content=content, key=key, tags=["checkout-api", "connections"],
                           provenance=Provenance("scribe", "history", confidence))
        rec.created_at = now - age_h * 3600
        semantic.upsert(rec)

    print("  query: 'checkout-api connection pool exhausted'\n")
    for i, hit in enumerate(semantic.search("checkout-api connection pool exhausted", k=6), 1):
        age_days = hit.age_seconds(now) / 86400
        conf = hit.provenance.confidence if hit.provenance else 1.0
        print(f"  {i}. [{age_days:5.1f}d old, conf {conf:.2f}] {hit.content[:62]}")
    print("\n  Three of these mention the connection pool and would score similarly")
    print("  on term overlap alone. Recency and confidence separate them: the")
    print("  90-day-old fact drops below the fresh one, and the unconfirmed rumour")
    print("  (conf 0.35) falls to the bottom. Pure cosine top-k cannot express that.")


# ---------------------------------------------------------------------------
def part_g_blackboard() -> None:
    header("G. Blackboard — lost updates, CAS, and scope")

    bb = Blackboard()
    bb.put("findings", ["metrics: error rate 240x"], author="metrics_analyst")

    # Two agents read the same version and both write. Blind writes lose one.
    entry = bb.get("findings")
    naive = list(entry.value)
    bb.put("findings", naive + ["logs: NullPointerException"], author="logs_analyst")
    bb.put("findings", naive + ["deploy: v1.1.0 7min ago"], author="deploy_analyst")
    print(f"  blind writes      -> {bb.get('findings').value}")
    print("                       ^ the logs analyst's finding is gone. Lost update.")

    bb.put("findings", ["metrics: error rate 240x"], author="reset")
    try:
        stale = bb.get("findings").version
        bb.put("findings", ["a"], author="agent_a", expected_version=stale)
        bb.put("findings", ["b"], author="agent_b", expected_version=stale)
    except ConflictError as exc:
        print(f"\n  compare-and-set   -> ConflictError: {exc}")
        print("                       ^ detected instead of silently clobbered")

    bb.put("findings", ["metrics: error rate 240x"], author="reset")
    for agent, finding in [
        ("logs_analyst", "logs: NullPointerException"),
        ("deploy_analyst", "deploy: v1.1.0 7min ago"),
    ]:
        bb.update("findings", lambda old, f=finding: list(old or []) + [f], author=agent)
    print(f"\n  CAS + retry       -> {bb.get('findings').value}")
    print("                       ^ both survive")

    bb.put("tenant_pii", "cardholder 4111...", author="ingest", scope=Scope.TENANT)
    bb.put("runbook", "rollback on deploy-correlated 5xx", author="scribe", scope=Scope.GLOBAL)
    visible = bb.view(reader_scopes=[Scope.THREAD, Scope.GLOBAL])
    print(f"\n  scoped read       -> keys visible to a THREAD-scoped agent: {sorted(visible)}")
    print("                       ^ tenant data is not in this agent's context at all")

    try:
        bb.put("dump", "x" * 9000, author="verbose_agent")
    except ValueError as exc:
        print(f"\n  size cap          -> {exc}")


def main() -> None:
    episodic, semantic, _ = part_a_tiers()
    part_b_budget()
    part_c_rolling_compaction()
    part_d_structured_state()
    part_e_write_strategies(episodic)
    part_f_retrieval(semantic)
    part_g_blackboard()
    print(f"\n{RULE}\ndone\n{RULE}")


if __name__ == "__main__":
    main()
