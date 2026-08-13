"""Test suite. Runs with pytest, or standalone: `python tests/test_all.py`.

Every test is deterministic and offline. That is the payoff of the model seam:
orchestration, budgeting, policy, locking, and verification are all assertable
without a network call, and the LLM-shaped judgments are pinned with a
ScriptedLLM rather than hoped for.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from incident import agents as incident_agents  # noqa: E402
from incident import policy  # noqa: E402
from incident.agents import AgentContext  # noqa: E402
from incident.graph import compile_graph  # noqa: E402
from incident.state import Action, Alert, Phase, Severity, new_state  # noqa: E402
from incident.tools import SimulatedCluster, inverse_of  # noqa: E402
from magents.coordination import (  # noqa: E402
    Deadline,
    DeadlockError,
    LivelockDetector,
    LockTimeout,
    ResourceManager,
    WaitForGraph,
    backoff,
)
from magents.graph import (  # noqa: E402
    END,
    InMemoryCheckpointer,
    Interrupt,
    RecursionLimit,
    StateGraph,
    add,
    append,
    unique_append,
)
from magents.llm import ScriptedLLM, extract_json  # noqa: E402
from magents.memory import (  # noqa: E402
    HISTORY,
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
    SqliteSemanticStore,
    WriteBackBuffer,
    compact_working_memory,
    make_working_memory,
    truncating_summarizer,
)
from magents.observability import Tracer  # noqa: E402
from magents.patterns import (  # noqa: E402
    CriticRefiner,
    MixtureOfAgents,
    Orchestrator,
    Proposer,
    Specialist,
    agreement_score,
    json_verifier,
)


# ===========================================================================
# graph engine
# ===========================================================================
class TestGraph:
    def test_linear_run(self):
        g = StateGraph()
        g.add_node("a", lambda s: {"trail": s.get("trail", "") + "a"})
        g.add_node("b", lambda s: {"trail": s.get("trail", "") + "b"})
        g.set_entry_point("a").add_edge("a", "b").add_edge("b", END)
        assert g.compile().invoke({})["trail"] == "ab"

    def test_parallel_fan_out_needs_a_reducer(self):
        """Without an append reducer, concurrent writers clobber each other.
        This is THE multi-agent state bug, so it gets a test in both directions."""

        def make(name):
            return lambda s: {"findings": [name]}

        for reducers, expected in [({}, 1), ({"findings": append}, 3)]:
            g = StateGraph(reducers=reducers)
            g.add_node("fan", lambda s: {})
            for name in ("x", "y", "z"):
                g.add_node(name, make(name))
                g.add_edge(name, "join")
            g.add_node("join", lambda s: {})
            g.set_entry_point("fan")
            g.add_conditional_edges("fan", lambda s: ["x", "y", "z"])
            g.add_edge("join", END)
            assert len(g.compile().invoke({})["findings"]) == expected

    def test_nodes_cannot_see_same_superstep_writes(self):
        """Snapshot isolation: a parallel peer's write must not be visible."""
        g = StateGraph(reducers={"seen": append, "findings": append})
        g.add_node("fan", lambda s: {"findings": ["seed"]})
        g.add_node("p", lambda s: {"seen": [len(s["findings"])], "findings": ["p"]})
        g.add_node("q", lambda s: {"seen": [len(s["findings"])], "findings": ["q"]})
        g.add_node("join", lambda s: {})
        g.set_entry_point("fan")
        g.add_conditional_edges("fan", lambda s: ["p", "q"])
        g.add_edge("p", "join")
        g.add_edge("q", "join")
        g.add_edge("join", END)
        out = g.compile().invoke({"findings": []})
        assert out["seen"] == [1, 1]  # both saw only "seed"
        assert sorted(out["findings"]) == ["p", "q", "seed"]

    def test_conditional_routing(self):
        g = StateGraph()
        g.add_node("check", lambda s: {"n": s["n"] + 1})
        g.add_node("small", lambda s: {"label": "small"})
        g.add_node("big", lambda s: {"label": "big"})
        g.set_entry_point("check")
        g.add_conditional_edges("check", lambda s: "big" if s["n"] > 5 else "small")
        g.add_edge("small", END)
        g.add_edge("big", END)
        compiled = g.compile()
        assert compiled.invoke({"n": 0})["label"] == "small"
        assert compiled.invoke({"n": 10})["label"] == "big"

    def test_recursion_limit_stops_a_cycle(self):
        g = StateGraph(reducers={"n": add})
        g.add_node("loop", lambda s: {"n": 1})
        g.set_entry_point("loop").add_edge("loop", "loop")
        with pytest.raises(RecursionLimit):
            g.compile(recursion_limit=5).invoke({"n": 0})

    def test_validation_rejects_a_dead_end(self):
        g = StateGraph()
        g.add_node("a", lambda s: {})
        g.set_entry_point("a")
        with pytest.raises(Exception, match="no outgoing edge"):
            g.compile()

    def test_interrupt_checkpoints_and_resumes(self):
        cp = InMemoryCheckpointer()
        g = StateGraph(reducers={"trail": append})
        g.add_node("before", lambda s: {"trail": "before"})
        g.add_node("gated", lambda s: {"trail": f"gated:{s.get('token')}"})
        g.set_entry_point("before").add_edge("before", "gated").add_edge("gated", END)
        compiled = g.compile(checkpointer=cp, interrupt_before=["gated"])

        with pytest.raises(Interrupt) as caught:
            compiled.invoke({}, thread_id="t1")
        assert caught.value.node == "gated"
        assert cp.latest("t1").state["trail"] == ["before"]  # pre-action state

        # The patch is how a human injects a decision into a paused run.
        final = compiled.resume("t1", {"token": "approved"})
        assert final["trail"] == ["before", "gated:approved"]

    def test_reducers(self):
        assert append(["a"], ["b"]) == ["a", "b"]
        assert add(2, 3) == 5
        dedupe = unique_append(key=lambda x: x["id"])
        assert dedupe([{"id": 1}], [{"id": 1}, {"id": 2}]) == [{"id": 1}, {"id": 2}]


# ===========================================================================
# working memory
# ===========================================================================
class TestWorkingMemory:
    def test_system_and_task_survive_eviction(self):
        wm = make_working_memory("SYSTEM", "TASK", BudgetPolicy(total_tokens=800, reserve_output=200))
        for i in range(40):
            wm.add(f"noise {i} " + "x " * 200, HISTORY, priority=0.1)
        wm.fit()
        texts = [i.text for i in wm.items]
        assert "SYSTEM" in texts and "TASK" in texts

    def test_eviction_prefers_recent_and_high_priority(self):
        # history cap = 0.45 * (800-200) = 270 tokens; each item is ~152.
        wm = make_working_memory("s", "t", BudgetPolicy(total_tokens=800, reserve_output=200))
        wm.add("OLD LOW " + "x " * 300, HISTORY, priority=0.1)
        wm.add("NEW HIGH " + "y " * 300, HISTORY, priority=0.95)
        report = wm.fit()
        kept = " ".join(i.text[:9] for i in wm.items)
        assert "NEW HIGH" in kept
        assert report.evicted and report.evicted[0].text.startswith("OLD LOW")

    def test_compaction_replaces_rather_than_drops(self):
        # scratch cap = 0.10 * (4000-400) = 360 tokens; each item is ~101, so
        # three fit and three overflow into a digest that must fit the remainder.
        wm = make_working_memory(
            "s", "t", BudgetPolicy(total_tokens=4000, reserve_output=400),
            summarizer=truncating_summarizer(60),
        )
        for i in range(6):
            wm.add(f"item {i} " + "z " * 200, SCRATCH, priority=0.2)
        report = wm.fit()
        assert report.summarized > 0
        assert any("compacted" in i.text for i in wm.items)

    def test_rolling_compaction_keeps_the_tail_verbatim(self):
        wm = make_working_memory("s", "t")
        for i in range(10):
            wm.add(f"turn {i}", HISTORY)
        compact_working_memory(wm, truncating_summarizer(200), keep_tail=3)
        history = [i.text for i in wm.items if i.segment == HISTORY]
        assert history[-3:] == ["turn 7", "turn 8", "turn 9"]
        assert history[0].startswith("[compacted 7")  # and it sorts first

    def test_render_repeats_the_task_at_the_end(self):
        wm = make_working_memory("SYS", "THE TASK")
        wm.add("some history", HISTORY)
        system, messages = wm.render()
        assert system == "SYS"
        body = messages[0]["content"]
        assert body.count("THE TASK") == 2  # once pinned up top, once as a reminder
        assert body.rindex("THE TASK") > body.rindex("some history")


# ===========================================================================
# stores
# ===========================================================================
class TestStores:
    def test_semantic_upsert_does_not_duplicate(self):
        store = SemanticStore()
        for _ in range(5):
            store.upsert(MemoryRecord(content="pool size is 20", key="svc:pool"))
        assert len(store) == 1

    def test_semantic_upsert_versions_a_changed_fact(self):
        store = SemanticStore()
        store.upsert(MemoryRecord(content="pool size is 20", key="svc:pool"))
        updated = store.upsert(MemoryRecord(content="pool size is 40", key="svc:pool"))
        assert len(store) == 1
        assert updated.metadata["version"] == 2
        assert store.get("svc:pool").content == "pool size is 40"

    def test_retrieval_weighs_recency(self):
        store = SemanticStore()
        now = time.time()
        old = MemoryRecord(content="checkout pool exhausted", key="old")
        old.created_at = now - 120 * 86400
        store.upsert(old)
        store.upsert(MemoryRecord(content="checkout pool exhausted", key="new"))
        assert store.search("checkout pool exhausted", k=2)[0].key == "new"

    def test_retrieval_weighs_confidence(self):
        store = SemanticStore()
        store.upsert(MemoryRecord(content="checkout leaks connections", key="rumour",
                                  provenance=Provenance("a", "guess", 0.2)))
        store.upsert(MemoryRecord(content="checkout leaks connections", key="confirmed",
                                  provenance=Provenance("a", "heap dump", 0.95)))
        assert store.search("checkout leaks connections", k=2)[0].key == "confirmed"

    def test_ttl_expiry(self):
        store = SemanticStore()
        rec = MemoryRecord(content="transient", key="t", ttl_seconds=0.01)
        store.upsert(rec)
        time.sleep(0.02)
        assert store.get("t") is None

    def test_episodic_is_append_only_and_ordered(self):
        store = EpisodicStore()
        for i in range(5):
            store.log(f"event {i}", author="a")
        assert len(store) == 5
        assert store.recent(2)[0].content == "event 4"

    def test_procedural_requires_promotion(self):
        store = ProceduralStore()
        candidate = store.propose("runbook", "steps", author="agent")
        assert store.get("runbook") is None  # a candidate is not active
        store.promote("runbook", candidate.id, approver="human")
        assert store.get("runbook").content == "steps"

    def test_sqlite_store_round_trips(self, tmp_path):
        path = str(tmp_path / "mem.db")
        store = SqliteSemanticStore(path)
        store.upsert(MemoryRecord(content="durable fact", key="k", tags=["t"],
                                  provenance=Provenance("a", "s", 0.9)))
        reopened = SqliteSemanticStore(path)
        assert reopened.get("k").content == "durable fact"

    def test_write_back_buffer_is_idempotent(self):
        episodic = EpisodicStore()
        buffer = WriteBackBuffer(episodic, author="agent")
        buffer.record("same observation")
        buffer.record("same observation")
        assert buffer.flush() == 1
        buffer.record("same observation")  # a retried turn
        assert buffer.flush() == 0


# ===========================================================================
# blackboard
# ===========================================================================
class TestBlackboard:
    def test_cas_detects_a_lost_update(self):
        bb = Blackboard()
        bb.put("k", "v0", author="a")
        version = bb.get("k").version
        bb.put("k", "v1", author="a", expected_version=version)
        with pytest.raises(ConflictError):
            bb.put("k", "v2", author="b", expected_version=version)

    def test_update_retries_through_contention(self):
        bb = Blackboard()
        bb.put("counter", 0, author="init")
        errors: list[Exception] = []

        def bump():
            try:
                for _ in range(20):
                    bb.update("counter", lambda v: (v or 0) + 1, author="w", retries=10)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=bump) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert bb.get("counter").value == 80  # no lost updates

    def test_scope_filters_reads(self):
        bb = Blackboard()
        bb.put("pii", "secret", author="a", scope=Scope.TENANT)
        bb.put("shared", "ok", author="a", scope=Scope.THREAD)
        assert bb.get("pii", reader_scopes=[Scope.THREAD]) is None
        assert sorted(bb.view(reader_scopes=[Scope.THREAD])) == ["shared"]

    def test_value_size_cap(self):
        bb = Blackboard(max_value_chars=100)
        with pytest.raises(ValueError, match="publish a conclusion"):
            bb.put("k", "x" * 200, author="a")


# ===========================================================================
# patterns
# ===========================================================================
class TestPatterns:
    def test_orchestrator_fans_out_and_synthesizes(self):
        llm = ScriptedLLM(
            routes=[
                (lambda s, u: s.startswith("You decompose"),
                 '{"subtasks": [{"id":"s1","specialist":"alpha","wave":0,"task":"A"},'
                 '{"id":"s2","specialist":"beta","wave":0,"task":"B"}]}'),
                (lambda s, u: s.startswith("You are the orchestrator"), "combined"),
                (lambda s, u: "alpha" in s, "alpha result"),
                (lambda s, u: "beta" in s, "beta result"),
            ]
        )
        orchestrator = Orchestrator(llm, [
            Specialist("alpha", "does alpha", "You are alpha"),
            Specialist("beta", "does beta", "You are beta"),
        ])
        result = orchestrator.run("task")
        assert len(result.plan) == 2
        assert {r.output for r in result.results} == {"alpha result", "beta result"}
        assert result.answer == "combined"

    def test_orchestrator_drops_unknown_specialists(self):
        llm = ScriptedLLM(routes=[
            (lambda s, u: s.startswith("You decompose"),
             '{"subtasks": [{"id":"s1","specialist":"ghost","wave":0,"task":"A"}]}'),
        ], fallback="direct answer")
        orchestrator = Orchestrator(llm, [Specialist("real", "d", "s")])
        assert orchestrator.run("task").answer == "direct answer"

    def test_orchestrator_survives_a_failing_specialist(self):
        def boom(task, llm):
            raise RuntimeError("tool exploded")

        llm = ScriptedLLM(routes=[
            (lambda s, u: s.startswith("You decompose"),
             '{"subtasks": [{"id":"s1","specialist":"bad","wave":0,"task":"A"}]}'),
            (lambda s, u: s.startswith("You are the orchestrator"), "degraded answer"),
        ])
        orchestrator = Orchestrator(llm, [Specialist("bad", "d", "s", handler=boom)])
        result = orchestrator.run("task")
        assert len(result.failures) == 1
        assert result.answer == "degraded answer"

    def test_critic_refiner_stops_on_pass(self):
        llm = ScriptedLLM(routes=[
            (lambda s, u: s.startswith("You review"),
             '{"score": 9.5, "verdict": "pass", "issues": [], "summary": "good"}'),
        ])
        result = CriticRefiner(llm, rubric="r").run("task", draft="draft")
        assert result.iterations == 1
        assert result.stopped_because == "passed"

    def test_critic_refiner_returns_the_best_not_the_last(self):
        """Refinement is not monotone: v2 can be worse than v1."""
        scores = iter([
            '{"score": 7.0, "verdict": "revise", "issues": [{"severity":"low","what":"x","fix":"y"}], "summary": "s"}',
            '{"score": 2.0, "verdict": "revise", "issues": [{"severity":"low","what":"x","fix":"y"}], "summary": "s"}',
            '{"score": 3.0, "verdict": "revise", "issues": [], "summary": "s"}',
        ])
        llm = ScriptedLLM(routes=[
            (lambda s, u: s.startswith("You review"), ""),
            (lambda s, u: s.startswith("Revise"), "revised"),
        ])
        # Route "You review" dynamically through a closure over the iterator.
        llm.routes[0] = (lambda s, u: s.startswith("You review"), "")
        original = llm.complete

        def patched(system, messages, **kw):
            if system.startswith("You review"):
                from magents.llm import Completion
                return Completion(next(scores))
            return original(system, messages, **kw)

        llm.complete = patched  # type: ignore[method-assign]
        result = CriticRefiner(llm, rubric="r", max_iterations=3, min_improvement=-99).run(
            "task", draft="v1"
        )
        assert result.trajectory() == [7.0, 2.0, 3.0]
        assert result.output == "v1"  # the best-scoring draft, not the last

    def test_deterministic_verifier_short_circuits_the_model(self):
        llm = ScriptedLLM(routes=[
            (lambda s, u: True, '{"score": 10, "verdict": "pass", "issues": [], "summary": "lgtm"}'),
        ])
        refiner = CriticRefiner(llm, rubric="r", verifiers=[json_verifier(("action",))])
        critique = refiner.critique("task", "not json at all")
        assert critique.score == 0.0 and critique.verdict == "revise"
        # The model was never asked — the only call would have been the critic.
        assert not any(c["system"].startswith("You review") for c in llm.calls)

    def test_moa_aggregates_all_proposers(self):
        llm = ScriptedLLM(routes=[
            (lambda s, u: "persona A" in s, "answer from A"),
            (lambda s, u: "persona B" in s, "answer from B"),
            (lambda s, u: s.startswith("You synthesize"), "synthesized"),
        ])
        moa = MixtureOfAgents(llm, [
            Proposer("a", "You are persona A"),
            Proposer("b", "You are persona B"),
        ])
        result = moa.run("question")
        assert result.n_ok == 2
        assert result.answer == "synthesized"

    def test_moa_weighted_vote_needs_no_aggregator_call(self):
        llm = ScriptedLLM(routes=[
            (lambda s, u: "A" in s, "verdict: rollback"),
            (lambda s, u: "B" in s, "verdict: rollback"),
            (lambda s, u: "C" in s, "verdict: scale"),
        ])
        moa = MixtureOfAgents(llm, [
            Proposer("a", "persona A"), Proposer("b", "persona B"),
            Proposer("c", "persona C", weight=3.0),
        ])
        winner, distribution = moa.vote("q", lambda t: t.split("verdict: ")[-1].strip())
        assert winner == "scale"  # weight 3 outvotes two weight-1 proposers
        assert distribution["scale"] == pytest.approx(0.6)

    def test_agreement_score_flags_correlated_proposers(self):
        from magents.patterns.moa import Proposal

        identical = [Proposal("a", "roll back the deploy now"), Proposal("b", "roll back the deploy now")]
        diverse = [Proposal("a", "roll back the deploy"), Proposal("b", "increase replica capacity")]
        assert agreement_score(identical) > 0.9
        assert agreement_score(diverse) < 0.4


# ===========================================================================
# coordination
# ===========================================================================
class TestCoordination:
    def test_wait_for_graph_finds_a_cycle(self):
        g = WaitForGraph()
        assert g.wait("a", "b") is None
        assert g.wait("b", "c") is None
        cycle = g.wait("c", "a")
        assert cycle is not None and set(cycle) == {"a", "b", "c"}

    def test_wait_for_graph_ignores_a_dag(self):
        g = WaitForGraph()
        assert g.wait("a", "b") is None
        assert g.wait("a", "c") is None
        assert g.wait("b", "c") is None

    def test_ordering_rejects_out_of_order_acquisition(self):
        rm = ResourceManager(ordered=True)
        rm.acquire("agent", "svc:z")
        with pytest.raises(DeadlockError):
            rm.acquire("agent", "svc:a")

    def test_hold_all_releases_on_partial_failure(self):
        rm = ResourceManager(ordered=True, default_timeout=0.05)
        rm.acquire("other", "svc:b")
        with pytest.raises(LockTimeout):
            with rm.hold_all("agent", ["svc:a", "svc:b"]):
                pass
        assert rm.holders()["svc:a"] is None  # no leak

    def test_timeout_bounds_the_wait(self):
        rm = ResourceManager(ordered=True)
        rm.acquire("holder", "r")
        started = time.monotonic()
        with pytest.raises(LockTimeout):
            rm.acquire("waiter", "r", timeout=0.1)
        assert 0.09 <= time.monotonic() - started < 1.0

    def test_reentrant_acquire(self):
        rm = ResourceManager(ordered=True)
        assert rm.acquire("a", "r")
        assert rm.acquire("a", "r")

    def test_fifo_queue_prevents_starvation(self):
        rm = ResourceManager(ordered=True, default_timeout=2.0)
        rm.acquire("holder", "r")
        order: list[str] = []

        def waiter(name):
            rm.acquire(name, "r", timeout=2.0)
            order.append(name)
            rm.release(name, "r")

        threads = []
        for name in ("first", "second"):
            t = threading.Thread(target=waiter, args=(name,))
            t.start()
            threads.append(t)
            time.sleep(0.05)  # establish queue order
        rm.release("holder", "r")
        for t in threads:
            t.join(timeout=3)
        assert order == ["first", "second"]

    def test_livelock_detector(self):
        detector = LivelockDetector(window=6, repeats=2)
        assert not detector.observe(("svc", 3))
        assert not detector.observe(("svc", 6))
        assert not detector.observe(("svc", 3))
        assert not detector.observe(("svc", 6))
        assert detector.observe(("svc", 3))  # third repeat

    def test_backoff_is_bounded_and_jittered(self):
        samples = [backoff(4, base=0.05, cap=1.0) for _ in range(50)]
        assert all(0 <= s <= 1.0 for s in samples)
        assert len(set(samples)) > 1  # jitter is actually applied

    def test_deadline_expires(self):
        gate = Deadline(seconds=0.05, on_expiry="escalate")
        assert not gate.expired
        time.sleep(0.06)
        assert gate.expired and gate.remaining == 0.0


# ===========================================================================
# incident: policy
# ===========================================================================
class TestPolicy:
    def test_unknown_action_is_rejected(self):
        decision = policy.evaluate(
            Action("rm_minus_rf", "service/checkout-api", {"service": "checkout-api"}),
            severity=Severity.SEV2, confidence=1.0, guardrails=policy.Guardrails(),
        )
        assert not decision.allowed and "catalog" in decision.reason

    def test_high_risk_needs_high_confidence(self):
        action = Action("rollback_deploy", "service/checkout-api", {"service": "checkout-api"})
        low = policy.evaluate(action, severity=Severity.SEV2, confidence=0.5,
                              guardrails=policy.Guardrails())
        high = policy.evaluate(action, severity=Severity.SEV2, confidence=0.95,
                               guardrails=policy.Guardrails())
        assert low.requires_approval and not high.requires_approval

    def test_sev1_never_auto_executes(self):
        decision = policy.evaluate(
            Action("scale", "service/checkout-api", {"service": "checkout-api", "replicas": 8}),
            severity=Severity.SEV1, confidence=0.99, guardrails=policy.Guardrails(),
        )
        assert decision.requires_approval

    def test_critical_risk_never_auto_executes(self):
        decision = policy.evaluate(
            Action("drain_and_failover", "service/checkout-api", {"service": "checkout-api"}),
            severity=Severity.SEV2, confidence=1.0, guardrails=policy.Guardrails(),
        )
        assert decision.requires_approval

    def test_change_freeze_gates_everything(self):
        decision = policy.evaluate(
            Action("scale", "service/checkout-api", {"service": "checkout-api", "replicas": 8}),
            severity=Severity.SEV3, confidence=0.99,
            guardrails=policy.Guardrails(change_freeze=True),
        )
        assert decision.requires_approval and "freeze" in decision.reason

    def test_escalation_is_never_blocked(self):
        decision = policy.evaluate(
            Action("page_oncall", "service/checkout-api",
                   {"service": "checkout-api", "message": "help"}),
            severity=Severity.SEV1, confidence=0.0,
            guardrails=policy.Guardrails(change_freeze=True),
        )
        assert decision.auto_executable

    def test_missing_params_are_rejected(self):
        decision = policy.evaluate(
            Action("scale", "service/checkout-api", {"service": "checkout-api"}),
            severity=Severity.SEV2, confidence=1.0, guardrails=policy.Guardrails(),
        )
        assert not decision.allowed and "replicas" in decision.reason

    def test_out_of_scope_service_is_rejected(self):
        decision = policy.evaluate(
            Action("scale", "service/mystery", {"service": "mystery", "replicas": 2}),
            severity=Severity.SEV2, confidence=1.0, guardrails=policy.Guardrails(),
        )
        assert not decision.allowed and "not in scope" in decision.reason

    def test_attempt_limit_blocks_further_action(self):
        decision = policy.evaluate(
            Action("scale", "service/checkout-api", {"service": "checkout-api", "replicas": 8}),
            severity=Severity.SEV2, confidence=1.0, guardrails=policy.Guardrails(), attempts=2,
        )
        assert not decision.allowed and "attempt limit" in decision.reason

    def test_blast_radius_includes_dependents(self):
        alone = policy.blast_radius(
            Action("scale", "service/checkout-api", {"service": "checkout-api", "replicas": 2})
        )
        upstream = policy.blast_radius(
            Action("scale", "service/ledger-db-proxy", {"service": "ledger-db-proxy", "replicas": 2})
        )
        assert upstream > alone  # ledger-db-proxy has two dependents

    def test_plan_needs_approval_if_any_action_does(self):
        actions = [
            Action("restart_pods", "service/checkout-api", {"service": "checkout-api", "count": 1}),
            Action("drain_and_failover", "service/checkout-api", {"service": "checkout-api"}),
        ]
        _, verdict = policy.evaluate_plan(actions, severity=Severity.SEV2, confidence=0.99,
                                          guardrails=policy.Guardrails())
        assert verdict.allowed and verdict.requires_approval

    def test_oversized_plan_is_rejected(self):
        actions = [
            Action("restart_pods", "service/checkout-api", {"service": "checkout-api", "count": 1})
        ] * 6
        _, verdict = policy.evaluate_plan(actions, severity=Severity.SEV2, confidence=0.9,
                                          guardrails=policy.Guardrails())
        assert not verdict.allowed and "cap is 5" in verdict.reason


# ===========================================================================
# incident: tools and state
# ===========================================================================
class TestIncidentPrimitives:
    def test_idempotency_key_is_stable_and_discriminating(self):
        a = Action("scale", "service/x", {"service": "x", "replicas": 4})
        b = Action("scale", "service/x", {"replicas": 4, "service": "x"})  # key order differs
        c = Action("scale", "service/x", {"service": "x", "replicas": 8})
        assert a.idempotency_key == b.idempotency_key
        assert a.idempotency_key != c.idempotency_key

    def test_inverse_is_computed_from_live_state(self):
        cluster = SimulatedCluster()
        cluster.services["checkout-api"].replicas = 6
        action = Action("scale", "service/checkout-api", {"service": "checkout-api", "replicas": 20})
        inverse = inverse_of(action, cluster)
        assert inverse.params["replicas"] == 6  # the real current value, not the plan's

    def test_irreversible_actions_have_no_inverse(self):
        cluster = SimulatedCluster()
        action = Action("restart_pods", "service/checkout-api",
                        {"service": "checkout-api", "count": 2})
        assert inverse_of(action, cluster) is None

    def test_alert_fingerprint_dedupes(self):
        a = Alert("1", "checkout-api", "t", "d", "error_rate", 0.2, 0.01)
        b = Alert("2", "checkout-api", "different title", "d", "error_rate", 0.3, 0.01)
        assert a.fingerprint == b.fingerprint

    def test_faults_produce_matching_symptoms(self):
        for fault, probe in [
            ("bad_deploy", lambda c: c.get_deployments("checkout-api")["recent"]),
            ("resource_exhaustion", lambda c: c.get_metrics("checkout-api")["cpu_saturation"] > 0.9),
            ("connection_pool_exhaustion", lambda c: c.get_connections("checkout-api")["utilization"] == 1.0),
            ("downstream_dependency",
             lambda c: c.get_dependencies("checkout-api")["health"]["ledger-db-proxy"]["cpu_saturation"] > 0.9),
        ]:
            cluster = SimulatedCluster()
            cluster.inject_fault(fault)
            assert probe(cluster), fault


# ===========================================================================
# incident: agents
# ===========================================================================
def make_ctx(fault: str | None = None, llm=None) -> AgentContext:
    cluster = SimulatedCluster()
    if fault:
        cluster.inject_fault(fault)
    return AgentContext(
        llm=llm or ScriptedLLM(routes=[], fallback=""),
        cluster=cluster,
        episodic=EpisodicStore(),
        semantic=SemanticStore(),
        procedural=ProceduralStore(),
    )


class TestIncidentAgents:
    def test_triage_dedupes_before_calling_the_model(self):
        ctx = make_ctx("bad_deploy")
        alert = Alert("1", "checkout-api", "t", "d", "error_rate", 0.2, 0.01)
        result = incident_agents.triage(alert, ctx, {alert.fingerprint})
        assert result["suppress"]
        assert not ctx.llm.calls  # no model call for a duplicate

    def test_triage_falls_back_when_the_model_is_unavailable(self):
        ctx = make_ctx("bad_deploy")
        alert = Alert("1", "checkout-api", "t", "d", "error_rate", 0.6, 0.01)  # 60x
        result = incident_agents.triage(alert, ctx, set())
        assert result["severity"] is Severity.SEV1 and not result["suppress"]

    def test_analysts_are_read_only(self):
        ctx = make_ctx("bad_deploy")
        before = ctx.cluster.audit.copy()
        alert = Alert("1", "checkout-api", "t", "d", "error_rate", 0.24, 0.01)
        for analyst in incident_agents.ANALYSTS.values():
            analyst(alert, ctx)
        assert ctx.cluster.audit == before  # nothing mutated

    def test_analysts_find_the_right_signal(self):
        alert = Alert("1", "checkout-api", "t", "d", "error_rate", 0.24, 0.01)
        cases = {
            "bad_deploy": ("deploy_analyst", "recent_change"),
            "resource_exhaustion": ("metrics_analyst", "saturation"),
            "connection_pool_exhaustion": ("logs_analyst", "connections"),
            "downstream_dependency": ("deploy_analyst", "downstream"),
        }
        for fault, (agent, tag) in cases.items():
            ctx = make_ctx(fault)
            findings = incident_agents.ANALYSTS[agent](alert, ctx)
            assert any(tag in f.tags for f in findings), (fault, agent, tag)

    def test_heuristic_synthesis_ranks_downstream_above_stale_deploys(self):
        """The trap case: a 2h-old deploy must not outrank a saturated dependency."""
        ctx = make_ctx("downstream_dependency")
        alert = Alert("1", "checkout-api", "t", "d", "error_rate", 0.15, 0.01)
        findings = [f for a in incident_agents.ANALYSTS.values() for f in a(alert, ctx)]
        hypotheses = incident_agents.synthesize(alert, findings, ctx)
        assert "downstream" in hypotheses[0].cause or "dependency" in hypotheses[0].cause

    def test_planner_escalates_when_the_cause_is_elsewhere(self):
        from incident.state import Hypothesis

        ctx = make_ctx("downstream_dependency")
        alert = Alert("1", "checkout-api", "t", "d", "error_rate", 0.15, 0.01)
        plan = incident_agents.plan_remediation(
            alert, [Hypothesis("downstream dependency is saturated", 0.85)], ctx
        )
        assert [a.tool for a in plan.actions] == ["page_oncall"]

    def test_verifier_checks_more_than_the_alerting_signal(self):
        ctx = make_ctx("resource_exhaustion")
        alert = Alert("1", "checkout-api", "t", "d", "error_rate", 0.11, 0.01)
        result = incident_agents.verify(alert, ctx)
        assert not result["recovered"]
        assert set(result["checks"]) == {
            "error_rate", "latency_p99", "cpu_saturation", "memory_saturation"
        }

    def test_scribe_filters_low_confidence_facts(self):
        llm = ScriptedLLM(routes=[
            (lambda s, u: s.startswith("Write a postmortem"),
             '{"summary": "s", "facts": ['
             '{"key": "keep", "content": "solid fact", "confidence": 0.9},'
             '{"key": "drop", "content": "wild guess", "confidence": 0.3}], "runbook": null}'),
        ])
        ctx = make_ctx("bad_deploy", llm=llm)
        alert = Alert("1", "checkout-api", "t", "d", "error_rate", 0.24, 0.01)
        result = incident_agents.write_postmortem("INC-1", alert, ["t"], "resolved", ctx)
        assert result["facts_written"] == 1
        assert ctx.semantic.get("keep") and ctx.semantic.get("drop") is None

    def test_scribe_only_proposes_runbooks_and_only_on_success(self):
        runbook = ('{"summary": "s", "facts": [], "runbook": {"name": "rb", "when": "w", '
                   '"steps": ["a"]}}')
        llm = ScriptedLLM(routes=[(lambda s, u: s.startswith("Write a postmortem"), runbook)])

        ctx = make_ctx("bad_deploy", llm=llm)
        alert = Alert("1", "checkout-api", "t", "d", "error_rate", 0.24, 0.01)

        failed = incident_agents.write_postmortem("INC-1", alert, ["t"], "escalated", ctx)
        assert failed["runbook_proposed"] is None  # a failure must not codify itself

        ok = incident_agents.write_postmortem("INC-2", alert, ["t"], "resolved", ctx)
        assert ok["runbook_proposed"]["status"] == "awaiting human approval"
        assert ctx.procedural.get("rb") is None  # proposed, not active


# ===========================================================================
# incident: end to end
# ===========================================================================
def scripted_incident_llm() -> ScriptedLLM:
    return ScriptedLLM(routes=[
        (lambda s, u: s.startswith("You are the safety reviewer"),
         '{"verdict": "pass", "score": 8.5, "issues": [], "summary": "ok"}'),
        (lambda s, u: s.startswith("Write a postmortem"),
         '{"summary": "done", "facts": [], "runbook": null}'),
    ], fallback="")


class TestIncidentEndToEnd:
    def _run(self, fault: str, alert: Alert, guardrails=None, approve=True):
        ctx = make_ctx(fault, llm=scripted_incident_llm())
        compiled, tracer = compile_graph(
            ctx, guardrails=guardrails or policy.Guardrails(),
            checkpointer=InMemoryCheckpointer(), locks=ResourceManager(ordered=True, default_timeout=1.0),
        )
        state = new_state(alert)
        thread_id = state["incident_id"]
        pauses = 0
        run = lambda: compiled.invoke(state, thread_id=thread_id)  # noqa: E731
        for _ in range(5):
            try:
                return run(), ctx, pauses, tracer
            except Interrupt:
                pauses += 1
                if not approve:
                    return None, ctx, pauses, tracer
                run = lambda: compiled.resume(  # noqa: E731
                    thread_id, {"approval_decision": "granted", "approver": "human"}
                )
        raise AssertionError("too many pauses")

    def test_resource_exhaustion_resolves_autonomously(self):
        alert = Alert("1", "checkout-api", "latency", "d", "latency_p99", 2600, 500)
        final, ctx, pauses, _ = self._run("resource_exhaustion", alert)
        assert final["phase"] == Phase.RESOLVED.value
        assert pauses == 0
        assert any("scale" in a for a in ctx.cluster.audit)

    def test_bad_deploy_pauses_then_resolves(self):
        alert = Alert("1", "checkout-api", "errors", "d", "error_rate", 0.24, 0.01)
        final, ctx, pauses, _ = self._run("bad_deploy", alert)
        assert final["phase"] == Phase.RESOLVED.value
        assert pauses == 1  # rollback is HIGH risk at 0.82 confidence
        assert any("rollback" in a for a in ctx.cluster.audit)

    def test_downstream_trap_escalates_without_touching_the_wrong_service(self):
        alert = Alert("1", "checkout-api", "errors", "d", "error_rate", 0.15, 0.01)
        final, ctx, _, _ = self._run("downstream_dependency", alert)
        assert final["phase"] == Phase.ESCALATED.value
        # It paged, and it did NOT restart/scale/roll back the victim service.
        assert all(a.startswith("page") for a in ctx.cluster.audit)

    def test_idempotency_prevents_double_application_on_retry(self):
        alert = Alert("1", "checkout-api", "errors", "d", "error_rate", 0.15, 0.01)
        final, _, _, _ = self._run("downstream_dependency", alert)
        skipped = [r for r in final["executions"] if r.status == "skipped"]
        assert skipped, "the retried plan should have hit the idempotency ledger"

    def test_change_freeze_forces_approval_on_an_otherwise_auto_plan(self):
        alert = Alert("1", "checkout-api", "latency", "d", "latency_p99", 2600, 500)
        _, _, without, _ = self._run("resource_exhaustion", alert)
        _, _, with_freeze, _ = self._run(
            "resource_exhaustion", alert, guardrails=policy.Guardrails(change_freeze=True)
        )
        assert without == 0 and with_freeze == 1

    def test_declining_approval_leaves_production_untouched(self):
        alert = Alert("1", "checkout-api", "errors", "d", "error_rate", 0.24, 0.01)
        final, ctx, pauses, _ = self._run("bad_deploy", alert, approve=False)
        assert final is None and pauses == 1
        assert ctx.cluster.audit == []  # paused before any write
        assert ctx.cluster.services["checkout-api"].version == "v1.1.0"

    def test_bounded_retries_terminate(self):
        alert = Alert("1", "checkout-api", "errors", "d", "error_rate", 0.15, 0.01)
        final, _, _, _ = self._run("downstream_dependency", alert)
        assert final["attempts"] <= policy.Guardrails().max_remediation_attempts

    def test_every_run_writes_a_postmortem(self):
        for fault, alert in [
            ("bad_deploy", Alert("1", "checkout-api", "e", "d", "error_rate", 0.24, 0.01)),
            ("downstream_dependency", Alert("2", "checkout-api", "e", "d", "error_rate", 0.15, 0.01)),
        ]:
            final, ctx, _, _ = self._run(fault, alert)
            assert len(ctx.episodic) >= 1
            assert any("postmortem" in e for e in final["events"])

    def test_trace_records_every_decision_point(self):
        alert = Alert("1", "checkout-api", "latency", "d", "latency_p99", 2600, 500)
        _, _, _, tracer = self._run("resource_exhaustion", alert)
        decisions = {s.name for s in tracer.spans if s.name.startswith("decision:")}
        assert {"decision:triage", "decision:root_cause", "decision:safety",
                "decision:verify"} <= decisions


# ===========================================================================
# misc
# ===========================================================================
class TestMisc:
    def test_extract_json_handles_fences_and_prose(self):
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
        assert extract_json('Sure! Here you go: {"a": 1} Hope that helps.') == {"a": 1}
        assert extract_json("no json here", default={}) == {}

    def test_tracer_costs_and_nests(self):
        tracer = Tracer()
        from magents.llm import Usage

        with tracer.span("outer") as outer:
            tracer.record_usage(outer, Usage(1_000_000, 100_000), "claude-opus-5")
            with tracer.span("inner"):
                pass
        assert tracer.total_cost_usd == pytest.approx(5.0 + 2.5)
        assert "inner" in tracer.tree()

    def test_tracer_records_errors(self):
        tracer = Tracer()
        with pytest.raises(ValueError):
            with tracer.span("boom"):
                raise ValueError("nope")
        assert tracer.spans[0].status == "error"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
