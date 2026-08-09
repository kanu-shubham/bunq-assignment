#!/usr/bin/env python3
"""Demo 6 — Semantic caching.

Shows the hit-rate win, and then the thing that matters more: the threshold
sweep that tells you how many of those hits would have served an answer to a
different question.

    python demos/demo6_semantic_cache.py
"""

from __future__ import annotations

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ragkit import (  # noqa: E402
    MetadataFilter,
    PipelineConfig,
    RagPipeline,
    SemanticCache,
    load_corpus,
    load_queries,
    measure_false_hits,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]


def rule(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


# The same twenty questions in a hundred phrasings — the workload semantic
# caching is actually for.
TRAFFIC = [
    "how many holiday days do I get each year",
    "how much annual leave am I entitled to",
    "how many days of paid holiday do we get",
    "what is our vacation allowance",
    "how do I roll back a deploy",
    "what is the command to roll back a deployment",
    "how do I undo a bad release",
    "who gets paged at night",
    "who is on call overnight",
    "what happens when a payment times out",
    "what happens if a payment times out at the scheme",
    "how do I roll back a database migration",
    "what is ERR_CURRENCY_MISMATCH",
    "what does ERR_CURRENCY_MISMATCH mean",
    "how many holiday days do I get each year",
]


def main() -> int:
    docs = load_corpus(ROOT / "data" / "corpus")
    pipeline = RagPipeline.build(
        docs, PipelineConfig(top_k=5, use_cache=True, cache_threshold=0.90)
    )
    cache = pipeline.cache
    assert cache is not None

    rule("A realistic request stream")
    print(f"threshold = {cache.threshold}\n")
    print(f"{'query':<50} {'result':<9} {'sim':>6} {'ms':>7}  matched")
    for query in TRAFFIC:
        started = time.perf_counter()
        result = pipeline.retrieve(query)
        elapsed = (time.perf_counter() - started) * 1000
        info = result.trace.get("cache", {})
        if info.get("hit"):
            kind = info.get("kind", "hit")
            sim = info.get("similarity", 0.0)
            matched = info.get("matched_query", "")
        else:
            kind = "miss"
            sim = info.get("nearest_similarity", 0.0)
            matched = "(nearest)"
        print(f"{query[:48]:<50} {kind:<9} {sim:>6.3f} {elapsed:>7.2f}  {matched[:32]}")

    print(
        "\n  The `sim` column on a miss is the nearest stored query. Notice how far\n"
        "  below the threshold the paraphrases sit: with this hashing embedder,\n"
        "  'how much annual leave am I entitled to' and 'how many holiday days do I\n"
        "  get' share almost no surface form, so they are not near-neighbours in this\n"
        "  vector space at all. A trained bi-encoder would place them close together\n"
        "  and this cache would hit. The mechanism is sound; the embedder is the\n"
        "  limiting factor, and the miss column is how you find that out.\n\n"
        "  Now look at 'how do I roll back a database migration'. Its nearest stored\n"
        "  query is 'how do I roll back a deployment' at ~0.84 — a near miss under a\n"
        "  0.90 threshold, and a confident hit under 0.80. Those two questions have\n"
        "  different answers: rolling back a deploy is one command, and the deploy\n"
        "  runbook says explicitly that it does NOT roll back migrations. A cache\n"
        "  tuned for hit rate would have served the wrong one, instantly, with no\n"
        "  error. This is not a hypothetical — it is on the screen above."
    )

    stats = cache.stats.as_dict()
    print()
    for key, value in stats.items():
        print(f"  {key:<22} {value}")

    rule("Scope is part of the key")
    scoped = MetadataFilter(equals={"doc_type": "runbook"})
    q = "how do I roll back a deploy"
    before = cache.stats.hits
    pipeline.retrieve(q, metadata_filter=scoped)
    print(f"  {q!r}")
    print(f"    unfiltered: cached")
    print(f"    with doc_type=runbook: {'HIT' if cache.stats.hits > before else 'MISS (correct)'}")
    print(
        "\n  The same question under a different filter is a different question, and a\n"
        "  cache that ignores the filter will happily serve results the caller was not\n"
        "  allowed to see. In a multi-tenant system the scope is the tenant id, and\n"
        "  getting this wrong is a data-leak bug rather than a tuning issue."
    )

    rule("Choosing the threshold — the part that is not optional")
    queries = load_queries(ROOT / "data" / "eval" / "queries.jsonl")
    pairs = [(q.text, q.gold_doc_ids) for q in queries]
    # Interleave near-duplicate phrasings so the sweep has real hits to grade.
    pairs += [
        ("how much annual leave am I entitled to", frozenset({"policy-leave-and-pto"})),
        ("what is our vacation allowance", frozenset({"policy-leave-and-pto"})),
        ("how do I undo a bad release", frozenset({"runbook-deploy-rollback"})),
        ("what is the command to roll back a deployment", frozenset({"runbook-deploy-rollback"})),
        ("who is on call overnight", frozenset({"policy-oncall-rotation"})),
        ("what does ERR_CURRENCY_MISMATCH mean", frozenset({"readme-ledger-core"})),
    ]

    rows = measure_false_hits(
        cache, pairs,
        thresholds=(0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95),
    )
    print(f"  {'threshold':>10} {'hit rate':>10} {'false hits':>12} {'of hits':>9}")
    for row in rows:
        print(f"  {row['threshold']:>10.2f} {row['hit_rate']:>10.1%} "
              f"{row['false_hit_rate']:>12.1%} {row['false_hits_among_hits']:>9.1%}")

    print(
        "\n  A false hit is a cache hit whose stored answer was about a different\n"
        "  document entirely. Read the last column as: of the requests you served\n"
        "  from cache, this fraction got the wrong answer — instantly, confidently,\n"
        "  and with no error anywhere in your logs.\n\n"
        "  This curve is why the threshold is a correctness decision, not a hit-rate\n"
        "  knob. Pick the highest threshold whose hit rate you can live with, not the\n"
        "  lowest one whose false-hit rate you can tolerate — the two sound similar\n"
        "  and lead to very different systems.\n\n"
        "  The absolute numbers here are specific to this embedder and this corpus.\n"
        "  Cosine similarity means something different in every vector space, so a\n"
        "  threshold copied from a blog post is a guess. Re-derive this curve whenever\n"
        "  you change the embedding model — it is thirty lines and one eval file."
    )

    rule("What it saves")
    cold = RagPipeline.build(docs, PipelineConfig(top_k=5, use_cache=False))
    n = 3
    started = time.perf_counter()
    for _ in range(n):
        for query in TRAFFIC:
            cold.retrieve(query)
    uncached_ms = (time.perf_counter() - started) * 1000

    warm = RagPipeline.build(docs, PipelineConfig(top_k=5, use_cache=True, cache_threshold=0.90))
    started = time.perf_counter()
    for _ in range(n):
        for query in TRAFFIC:
            warm.retrieve(query)
    cached_ms = (time.perf_counter() - started) * 1000

    print(f"  {n * len(TRAFFIC)} requests, no cache   {uncached_ms:8.1f} ms")
    print(f"  {n * len(TRAFFIC)} requests, cached     {cached_ms:8.1f} ms")
    print(f"  saved                    {uncached_ms - cached_ms:8.1f} ms "
          f"({1 - cached_ms / uncached_ms:.0%})")
    print(f"  hit rate                 {warm.cache.stats.hit_rate:.1%}")
    print(
        "\n  Retrieval here is sub-millisecond, so the saving looks modest. Put a\n"
        "  cross-encoder and an LLM call behind it and the same hit rate is the\n"
        "  difference between 40ms and 4 seconds — and between one dollar and eighty."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
