from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from ragx.config import Config
from ragx.eval.dataset import load_cases
from ragx.eval.retrieval_metrics import ndcg_at_k, recall_at_k, reciprocal_rank
from ragx.eval.runner import Thresholds, run_eval
from ragx.factory import build
from ragx.llm import ScriptedLLM
from ragx.service.app import create_app
from ragx.types import Principal, Visibility

# ---- pipeline --------------------------------------------------------------


def test_end_to_end_answer_has_citations(system, employee):
    result = system.pipeline.ask("How long do I have to submit an expense claim?", employee)
    answer = result.answer
    assert not answer.abstained
    assert answer.citations
    assert result.debug["retrieval"]["dense_hits"] > 0
    assert result.debug["retrieval"]["lexical_hits"] > 0


def test_pipeline_abstains_without_calling_the_model_when_nothing_is_permitted(corpus_dir):
    calls: list[str] = []

    def handler(model: str, prompt: str) -> str:
        calls.append(prompt)
        return "should never be called"

    sys_ = build(Config(), offline=True, scripted_handler=handler)
    sys_.ingest_directory(corpus_dir, tenant_id="acme")

    outsider = Principal(tenant_id="other-tenant", subject_id="x")
    result = sys_.pipeline.ask("expense claim deadline", outsider)

    assert result.answer.abstained
    assert calls == []  # no generation spend on a request with no context


def test_confidential_answer_requires_the_right_principal(system, employee, finance):
    question = "What is the salary band for a Staff Engineer?"
    denied = system.pipeline.ask(question, employee).answer
    allowed = system.pipeline.ask(question, finance).answer

    assert not any(c.doc_id == "compensation-bands" for c in denied.citations)
    assert any(c.chunk.doc_id == "compensation-bands" for c in allowed.contexts)


def test_stage_timings_are_recorded(system, employee):
    result = system.pipeline.ask("per diem Amsterdam", employee)
    timings = result.answer.stage_timings_ms
    assert {"retrieve.dense", "retrieve.lexical", "retrieve.fuse"} <= set(timings)
    assert result.debug["total_ms"] > 0


def test_streaming_pipeline_emits_contexts_then_tokens_then_done(system, employee):
    events = list(system.pipeline.stream("expense claim deadline", employee))
    kinds = [k for k, _ in events]
    assert kinds[0] == "context"
    assert "token" in kinds
    assert kinds[-1] == "done"


# ---- service ---------------------------------------------------------------


@pytest.fixture
def client(system):
    return TestClient(create_app(system=system))


def test_healthz_reports_index_state(client):
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["chunks"] > 0


def test_ask_returns_answer_and_citations(client):
    response = client.post(
        "/ask",
        json={"question": "How long do I have to submit an expense claim?"},
        headers={"x-tenant-id": "acme", "x-groups": "engineering"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answer"]
    assert body["citations"]
    assert body["trace_id"]


def test_ask_rejects_an_empty_question(client):
    assert client.post("/ask", json={"question": ""}).status_code == 422


def test_ask_rejects_an_unknown_visibility_header(client):
    response = client.post(
        "/ask", json={"question": "hi there"}, headers={"x-max-visibility": "cosmic"}
    )
    assert response.status_code == 400


def test_headers_scope_access(client):
    question = {"question": "What is the salary band for a Staff Engineer?"}
    plain = client.post("/ask", json=question, headers={"x-groups": "engineering"}).json()
    privileged = client.post(
        "/ask",
        json=question,
        headers={"x-groups": "finance", "x-max-visibility": "confidential"},
    ).json()

    assert all("comp-bands" not in c["uri"] for c in plain["citations"])
    assert any("comp-bands" in c["uri"] for c in privileged["citations"])


def test_stream_endpoint_emits_sse_events(client):
    with client.stream(
        "POST", "/ask/stream", json={"question": "per diem for Amsterdam"}
    ) as response:
        payload = "".join(chunk for chunk in response.iter_text())
    assert "event: context" in payload
    assert "event: token" in payload
    assert "event: done" in payload
    done_line = [ln for ln in payload.splitlines() if ln.startswith("data:")][-1]
    assert "answer" in json.loads(done_line[len("data:") :])


def test_metrics_endpoint_exposes_counters(client):
    client.post("/ask", json={"question": "expense deadline"})
    body = client.get("/metrics").text
    assert "ragx_retrieval_requests_total" in body
    assert "ragx_ask_latency_ms" in body


def test_documents_can_be_deleted_through_the_api(client, system):
    before = len(system.vector_store)
    response = client.request("DELETE", "/documents", json={"doc_ids": ["oncall-rotation"]})
    assert response.status_code == 200
    assert len(system.vector_store) < before


# ---- eval harness ----------------------------------------------------------


def test_retrieval_metric_math():
    retrieved = ["a", "b", "c", "d"]
    assert recall_at_k(retrieved, ["a", "z"], 4) == 0.5
    assert reciprocal_rank(retrieved, ["b"]) == 0.5
    assert ndcg_at_k(retrieved, ["a"], 4) == 1.0
    assert ndcg_at_k(retrieved, ["d"], 4) < 0.5


def test_golden_set_loads_and_covers_the_required_case_kinds(evalset_path):
    cases = load_cases(evalset_path)
    tags = {tag for case in cases for tag in case.tags}
    assert {"multi-hop", "unanswerable", "permission", "conflict"} <= tags
    assert any(c.unanswerable for c in cases)
    assert all(c.reference_answer for c in cases)


def test_eval_runner_produces_a_scored_report(system, evalset_path):
    cases = load_cases(evalset_path)
    report = run_eval(system, cases, thresholds=Thresholds())
    summary = report.summary()

    assert summary["cases"] == len(cases)
    assert 0.0 <= summary["recall@candidates"] <= 1.0
    assert summary["permission_leaks"] == 0
    assert "recall@final" in report.to_markdown()
    assert json.loads(report.to_json())["summary"] == summary


def test_retrieval_recall_is_high_on_the_golden_set(system, evalset_path):
    """Guards the retrieval stage itself: the offline embedder is weak, but
    hybrid retrieval plus BM25 should still surface the right document."""
    cases = [c for c in load_cases(evalset_path) if not c.unanswerable]
    report = run_eval(system, cases)
    assert report.summary()["recall@candidates"] >= 0.85


def test_permission_leak_is_detected_when_acls_are_bypassed(corpus_dir, evalset_path):
    """If someone 'simplifies' the access filter away, the eval must fail."""
    sys_ = build(Config(), offline=True)
    sys_.ingest_directory(corpus_dir, tenant_id="acme")

    # Simulate the regression: every principal is treated as fully privileged.
    from ragx.index import vector_store as vs

    original = vs.AccessFilter.matches
    vs.AccessFilter.matches = lambda self, chunk: chunk.tenant_id == self.tenant_id
    try:
        cases = [c for c in load_cases(evalset_path) if "permission" in c.tags]
        report = run_eval(sys_, cases)
    finally:
        vs.AccessFilter.matches = original

    assert report.summary()["permission_leaks"] > 0
    assert any("permission_leaks" in f for f in report.failures())


def test_judge_scoring_shapes(system, evalset_path):
    from ragx.eval.judge import Judge

    def handler(model: str, prompt: str) -> str:
        if "Split the answer into its factual claims" in prompt:
            return json.dumps(
                {
                    "claims": [
                        {"claim": "x", "verdict": "supported", "reason": "stated"},
                        {"claim": "y", "verdict": "unsupported", "reason": "absent"},
                    ]
                }
            )
        return json.dumps({"verdict": "partial", "reason": "missing a fact"})

    judge = Judge(ScriptedLLM(handler), "claude-opus-5")
    employee = Principal(tenant_id="acme", subject_id="u", groups=frozenset({"engineering"}))
    answer = system.pipeline.ask("expense claim deadline", employee).answer

    faith = judge.faithfulness(answer)
    assert faith.score == 0.5
    assert judge.correctness("q", "candidate", "reference").score == 0.5


def test_judge_does_not_call_the_model_for_an_uncited_answer():
    from ragx.eval.judge import Judge
    from ragx.types import Answer

    calls: list[str] = []
    judge = Judge(ScriptedLLM(lambda m, p: calls.append(p) or "{}"), "claude-opus-5")
    result = judge.faithfulness(
        Answer(text="Some uncited claim about the policy.", citations=(), contexts=())
    )
    assert calls == []
    assert result.score == 0.0


def test_visibility_enum_round_trips():
    assert Visibility("confidential") is Visibility.CONFIDENTIAL


def test_retrieval_gate_selects_only_retrieval_failures():
    """CI runs offline, where generation metrics are meaningless. The gate must
    still fail on a real retrieval or permission regression."""
    from ragx.eval.runner import gated_failures

    failures = [
        "recall@final 0.4 < 0.75",
        "permission_leaks 2 > 0",
        "grounding 0.6 < 0.9",
        "abstention_recall 0.0 < 0.8",
    ]
    assert gated_failures(failures, "all") == failures
    assert gated_failures(failures, "retrieval") == [
        "recall@final 0.4 < 0.75",
        "permission_leaks 2 > 0",
    ]
    with pytest.raises(ValueError):
        gated_failures(failures, "nonsense")


def test_offline_eval_meets_its_retrieval_thresholds(system, evalset_path):
    """The gate is only meaningful if the current system passes it."""
    from ragx.eval.runner import gated_failures

    report = run_eval(system, load_cases(evalset_path), thresholds=Thresholds())
    assert gated_failures(report.failures(), "retrieval") == []
