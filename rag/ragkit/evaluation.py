"""Retrieval evaluation.

The number that matters is **recall@k**, and it matters because of what sits
downstream: the generator can only be grounded in what retrieval handed it. If
the answer is not in the top-k, no amount of prompt engineering produces a
correct grounded answer — it produces a confident wrong one. Every other metric
here is diagnostic; recall@k is the constraint.

Definitions used throughout, stated because they vary between papers and
libraries and a comparison against an undefined "recall" is worthless:

``recall@k``     Take the top-k *chunks*, map them to their documents. Score is
                 |gold ∩ those documents| / |gold|, averaged over queries. This
                 measures what actually reaches the model's context — not top-k
                 documents, because k chunks may come from fewer than k
                 documents, and that collapse is a real cost of chunking.
``hit@k``        1.0 if any gold document is in that set. The metric to watch
                 for single-answer questions.
``mrr``          Reciprocal rank of the first gold document.
``ndcg@k``       Binary-relevance nDCG; rewards putting gold near the top.
``precision@k``  Fraction of retrieved documents that are gold. Falls as k
                 grows and is mostly useful for context-budget arguments.
"""

from __future__ import annotations

import json
import math
import pathlib
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .filters import MetadataFilter
from .pipeline import PipelineConfig, RagPipeline
from .types import EvalQuery

DEFAULT_KS: tuple[int, ...] = (1, 3, 5, 10)


def load_queries(path: str | pathlib.Path, *, split: str | None = None) -> list[EvalQuery]:
    """Load a JSONL eval set, optionally restricted to one split."""
    path = pathlib.Path(path)
    queries: list[EvalQuery] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: {exc}") from exc
        if split and row.get("split") != split:
            continue
        queries.append(
            EvalQuery(
                query_id=row["query_id"],
                text=row["query"],
                gold_doc_ids=frozenset(row["gold_doc_ids"]),
                category=row.get("category", "general"),
                filters=row.get("filters") or {},
                note=row.get("note", ""),
            )
        )
    if not queries:
        raise ValueError(f"no queries loaded from {path} (split={split!r})")
    return queries


# ------------------------------------------------------------------ metrics


def recall_at_k(retrieved_docs: Sequence[str], gold: Iterable[str], k: int) -> float:
    gold = set(gold)
    if not gold:
        return 0.0
    return len(gold & set(retrieved_docs[:k])) / len(gold)


def hit_at_k(retrieved_docs: Sequence[str], gold: Iterable[str], k: int) -> float:
    return 1.0 if set(gold) & set(retrieved_docs[:k]) else 0.0


def precision_at_k(retrieved_docs: Sequence[str], gold: Iterable[str], k: int) -> float:
    top = retrieved_docs[:k]
    if not top:
        return 0.0
    return len(set(gold) & set(top)) / len(top)


def reciprocal_rank(retrieved_docs: Sequence[str], gold: Iterable[str]) -> float:
    gold = set(gold)
    for i, doc in enumerate(retrieved_docs, start=1):
        if doc in gold:
            return 1.0 / i
    return 0.0


def ndcg_at_k(retrieved_docs: Sequence[str], gold: Iterable[str], k: int) -> float:
    gold = set(gold)
    dcg = sum(
        1.0 / math.log2(i + 1)
        for i, doc in enumerate(retrieved_docs[:k], start=1)
        if doc in gold
    )
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(gold), k) + 1))
    return dcg / ideal if ideal else 0.0


# ---------------------------------------------------------------- evaluation


@dataclass
class QueryOutcome:
    query: EvalQuery
    retrieved_docs: list[str]
    latency_ms: float
    n_variants: int
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def found_rank(self) -> int | None:
        for i, doc in enumerate(self.retrieved_docs, start=1):
            if doc in self.query.gold_doc_ids:
                return i
        return None


@dataclass
class EvalReport:
    label: str
    metrics: dict[str, float]
    by_category: dict[str, dict[str, float]]
    outcomes: list[QueryOutcome]
    latency_ms_p50: float
    latency_ms_p95: float
    n_queries: int

    def failures(self, k: int = 5) -> list[QueryOutcome]:
        """Queries with no gold document in the top-k. The interesting ones."""
        return [o for o in self.outcomes if not hit_at_k(o.retrieved_docs, o.query.gold_doc_ids, k)]

    def summary_row(self, ks: Sequence[int] = DEFAULT_KS) -> dict[str, Any]:
        row: dict[str, Any] = {"config": self.label}
        for k in ks:
            row[f"recall@{k}"] = self.metrics[f"recall@{k}"]
        row["mrr"] = self.metrics["mrr"]
        row["ndcg@10"] = self.metrics["ndcg@10"]
        row["p50_ms"] = round(self.latency_ms_p50, 1)
        return row


def evaluate(
    pipeline: RagPipeline,
    queries: Sequence[EvalQuery],
    *,
    ks: Sequence[int] = DEFAULT_KS,
    label: str | None = None,
    respect_filters: bool = True,
) -> EvalReport:
    ks = tuple(sorted(set(ks) | {10}))
    max_k = max(ks)
    outcomes: list[QueryOutcome] = []

    for query in queries:
        mfilter = MetadataFilter.parse(query.filters) if (respect_filters and query.filters) else None
        started = time.perf_counter()
        result = pipeline.retrieve(query.text, max_k, metadata_filter=mfilter)
        latency = (time.perf_counter() - started) * 1000

        # Documents in the order their best chunk appeared, truncated per k
        # inside each metric so that recall@1 means "the single best chunk".
        doc_rank: list[str] = []
        for sc in result.chunks:
            if sc.doc_id not in doc_rank:
                doc_rank.append(sc.doc_id)

        per_k_docs = {}
        for k in ks:
            seen: list[str] = []
            for sc in result.chunks[:k]:
                if sc.doc_id not in seen:
                    seen.append(sc.doc_id)
            per_k_docs[k] = seen

        metrics: dict[str, float] = {}
        for k in ks:
            docs_k = per_k_docs[k]
            metrics[f"recall@{k}"] = recall_at_k(docs_k, query.gold_doc_ids, k)
            metrics[f"hit@{k}"] = hit_at_k(docs_k, query.gold_doc_ids, k)
            metrics[f"precision@{k}"] = precision_at_k(docs_k, query.gold_doc_ids, k)
            metrics[f"ndcg@{k}"] = ndcg_at_k(docs_k, query.gold_doc_ids, k)
        metrics["mrr"] = reciprocal_rank(doc_rank, query.gold_doc_ids)

        outcomes.append(
            QueryOutcome(
                query=query,
                retrieved_docs=doc_rank,
                latency_ms=latency,
                n_variants=int(result.trace.get("n_variants", 1)),
                metrics=metrics,
            )
        )

    keys = sorted(outcomes[0].metrics) if outcomes else []
    aggregate = {
        key: round(statistics.fmean(o.metrics[key] for o in outcomes), 4) for key in keys
    }

    by_category: dict[str, dict[str, float]] = {}
    for category in sorted({o.query.category for o in outcomes}):
        subset = [o for o in outcomes if o.query.category == category]
        by_category[category] = {
            key: round(statistics.fmean(o.metrics[key] for o in subset), 4) for key in keys
        }
        by_category[category]["n"] = len(subset)

    latencies = sorted(o.latency_ms for o in outcomes)
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]

    return EvalReport(
        label=label or pipeline.config.label(),
        metrics=aggregate,
        by_category=by_category,
        outcomes=outcomes,
        latency_ms_p50=p50,
        latency_ms_p95=p95,
        n_queries=len(outcomes),
    )


def paired_bootstrap(
    baseline: EvalReport,
    candidate: EvalReport,
    metric: str = "recall@5",
    *,
    iterations: int = 5000,
    seed: int = 0,
) -> dict[str, float]:
    """Paired bootstrap over queries for the delta between two configurations.

    A point estimate on 43 queries is not evidence. Resampling the *same*
    queries for both configurations (paired, because the queries are identical)
    gives a confidence interval on the difference, and the interval is usually
    the thing that ends the argument: on an eval set this size a two-point
    change is indistinguishable from noise, so a component that moves recall by
    two points has not been shown to do anything at all.

    Returns the observed delta, a 95% interval, and the fraction of resamples
    in which the candidate beat the baseline.
    """
    import random

    if [o.query.query_id for o in baseline.outcomes] != [o.query.query_id for o in candidate.outcomes]:
        raise ValueError("reports must cover the same queries in the same order")

    deltas = [
        c.metrics[metric] - b.metrics[metric]
        for b, c in zip(baseline.outcomes, candidate.outcomes)
    ]
    n = len(deltas)
    observed = statistics.fmean(deltas)

    rng = random.Random(seed)
    samples = []
    for _ in range(iterations):
        resample = [deltas[rng.randrange(n)] for _ in range(n)]
        samples.append(statistics.fmean(resample))
    samples.sort()
    lo = samples[int(0.025 * iterations)]
    hi = samples[int(0.975 * iterations) - 1]
    wins = sum(1 for s in samples if s > 0) / iterations

    return {
        "metric": metric,
        "delta": round(observed, 4),
        "ci_low": round(lo, 4),
        "ci_high": round(hi, 4),
        "p_better": round(wins, 3),
        "n": n,
        "significant": bool(lo > 0 or hi < 0),
    }


@dataclass
class AblationStep:
    label: str
    config: PipelineConfig
    note: str = ""


def run_ablation(
    pipeline: RagPipeline,
    queries: Sequence[EvalQuery],
    steps: Sequence[AblationStep],
    *,
    ks: Sequence[int] = DEFAULT_KS,
) -> list[EvalReport]:
    """Evaluate a sequence of configurations against one prebuilt index."""
    reports: list[EvalReport] = []
    for step in steps:
        variant = pipeline.with_config(step.config)
        reports.append(evaluate(variant, queries, ks=ks, label=step.label))
    return reports


def default_ablation(base: PipelineConfig | None = None) -> list[AblationStep]:
    """The ladder the README reports: one change per rung.

    Each step differs from the one above it in exactly one flag, which is what
    makes the delta attributable. An ablation that changes two things at once
    tells you the pair helped and nothing more.
    """
    base = base or PipelineConfig()

    def cfg(**overrides) -> PipelineConfig:
        return PipelineConfig(
            top_k=base.top_k,
            candidate_k=base.candidate_k,
            rerank_depth=base.rerank_depth,
            max_per_doc=base.max_per_doc,
            chunk=base.chunk,
            **overrides,
        )

    return [
        AblationStep("1. BM25 only", cfg(fusion="bm25_only", use_rewrite=False, use_rerank=False),
                     "sparse lexical baseline"),
        AblationStep("2. Dense only", cfg(fusion="dense_only", use_rewrite=False, use_rerank=False),
                     "vector baseline"),
        AblationStep("3. Hybrid (RRF)", cfg(fusion="rrf", use_rewrite=False, use_rerank=False),
                     "+ rank fusion"),
        AblationStep("4. Hybrid + rerank", cfg(fusion="rrf", use_rewrite=False, use_rerank=True),
                     "+ cross-encoder"),
        AblationStep("5. Hybrid + rerank + rewrite", cfg(fusion="rrf", use_rewrite=True, use_rerank=True),
                     "+ query rewriting (full pipeline)"),
        # Off the main ladder: rewriting without re-ranking, so the contribution
        # of each of the last two stages is attributable on its own rather than
        # only in combination.
        AblationStep("6. Hybrid + rewrite (no rerank)", cfg(fusion="rrf", use_rewrite=True, use_rerank=False),
                     "isolates query rewriting"),
    ]


def format_table(reports: Sequence[EvalReport], ks: Sequence[int] = DEFAULT_KS) -> str:
    """Markdown table of the headline metrics."""
    ks = tuple(sorted(set(ks)))
    headers = ["configuration", *[f"recall@{k}" for k in ks], "MRR", "nDCG@10", "p50 ms"]
    rows = [
        [
            r.label,
            *[f"{r.metrics[f'recall@{k}']:.3f}" for k in ks],
            f"{r.metrics['mrr']:.3f}",
            f"{r.metrics['ndcg@10']:.3f}",
            f"{r.latency_ms_p50:.1f}",
        ]
        for r in reports
    ]
    widths = [max(len(h), *(len(row[i]) for row in rows)) for i, h in enumerate(headers)]
    def line(cells: Sequence[str]) -> str:
        return "| " + " | ".join(c.ljust(w) for c, w in zip(cells, widths)) + " |"
    out = [line(headers), "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    out.extend(line(row) for row in rows)
    return "\n".join(out)


def format_category_table(reports: Sequence[EvalReport], k: int = 5) -> str:
    """Per-category recall@k across configurations — where the story actually is."""
    categories = sorted({c for r in reports for c in r.by_category})
    headers = ["configuration", *categories]
    rows = []
    for r in reports:
        row = [r.label]
        for c in categories:
            stats = r.by_category.get(c)
            row.append(f"{stats[f'recall@{k}']:.3f}" if stats else "-")
        rows.append(row)
    widths = [max(len(h), *(len(row[i]) for row in rows)) for i, h in enumerate(headers)]
    def line(cells: Sequence[str]) -> str:
        return "| " + " | ".join(c.ljust(w) for c, w in zip(cells, widths)) + " |"
    out = [line(headers), "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    out.extend(line(row) for row in rows)
    return "\n".join(out)
