#!/usr/bin/env python3
"""Demo 3 — Hybrid search and metadata filtering.

Finds queries where BM25 beats dense retrieval and vice versa, shows fusion
getting both right, and demonstrates why metadata filters must be applied before
top-k selection rather than after.

    python demos/demo3_hybrid_search.py
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ragkit import (  # noqa: E402
    MetadataFilter,
    PipelineConfig,
    RagPipeline,
    load_corpus,
    load_queries,
)
from ragkit.filters import selectivity  # noqa: E402
from ragkit.hybrid import normalized_score_fusion, reciprocal_rank_fusion  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


def rule(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def rank_of(chunks, gold: set[str]) -> int | None:
    seen: list[str] = []
    for sc in chunks:
        if sc.doc_id not in seen:
            seen.append(sc.doc_id)
    for i, doc in enumerate(seen, start=1):
        if doc in gold:
            return i
    return None


def main() -> int:
    docs = load_corpus(ROOT / "data" / "corpus")
    queries = load_queries(ROOT / "data" / "eval" / "queries.jsonl")
    base = PipelineConfig(top_k=10, use_rewrite=False, use_rerank=False)
    pipeline = RagPipeline.build(docs, base)

    lex = pipeline.with_config(PipelineConfig(top_k=10, fusion="bm25_only", use_rewrite=False, use_rerank=False))
    den = pipeline.with_config(PipelineConfig(top_k=10, fusion="dense_only", use_rewrite=False, use_rerank=False))
    hyb = pipeline.with_config(PipelineConfig(top_k=10, fusion="rrf", use_rewrite=False, use_rerank=False))

    rule("Where the two indexes disagree")
    print("Rank of the first gold document (lower is better; '-' = not in top 10)\n")
    print(f"{'query':<52} {'bm25':>6} {'dense':>6} {'rrf':>6}")

    lex_wins, den_wins = [], []
    for q in queries:
        gold = set(q.gold_doc_ids)
        rl = rank_of(lex.retrieve(q.text, 10).chunks, gold)
        rd = rank_of(den.retrieve(q.text, 10).chunks, gold)
        rh = rank_of(hyb.retrieve(q.text, 10).chunks, gold)
        if rl and (not rd or rd > rl + 1):
            lex_wins.append((q, rl, rd, rh))
        elif rd and (not rl or rl > rd + 1):
            den_wins.append((q, rl, rd, rh))

    def show(rows, title):
        print(f"\n  {title}")
        for q, rl, rd, rh in rows[:5]:
            f = lambda r: str(r) if r else "-"  # noqa: E731
            print(f"  {q.text[:50]:<52} {f(rl):>6} {f(rd):>6} {f(rh):>6}   [{q.category}]")

    show(lex_wins, "BM25 wins — exact tokens the embedder blurs")
    show(den_wins, "Dense wins — the query's surface form is not in the document")

    print(
        "\nThat split is the entire argument for hybrid. The two indexes fail on\n"
        "different queries, so fusing them recovers most of both. Note the pattern:\n"
        "BM25 owns rare identifiers, dense owns typos and morphology."
    )

    rule("RRF vs. normalised-score fusion")
    query = "how do we stop sending the same push message twice"
    lexical = pipeline.bm25.search(query, 8)
    vector = pipeline.dense.search(query, 8)
    print(f"query: {query!r}\n")
    print(f"  BM25 top score  {lexical[0].score:8.3f}   (unbounded, corpus-dependent)")
    print(f"  cosine top score{vector[0].score:8.3f}   (bounded in [-1, 1])")
    print("  Adding those two numbers together is meaningless — hence rank-based fusion.\n")

    rrf = reciprocal_rank_fusion([lexical, vector], component_names=["bm25", "dense"])
    nsf = normalized_score_fusion([lexical, vector])
    print(f"  {'rank':<5} {'RRF':<46} {'normalised':<46}")
    for i in range(5):
        a = rrf[i].doc_id if i < len(rrf) else "-"
        b = nsf[i].doc_id if i < len(nsf) else "-"
        print(f"  {i + 1:<5} {a:<46} {b:<46}")
    print(
        "\n  RRF needs no tuning and cannot be skewed by an outlier score. Normalised\n"
        "  fusion keeps score magnitude — useful when you want to know whether the top\n"
        "  hit is a runaway winner or a coin flip — at the cost of a weight to tune."
    )

    rule("Metadata filtering: before top-k, not after")
    mfilter = MetadataFilter(equals={"doc_type": "postmortem"})
    sel = selectivity(mfilter, pipeline.chunks)
    print(f"filter: doc_type=postmortem   admits {sel:.1%} of chunks\n")

    query = "duplicate"
    pre = hyb.retrieve(query, 5, metadata_filter=mfilter)
    print("  Pre-filtered (correct) — the filter is pushed into both indexes:")
    for sc in pre.chunks:
        print(f"    {sc.score:.4f}  {sc.doc_id}")

    unfiltered = hyb.retrieve(query, 5).chunks
    post = [sc for sc in unfiltered if mfilter.matches(sc.chunk)]
    print(f"\n  Post-filtered (wrong) — retrieve 5, then discard: {len(post)} of 5 survive")
    for sc in post:
        print(f"    {sc.score:.4f}  {sc.doc_id}")
    if len(post) < 5:
        print(
            f"\n  Post-filtering returned {len(post)} results where {len(pre.chunks)} were asked for.\n"
            "  The failure presents as 'the retriever found nothing', which sends you\n"
            "  debugging the retriever instead of the filter placement. The more\n"
            "  selective the filter, the more results silently vanish."
        )

    rule("A filter narrow enough to be dangerous")
    narrow = MetadataFilter(equals={"team": "people"}, has_tags=["pto"])
    print(f"filter: team=people AND tags contains 'pto' -> {selectivity(narrow, pipeline.chunks):.2%} of chunks")
    got = hyb.retrieve("how much leave do I get", 5, metadata_filter=narrow)
    for sc in got.chunks:
        print(f"    {sc.score:.4f}  {sc.doc_id}")
    print(
        "\n  Below roughly 2% selectivity, k is bounded by the filter and not by\n"
        "  relevance. Worth surfacing to the caller: 'showing all 3 matching\n"
        "  documents' is a better answer than a top-10 that is silently a top-3."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
