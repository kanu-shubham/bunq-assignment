"""Evaluation harness.

Runs the full pipeline over the golden set, scores each stage separately, and
returns a report that either passes or fails a set of thresholds. The stage
separation is the point: a single "answer quality" number tells you the system
got worse, not which of the four things to fix.

What it measures
----------------
retrieval      recall@candidates, recall@final, nDCG@final, MRR (doc-level)
grounding      deterministic: fraction of claim sentences with a valid citation,
               plus hallucinated-marker count
behaviour      abstention recall on unanswerable cases, false-abstention rate on
               answerable ones, and permission leakage (answered when the
               principal was not allowed to see the source)
faithfulness   LLM judge, per claim, against cited passages only (optional)
correctness    LLM judge against the reference answer (optional)
cost/latency   tokens, USD, per-stage wall time

Run it in CI on every change to a prompt, a chunking parameter, a fusion weight,
or a model id. Those are the four things that move quality and none of them is
covered by unit tests.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..factory import RagSystem
from ..index.vector_store import AccessFilter
from ..obs import metrics
from ..obs.trace import Timings
from ..types import Answer, EvalCase, Principal, dedupe_preserving_order
from .judge import Judge
from .retrieval_metrics import RetrievalScores, mean, score_case

# Metrics that are meaningful without live model calls. An offline CI job gates
# on these; generation metrics need `--live --judge` and are reported, not gated.
RETRIEVAL_GATED_METRICS = frozenset(
    {"recall@candidates", "recall@final", "ndcg@final", "mrr", "permission_leaks"}
)


def gated_failures(failures: Sequence[str], gate: str) -> list[str]:
    """Select which threshold failures should fail the process."""
    if gate == "all":
        return list(failures)
    if gate == "retrieval":
        return [f for f in failures if f.split()[0] in RETRIEVAL_GATED_METRICS]
    raise ValueError(f"unknown gate: {gate}")


@dataclass(frozen=True, slots=True)
class Thresholds:
    """CI gates. Set from a measured baseline, not from aspiration.

    Ratchet them up as the system improves; a threshold that has never failed is
    not protecting anything.
    """

    min_recall_at_candidates: float = 0.85
    min_recall_at_final: float = 0.75
    min_ndcg_at_final: float = 0.60
    min_grounding_score: float = 0.90
    min_abstention_recall: float = 0.80  # unanswerable questions correctly refused
    max_false_abstention: float = 0.15  # answerable questions wrongly refused
    max_permission_leaks: int = 0  # non-negotiable
    min_faithfulness: float = 0.90  # judge, only enforced when a judge is used
    min_correctness: float = 0.70


@dataclass
class CaseResult:
    case_id: str
    question: str
    unanswerable: bool
    tags: tuple[str, ...]
    retrieval: RetrievalScores | None
    answer: Answer
    latency_ms: float
    faithfulness: float | None = None
    correctness: float | None = None
    permission_leak: bool = False

    @property
    def abstained(self) -> bool:
        return self.answer.abstained


@dataclass
class EvalReport:
    results: list[CaseResult] = field(default_factory=list)
    thresholds: Thresholds = field(default_factory=Thresholds)
    config_snapshot: dict = field(default_factory=dict)

    # ---- aggregates ----------------------------------------------------

    @property
    def answerable(self) -> list[CaseResult]:
        return [r for r in self.results if not r.unanswerable]

    @property
    def unanswerable(self) -> list[CaseResult]:
        return [r for r in self.results if r.unanswerable]

    def summary(self) -> dict[str, float | int]:
        answerable = self.answerable
        retrieval = [r.retrieval for r in answerable if r.retrieval is not None]
        faith = [r.faithfulness for r in answerable if r.faithfulness is not None]
        correct = [r.correctness for r in answerable if r.correctness is not None]
        latencies = sorted(r.latency_ms for r in self.results)

        def pct(q: float) -> float:
            if not latencies:
                return 0.0
            return latencies[min(len(latencies) - 1, int(round(q * (len(latencies) - 1))))]

        return {
            "cases": len(self.results),
            "recall@candidates": round(mean([s.recall_at_candidates for s in retrieval]), 4),
            "recall@final": round(mean([s.recall_at_final for s in retrieval]), 4),
            "ndcg@final": round(mean([s.ndcg_at_final for s in retrieval]), 4),
            "mrr": round(mean([s.mrr for s in retrieval]), 4),
            "grounding": round(
                mean([r.answer.grounding_score for r in answerable if not r.abstained]), 4
            ),
            "abstention_recall": round(
                mean([1.0 if r.abstained else 0.0 for r in self.unanswerable]), 4
            ),
            "false_abstention": round(
                mean([1.0 if r.abstained else 0.0 for r in answerable]), 4
            ),
            "permission_leaks": sum(1 for r in self.results if r.permission_leak),
            "faithfulness": round(mean(faith), 4) if faith else -1.0,
            "correctness": round(mean(correct), 4) if correct else -1.0,
            "latency_p50_ms": round(pct(0.5), 1),
            "latency_p95_ms": round(pct(0.95), 1),
            "total_input_tokens": sum(r.answer.usage.input_tokens for r in self.results),
            "total_output_tokens": sum(r.answer.usage.output_tokens for r in self.results),
            "estimated_cost_usd": round(
                sum(
                    metrics.estimate_cost_usd(
                        r.answer.usage.input_tokens,
                        r.answer.usage.output_tokens,
                        r.answer.usage.cache_read_input_tokens,
                    )
                    for r in self.results
                ),
                4,
            ),
        }

    def failures(self) -> list[str]:
        s = self.summary()
        t = self.thresholds
        checks: list[tuple[str, bool]] = [
            (f"recall@candidates {s['recall@candidates']} < {t.min_recall_at_candidates}",
             s["recall@candidates"] < t.min_recall_at_candidates),
            (f"recall@final {s['recall@final']} < {t.min_recall_at_final}",
             s["recall@final"] < t.min_recall_at_final),
            (f"ndcg@final {s['ndcg@final']} < {t.min_ndcg_at_final}",
             s["ndcg@final"] < t.min_ndcg_at_final),
            (f"grounding {s['grounding']} < {t.min_grounding_score}",
             s["grounding"] < t.min_grounding_score),
            (f"abstention_recall {s['abstention_recall']} < {t.min_abstention_recall}",
             bool(self.unanswerable) and s["abstention_recall"] < t.min_abstention_recall),
            (f"false_abstention {s['false_abstention']} > {t.max_false_abstention}",
             s["false_abstention"] > t.max_false_abstention),
            (f"permission_leaks {s['permission_leaks']} > {t.max_permission_leaks}",
             s["permission_leaks"] > t.max_permission_leaks),
            (f"faithfulness {s['faithfulness']} < {t.min_faithfulness}",
             s["faithfulness"] >= 0 and s["faithfulness"] < t.min_faithfulness),
            (f"correctness {s['correctness']} < {t.min_correctness}",
             s["correctness"] >= 0 and s["correctness"] < t.min_correctness),
        ]
        return [message for message, failed in checks if failed]

    @property
    def passed(self) -> bool:
        return not self.failures()

    def to_json(self) -> str:
        return json.dumps(
            {
                "summary": self.summary(),
                "failures": self.failures(),
                "config": self.config_snapshot,
                "cases": [
                    {
                        "case_id": r.case_id,
                        "question": r.question,
                        "abstained": r.abstained,
                        "grounding": round(r.answer.grounding_score, 3),
                        "citations": [c.chunk_id for c in r.answer.citations],
                        "retrieval": r.retrieval.as_dict() if r.retrieval else None,
                        "faithfulness": r.faithfulness,
                        "correctness": r.correctness,
                        "permission_leak": r.permission_leak,
                        "latency_ms": round(r.latency_ms, 1),
                    }
                    for r in self.results
                ],
            },
            indent=2,
        )

    def to_markdown(self) -> str:
        s = self.summary()
        lines = ["| metric | value |", "| --- | --- |"]
        lines += [f"| {k} | {v} |" for k, v in s.items()]
        if self.failures():
            lines.append("")
            lines.append("**Threshold failures**")
            lines += [f"- {f}" for f in self.failures()]
        return "\n".join(lines)


def run_eval(
    system: RagSystem,
    cases: Sequence[EvalCase],
    *,
    tenant_id: str = "acme",
    judge: Judge | None = None,
    thresholds: Thresholds | None = None,
) -> EvalReport:
    report = EvalReport(
        thresholds=thresholds or Thresholds(),
        config_snapshot=system.config.to_dict(),
    )
    final_k = system.config.retrieval.final_k

    for case in cases:
        principal = Principal(
            tenant_id=tenant_id,
            subject_id=f"eval:{case.case_id}",
            groups=case.principal_groups,
            max_visibility=case.principal_max_visibility,
        )
        started = time.perf_counter()
        timings = Timings()

        retrieval = system.retriever.retrieve(
            case.question, AccessFilter.for_principal(principal), timings
        )
        candidates = list(retrieval.candidates)
        if system.config.rerank.enabled and candidates:
            contexts = system.reranker.rerank(case.question, candidates, final_k)
        else:
            contexts = candidates[:final_k]

        if contexts:
            answer = system.answerer.answer(case.question, contexts, timings)
        else:
            answer = Answer(
                text="", citations=(), contexts=(), abstained=True, grounding_score=1.0
            )
        latency_ms = (time.perf_counter() - started) * 1000

        scores = None
        if not case.unanswerable and (case.relevant_doc_ids or case.relevant_chunk_ids):
            use_docs = bool(case.relevant_doc_ids)
            candidate_ids = _ids(candidates, use_docs)
            final_ids = _ids(contexts, use_docs)
            relevant = case.relevant_doc_ids if use_docs else case.relevant_chunk_ids
            scores = score_case(candidate_ids, final_ids, relevant, final_k)

        # Deliberately re-implemented instead of calling `AccessFilter`: an
        # eval that checks the system with the system's own filter cannot catch
        # a regression *in* that filter. This oracle looks only at the chunk's
        # own labels and the principal.
        permission_leak = any(
            _violates_access(context.chunk, principal) for context in answer.contexts
        )

        result = CaseResult(
            case_id=case.case_id,
            question=case.question,
            unanswerable=case.unanswerable,
            tags=case.tags,
            retrieval=scores,
            answer=answer,
            latency_ms=latency_ms,
            permission_leak=permission_leak,
        )

        if judge is not None and not case.unanswerable and not answer.abstained:
            result.faithfulness = judge.faithfulness(answer).score
            if case.reference_answer:
                result.correctness = judge.correctness(
                    case.question, answer.text, case.reference_answer
                ).score

        report.results.append(result)

    return report


def _ids(scored, use_docs: bool) -> list[str]:
    """Doc-level ids are deduped, preserving rank order: two chunks from the
    same document are one retrieved document, and counting them twice inflates
    nDCG in proportion to how verbose the document is."""
    ids = [s.chunk.doc_id if use_docs else s.chunk.chunk_id for s in scored]
    if not use_docs:
        return ids
    return dedupe_preserving_order(ids)


def write_report(report: EvalReport, directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "report.json"
    md_path = directory / "report.md"
    json_path.write_text(report.to_json(), encoding="utf-8")
    md_path.write_text(report.to_markdown() + "\n", encoding="utf-8")
    return json_path, md_path


_VISIBILITY_RANK = {"public": 0, "internal": 1, "confidential": 2}


def _violates_access(chunk, principal: Principal) -> bool:
    """Independent access oracle for the eval harness (see `run_eval`)."""
    if chunk.tenant_id != principal.tenant_id:
        return True
    if _VISIBILITY_RANK[chunk.visibility.value] > _VISIBILITY_RANK[principal.max_visibility.value]:
        return True
    if chunk.acl_groups and not (chunk.acl_groups & principal.groups):
        return True
    return False
