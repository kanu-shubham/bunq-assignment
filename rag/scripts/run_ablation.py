#!/usr/bin/env python3
"""Run the retrieval ablation and print the tables the README reports.

    python scripts/run_ablation.py                 # test split (headline)
    python scripts/run_ablation.py --split dev     # tuning split
    python scripts/run_ablation.py --split all
    python scripts/run_ablation.py --failures      # show what is still missed
    python scripts/run_ablation.py --json out.json

One change per rung of the ladder, so each delta is attributable to exactly one
component. That is the entire point of the exercise.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ragkit import (  # noqa: E402
    PipelineConfig,
    RagPipeline,
    corpus_stats,
    default_ablation,
    format_category_table,
    format_table,
    load_corpus,
    load_queries,
    paired_bootstrap,
    run_ablation,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus", default=str(ROOT / "data" / "corpus"))
    parser.add_argument("--queries", default=str(ROOT / "data" / "eval" / "queries.jsonl"))
    parser.add_argument("--split", default="test", choices=["dev", "test", "all"])
    parser.add_argument("--top-k", type=int, default=10, help="retrieval depth handed to metrics")
    parser.add_argument("--candidate-k", type=int, default=50)
    parser.add_argument("--rerank-depth", type=int, default=30)
    parser.add_argument("--failures", action="store_true", help="list queries still missed at k=5")
    parser.add_argument("--json", dest="json_out", default=None)
    args = parser.parse_args()

    docs = load_corpus(args.corpus)
    queries = load_queries(args.queries, split=None if args.split == "all" else args.split)

    print("=" * 78)
    print("CORPUS")
    print("=" * 78)
    for key, value in corpus_stats(docs).items():
        print(f"  {key:<14} {value}")

    base = PipelineConfig(
        top_k=args.top_k,
        candidate_k=args.candidate_k,
        rerank_depth=args.rerank_depth,
    )

    started = time.perf_counter()
    pipeline = RagPipeline.build(docs, base)
    build_ms = (time.perf_counter() - started) * 1000
    print()
    for key, value in pipeline.stats().items():
        print(f"  {key:<14} {value}")
    print(f"  {'index_build_ms':<14} {build_ms:.0f}")

    print()
    print("=" * 78)
    print(f"ABLATION  ({args.split} split, {len(queries)} queries)")
    print("=" * 78)
    print()

    reports = run_ablation(pipeline, queries, default_ablation(base))
    print(format_table(reports))

    print()
    print("Recall@5 by query category")
    print()
    print(format_category_table(reports, k=5))

    print()
    print("Deltas vs. the previous rung (recall@5 / recall@10 / MRR):")
    for prev, cur in zip(reports, reports[1:]):
        d5 = cur.metrics["recall@5"] - prev.metrics["recall@5"]
        d10 = cur.metrics["recall@10"] - prev.metrics["recall@10"]
        dm = cur.metrics["mrr"] - prev.metrics["mrr"]
        print(f"  {cur.label:<32} {d5:+.3f}  {d10:+.3f}  {dm:+.3f}")

    print()
    print("Paired bootstrap vs. the previous rung, 95% CI on the delta (5000 resamples).")
    print("An interval that straddles zero means the change is not distinguishable from noise.")
    for metric in ("recall@5", "mrr"):
        print(f"\n  {metric}")
        for prev, cur in zip(reports, reports[1:]):
            bs = paired_bootstrap(prev, cur, metric)
            verdict = "significant" if bs["significant"] else "not distinguishable from noise"
            print(
                f"    {cur.label:<32} {bs['delta']:+.3f}  "
                f"[{bs['ci_low']:+.3f}, {bs['ci_high']:+.3f}]  {verdict}"
            )

    first, last = reports[0], reports[-1]
    print()
    print(
        f"End to end: recall@5 {first.metrics['recall@5']:.3f} -> {last.metrics['recall@5']:.3f} "
        f"({last.metrics['recall@5'] - first.metrics['recall@5']:+.3f}), "
        f"MRR {first.metrics['mrr']:.3f} -> {last.metrics['mrr']:.3f}, "
        f"p50 {first.latency_ms_p50:.1f}ms -> {last.latency_ms_p50:.1f}ms"
    )

    if args.failures:
        print()
        print("=" * 78)
        print("REMAINING FAILURES (no gold document in top-5, best configuration)")
        print("=" * 78)
        misses = last.failures(5)
        if not misses:
            print("  none")
        for outcome in misses:
            print(f"\n  [{outcome.query.category}] {outcome.query.query_id}: {outcome.query.text}")
            print(f"    gold      : {sorted(outcome.query.gold_doc_ids)}")
            print(f"    retrieved : {outcome.retrieved_docs[:5]}")
            rank = outcome.found_rank
            print(f"    gold rank : {rank if rank else 'not in top-10'}")

    if args.json_out:
        payload = {
            "split": args.split,
            "n_queries": len(queries),
            "corpus": corpus_stats(docs),
            "pipeline": pipeline.stats(),
            "reports": [
                {
                    "label": r.label,
                    "metrics": r.metrics,
                    "by_category": r.by_category,
                    "p50_ms": round(r.latency_ms_p50, 2),
                    "p95_ms": round(r.latency_ms_p95, 2),
                }
                for r in reports
            ],
        }
        pathlib.Path(args.json_out).write_text(json.dumps(payload, indent=2))
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
