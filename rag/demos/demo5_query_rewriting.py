#!/usr/bin/env python3
"""Demo 5 — Query rewriting.

Shows the variants each strategy produces, what they cost, and — measured on the
paraphrase subset, which is the only place rewriting can possibly help — what
they buy.

    python demos/demo5_query_rewriting.py
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ragkit import (  # noqa: E402
    PipelineConfig,
    RagPipeline,
    RuleBasedRewriter,
    load_corpus,
    load_queries,
    paired_bootstrap,
)
from ragkit.evaluation import evaluate  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


def rule(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def main() -> int:
    docs = load_corpus(ROOT / "data" / "corpus")
    all_queries = load_queries(ROOT / "data" / "eval" / "queries.jsonl")
    test = load_queries(ROOT / "data" / "eval" / "queries.jsonl", split="test")
    base = PipelineConfig(top_k=10, candidate_k=50, rerank_depth=30)
    pipeline = RagPipeline.build(docs, base)
    rewriter = RuleBasedRewriter()

    rule("What the strategies produce")
    samples = [
        "how many holiday days do I get each year",
        "a customer wants all their data deleted",
        "who gets woken up when something breaks at night",
        "what is the payments rate limit and how do I roll back a deploy",
        "ERR_CURRENCY_MISMATCH",
    ]
    for query in samples:
        result = rewriter.rewrite(query)
        print(f"\n  {query!r}")
        if not result.variants:
            print("    (no rewrite — nothing in the lexicon matched and no scaffolding to strip)")
        for strategy, produced in result.strategy_trace.items():
            for variant in produced:
                print(f"    {strategy:<10} -> {variant}")

    print(
        "\n  Note the last one. A bare error code needs no rewriting and gets none —\n"
        "  rewriting an already-precise query only adds latency and dilutes the\n"
        "  ranking. The strategies are conditional on the query, not unconditional."
    )

    rule("The failure rewriting exists to fix")
    off = pipeline.with_config(
        PipelineConfig(top_k=10, candidate_k=50, rerank_depth=30, use_rewrite=False, use_rerank=False)
    )
    on = pipeline.with_config(
        PipelineConfig(top_k=10, candidate_k=50, rerank_depth=30, use_rewrite=True, use_rerank=False)
    )

    def rank_of(chunks, gold):
        seen = []
        for sc in chunks:
            if sc.doc_id not in seen:
                seen.append(sc.doc_id)
        for i, d in enumerate(seen, start=1):
            if d in gold:
                return i
        return None

    print(f"  {'query':<50} {'off':>5} {'on':>5}")
    fixed = 0
    for q in all_queries:
        if q.category != "paraphrase":
            continue
        gold = set(q.gold_doc_ids)
        a = rank_of(off.retrieve(q.text, 10).chunks, gold)
        b = rank_of(on.retrieve(q.text, 10).chunks, gold)
        if a != b:
            f = lambda r: str(r) if r else "-"  # noqa: E731
            marker = "  <- fixed" if (b or 99) < (a or 99) else ""
            if (b or 99) < (a or 99):
                fixed += 1
            print(f"  {q.text[:48]:<50} {f(a):>5} {f(b):>5}{marker}")
    print(f"\n  {fixed} paraphrase queries improved by rewriting.")

    rule("Cost")
    q = "a customer wants all their data deleted"
    r_off = off.retrieve(q, 10)
    r_on = on.retrieve(q, 10)
    print(f"  variants issued     {len(r_off.rewritten_queries)} -> {len(r_on.rewritten_queries)}")
    print(f"  retrieval time      {r_off.trace['retrieve_ms']:.2f} ms -> {r_on.trace['retrieve_ms']:.2f} ms")
    print(f"  total latency       {r_off.trace['latency_ms']:.2f} ms -> {r_on.trace['latency_ms']:.2f} ms")
    print(
        "\n  Each variant is a full pass over both indexes. Rewriting multiplies\n"
        "  first-stage cost by the number of variants — which is exactly why the\n"
        "  next demo (semantic caching) is worth building."
    )

    rule("Measured on the held-out test split")
    rep_off = evaluate(off, test, label="no rewrite")
    rep_on = evaluate(on, test, label="rule-based rewrite")
    print(f"  {'configuration':<22} {'r@1':>6} {'r@3':>6} {'r@5':>6} {'r@10':>6} {'MRR':>6} {'p50 ms':>8}")
    for rep in (rep_off, rep_on):
        m = rep.metrics
        print(f"  {rep.label:<22} {m['recall@1']:>6.3f} {m['recall@3']:>6.3f} {m['recall@5']:>6.3f} "
              f"{m['recall@10']:>6.3f} {m['mrr']:>6.3f} {rep.latency_ms_p50:>8.1f}")

    print()
    for metric in ("recall@5", "recall@10", "mrr"):
        bs = paired_bootstrap(rep_off, rep_on, metric)
        verdict = "SIGNIFICANT" if bs["significant"] else "not distinguishable from noise"
        print(f"  {metric:<10} {bs['delta']:+.3f}  [{bs['ci_low']:+.3f}, {bs['ci_high']:+.3f}]  {verdict}")

    print(
        "\n  Rewriting helps on the queries it is designed for and does nothing on the\n"
        "  rest, so its effect on a mixed eval set is diluted to the point of being\n"
        "  hard to detect. Two honest consequences: report per-category numbers, not\n"
        "  just the aggregate, and route conditionally — rewriting a query that\n"
        "  already retrieves well is pure cost."
    )

    rule("Per-category recall@5")
    cats = sorted(rep_off.by_category)
    print(f"  {'category':<16} {'off':>8} {'on':>8}")
    for c in cats:
        a = rep_off.by_category[c]["recall@5"]
        b = rep_on.by_category[c]["recall@5"]
        flag = "  *" if b > a else ("  v" if b < a else "")
        print(f"  {c:<16} {a:>8.3f} {b:>8.3f}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
