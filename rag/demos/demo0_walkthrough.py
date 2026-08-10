#!/usr/bin/env python3
"""Demo 0 — the whole pipeline on three documents, every number shown.

    python demos/demo0_walkthrough.py

Start here. The real corpus has 90 documents and 339 chunks, which is the right
size to *measure* a system and the wrong size to *understand* one. This file uses
three documents small enough that you can check every number with a pencil.

The three documents are chosen to create the three situations that matter:

  DOC-A  deploy rollback runbook      the answer to query 1
  DOC-B  database migration guide     shares the word "rollback" — a hard negative
  DOC-C  on-call policy               says "paged", never "woken up" — needs rewriting

Read the output top to bottom. Every stage prints what it received and what it
produced, so you can see exactly where a query succeeds or dies.
"""

from __future__ import annotations

import math
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ragkit import ChunkConfig, Document, HashingEmbedder  # noqa: E402
from ragkit.chunking import chunk_corpus  # noqa: E402
from ragkit.dense import DenseIndex  # noqa: E402
from ragkit.hybrid import reciprocal_rank_fusion  # noqa: E402
from ragkit.lexical import BM25Index, _FallbackBM25  # noqa: E402
from ragkit.query_rewrite import RuleBasedRewriter  # noqa: E402
from ragkit.rerank import LexicalCrossEncoder  # noqa: E402
from ragkit.textutil import tokenize  # noqa: E402

# ---------------------------------------------------------------- the corpus

DOCS = [
    Document(
        doc_id="DOC-A",
        title="Deploy and rollback",
        path="a.md",
        metadata={"doc_type": "runbook", "team": "platform", "tags": ["deploy"]},
        text="""## Manual rollback

Run `kubectl argo rollouts undo` to restore the previous release. Rollback takes
about ninety seconds.

## What rollback does not cover

Rollback does not undo database migrations. Every migration must be
backwards-compatible with the previous release.""",
    ),
    Document(
        doc_id="DOC-B",
        title="Database migrations",
        path="b.md",
        metadata={"doc_type": "guide", "team": "platform", "tags": ["database"]},
        text="""## Writing a migration

Split every schema change into an additive step and a later removal step. This
is called expand and contract.

## Reverting a migration

A migration rollback is a new forward migration, never an edit. Reverting by
hand loses the audit trail.""",
    ),
    Document(
        doc_id="DOC-C",
        title="On-call rotation",
        path="c.md",
        metadata={"doc_type": "policy", "team": "people", "tags": ["oncall"]},
        text="""## The rotation

Rotations are weekly. A primary and a secondary engineer are on the schedule at
all times.

## Response expectations

The primary is paged first and acknowledges within five minutes. The secondary
is paged automatically after ten minutes.""",
    ),
]


def rule(n: str, title: str) -> None:
    print(f"\n{'=' * 76}\nSTEP {n} — {title}\n{'=' * 76}")


def main() -> int:
    # ================================================================ STEP 1
    rule("1", "The documents")
    for d in DOCS:
        print(f"  {d.doc_id}  {d.title:<22} doc_type={d.metadata['doc_type']:<8} "
              f"team={d.metadata['team']}")
    print("\n  Metadata comes from frontmatter at load time. Capture it now — you")
    print("  cannot filter on a field you did not index.")

    # ================================================================ STEP 2
    rule("2", "Chunking: documents -> retrievable units")
    # Small config so three short documents produce more than one chunk each.
    config = ChunkConfig(target_words=40, max_words=90, overlap_words=10, min_words=8)
    chunks = chunk_corpus(DOCS, config)
    print(f"  config: target={config.target_words}w  max={config.max_words}w  "
          f"overlap={config.overlap_words}w\n")
    for c in chunks:
        print(f"  {c.chunk_id}")
        print(f"    prefix : {c.context_prefix}")
        print(f"    body   : {c.body[:64].replace(chr(10), ' ')}...")
    print(f"\n  3 documents -> {len(chunks)} chunks. Each chunk carries its heading path")
    print("  as a SEPARATE field from the body. That is what step 3 boosts.")

    # ================================================================ STEP 3
    rule("3", "Tokenising, and field boosting")
    c = chunks[0]
    print(f"  chunk {c.chunk_id}")
    print(f"    body tokens   : {tokenize(c.body)}")
    print(f"    prefix tokens : {tokenize(c.context_prefix)}")
    print("\n  What actually gets indexed is  body + (prefix + tags) * 2")
    print("  so heading words count DOUBLE in the term-frequency statistics.")
    print("  This is why a query matching only the heading still finds the chunk.")

    # ================================================================ STEP 4
    rule("4", "Building the BM25 index (the inverted index)")
    corpus_tokens = [
        tokenize(ch.body) + (tokenize(ch.context_prefix)
                             + tokenize(" ".join(ch.metadata.get("tags", [])))) * 2
        for ch in chunks
    ]
    bm25_raw = _FallbackBM25(corpus_tokens)
    print(f"  {bm25_raw.n_docs} chunks indexed")
    print(f"  doc_len  = {[int(x) for x in bm25_raw.doc_len]}")
    print(f"  avgdl    = {bm25_raw.avgdl:.2f}   (used by the length-normalisation term)")
    print("\n  postings for a few interesting terms  ->  [(chunk_index, term_freq), ...]")
    for term in ("rollback", "migration", "paged", "deploy"):
        postings = bm25_raw.postings.get(term)
        idf = bm25_raw.idf.get(term)
        if postings is None:
            print(f"    {term:<10} NOT IN CORPUS")
            continue
        print(f"    {term:<10} idf={idf:5.2f}  {postings}")
    print("\n  Read those idf values. 'rollback' appears in several chunks so its idf")
    print("  is low; 'paged' appears in one so its idf is high. Rare = informative.")

    # ================================================================ STEP 5
    rule("5", "Building the dense index (embeddings)")
    embedder = HashingEmbedder(dim=256)
    embedder.fit(ch.text for ch in chunks)
    dense = DenseIndex(chunks, embedder)
    print(f"  matrix shape = {dense.matrix.shape}   ({len(chunks)} chunks x {dense.dim} dims)")
    norms = [float((dense.matrix[i] ** 2).sum() ** 0.5) for i in range(len(chunks))]
    print(f"  row norms    = {[round(n, 3) for n in norms]}")
    print("\n  Every row is L2-normalised to length 1.0. That is why cosine similarity")
    print("  is just a dot product later — no division needed at query time.")

    bm25 = BM25Index(chunks)

    # ================================================================ STEP 6
    query = "how do I roll back a deploy"
    rule("6", f"QUERY 1: {query!r}  (the happy path)")
    q_tokens = tokenize(query)
    print(f"  query tokens: {q_tokens}\n")

    print("  6a. BM25, one term at a time (the sum from the formula):\n")
    print(f"      {'term':<10} {'idf':>6}   contribution per chunk")
    total = [0.0] * len(chunks)
    for term in q_tokens:
        postings = bm25_raw.postings.get(term)
        if not postings:
            print(f"      {term:<10} {'--':>6}   not in corpus, contributes nothing")
            continue
        idf = bm25_raw.idf[term]
        parts = []
        for idx, tf in postings:
            denom = tf + bm25_raw.k1 * (
                1 - bm25_raw.b + bm25_raw.b * bm25_raw.doc_len[idx] / bm25_raw.avgdl
            )
            contrib = idf * (tf * (bm25_raw.k1 + 1)) / denom
            total[idx] += contrib
            parts.append(f"{chunks[idx].chunk_id}:{contrib:.2f}")
        print(f"      {term:<10} {idf:>6.2f}   {'  '.join(parts)}")

    print(f"\n      {'TOTAL':<10} {'':>6}   " + "  ".join(
        f"{chunks[i].chunk_id}:{total[i]:.2f}" for i in range(len(chunks)) if total[i] > 0
    ))
    lexical = bm25.search(query, 6)
    print("\n      ranked by BM25:")
    for r, s in enumerate(lexical, 1):
        print(f"        {r}. {s.score:6.3f}  {s.chunk_id:<14} {s.chunk.metadata['title']}")

    print("\n      >>> STOP AND LOOK AT THIS. Only ONE query term scored at all.")
    print("      The user wrote 'roll back' as two words. That tokenises to")
    print("      ['roll', 'back'] — and the corpus says 'rollback', ONE token.")
    print("      To BM25 those are completely unrelated strings. So the entire")
    print("      reason DOC-A wins is the word 'deploy', which appears only in its")
    print("      HEADING and is there twice because of field boosting (step 3).")
    print("      Remove the heading boost and this query returns nothing at all.")
    print("      Lexical matching is exact matching. It has no idea that")
    print("      'roll back' and 'rollback' are the same thing.")

    print("\n  6b. Dense, cosine against every chunk:\n")
    qvec = embedder.encode([query])[0]
    sims = dense.matrix @ qvec
    for i, ch in enumerate(chunks):
        print(f"      {sims[i]:6.3f}  {ch.chunk_id:<14} {ch.metadata['title']}")
    vector = dense.search(query, 6)

    print("\n  6c. Fusion by RRF — ranks only, scores discarded:\n")
    print(f"      {'chunk':<14} {'bm25 rank':>10} {'dense rank':>11} {'RRF score':>28}")
    lex_rank = {s.chunk_id: r for r, s in enumerate(lexical, 1)}
    den_rank = {s.chunk_id: r for r, s in enumerate(vector, 1)}
    fused = reciprocal_rank_fusion([lexical, vector], component_names=["bm25", "dense"])
    for s in fused:
        lr, dr = lex_rank.get(s.chunk_id), den_rank.get(s.chunk_id)
        bits = []
        if lr:
            bits.append(f"1/(60+{lr})")
        if dr:
            bits.append(f"1/(60+{dr})")
        print(f"      {s.chunk_id:<14} {str(lr or '-'):>10} {str(dr or '-'):>11} "
              f"{' + '.join(bits):>20} = {s.score:.5f}")
    print("\n      A chunk in BOTH lists accumulates from both. Agreement between two")
    print("      independent retrievers is strong evidence, and RRF rewards it free.")

    print("\n  6d. Re-ranking the shortlist — joint (query, chunk) features:\n")
    scorer = LexicalCrossEncoder.from_embedder(embedder)
    print(f"      {'chunk':<14} {'ce':>5} {'cover':>6} {'phrase':>7} {'prox':>6} {'title':>6}")
    for s in fused[:4]:
        score, f = scorer.score_pair(query, s.chunk.body, s.chunk.context_prefix,
                                     qvec=qvec, cache_key=s.chunk_id)
        print(f"      {s.chunk_id:<14} {score:5.2f} {f['coverage']:6.2f} {f['phrase']:7.2f} "
              f"{f['proximity']:6.2f} {f['title_field']:6.2f}")
    reranked = scorer.rerank(query, fused, 3)
    print("\n      final top 3:")
    for r, s in enumerate(reranked, 1):
        print(f"        {r}. {s.chunk_id:<14} {s.chunk.metadata['title']}")

    print("\n      Two things to notice, both of them real weaknesses:")
    print("      1. 'cover' is 0.00 for every chunk. Coverage is computed over the")
    print("         chunk BODY, and the only matching term ('deploy') lives in the")
    print("         heading — which is why 'title' is the only non-zero feature.")
    print("         A heading-only match scores near zero on the strongest feature.")
    print("      2. DOC-B never even reached the shortlist, so its 'rollback' mention")
    print("         was never a threat here. It becomes one the moment a query")
    print("         actually contains the token 'rollback' — try editing this file.")

    # ================================================================ STEP 7
    query2 = "deploymnt rollbacks"
    rule("7", f"QUERY 2: {query2!r}  (typo + plural: where BM25 dies)")
    print(f"  query tokens: {tokenize(query2)}")
    print("  Neither token exists in the corpus: 'deployment' is misspelled and the")
    print("  corpus says 'rollback', not 'rollbacks'.\n")
    lex2 = bm25.search(query2, 6)
    den2 = dense.search(query2, 6)
    print(f"  BM25  : {len(lex2)} results  <- every score is zero, so nothing is returned")
    print(f"  Dense : {len(den2)} results")
    for r, s in enumerate(den2[:3], 1):
        print(f"     {r}. {s.score:6.3f}  {s.chunk_id:<14} {s.chunk.metadata['title']}")
    print("\n  Dense survives because its vectors are built from character n-grams:")
    print("  '#rollb', 'ollba', 'llbac' are shared between 'rollback' and 'rollbacks'.")
    print("  THIS is the row that justifies paying for a second index.")

    # ================================================================ STEP 8
    query3 = "who gets woken up at night"
    rule("8", f"QUERY 3: {query3!r}  (where BOTH indexes die)")
    print(f"  query tokens: {tokenize(query3)}")
    print("  DOC-C is the answer, but it says 'paged', never 'woken'.\n")
    for label, results in (("BM25", bm25.search(query3, 3)), ("Dense", dense.search(query3, 3))):
        print(f"  {label}:")
        if not results:
            print("     (nothing)")
        for r, s in enumerate(results, 1):
            hit = " <- CORRECT" if s.doc_id == "DOC-C" else ""
            print(f"     {r}. {s.score:6.3f}  {s.chunk_id:<14} {s.chunk.metadata['title']}{hit}")

    # First, the shipped rewriter — which does NOT recover this. Showing the
    # failure is the point: a rule-based rewriter is exactly as good as its
    # lexicon, and this lexicon has no entry for "woken up".
    shipped = RuleBasedRewriter().rewrite(query3)
    print(f"\n  The SHIPPED rewriter produces {len(shipped.queries)} variant(s):")
    for v in shipped.queries:
        print(f"     - {v}")
    print("\n  That is just the original query back. No lexicon entry matched, so")
    print("  nothing was added, and the query still fails. This is not a bug in the")
    print("  demo — it is why 'who gets woken up when something breaks at night' is")
    print("  one of the two queries the real 43-query test split still gets wrong.")

    # Now the same mechanism WITH the missing entry, to show what fixes it.
    demo_lexicon = {"woken up": ("on-call", "paged", "rotation"),
                    "night": ("overnight", "on-call")}
    fixed = RuleBasedRewriter(lexicon=demo_lexicon).rewrite(query3)
    print("\n  Add two lexicon entries — 'woken up' -> on-call/paged, 'night' ->")
    print("  overnight/on-call — and the same code produces:")
    for v in fixed.queries:
        print(f"     - {v}")
    best = max(fixed.queries, key=lambda v: len(tokenize(v)))
    print("\n  Retrieving with the expanded variant:")
    for r, s in enumerate(bm25.search(best, 3), 1):
        hit = " <- CORRECT, recovered" if s.doc_id == "DOC-C" else ""
        print(f"     {r}. {s.score:6.3f}  {s.chunk_id:<14} {s.chunk.metadata['title']}{hit}")

    print("\n  Two lessons, and the second one matters more:")
    print("  1. Rewriting is the ONLY stage that can raise the ceiling. A re-ranker")
    print("     cannot help here — the document was never retrieved, so there is")
    print("     nothing to reorder. Hence rewriting goes BEFORE retrieval.")
    print("  2. I did NOT add these entries to the shipped lexicon, even though it")
    print("     would turn a measured failure into a measured success. The failing")
    print("     query is in the TEST split. Editing a lexicon to fix a test query is")
    print("     tuning on your test set, and every number after that is a lie you")
    print("     told yourself. Grow the lexicon from production query logs — from")
    print("     the searches that returned nothing — never from your eval set.")

    # ================================================================ STEP 9
    rule("9", "Scoring it: recall@k and precision@k")
    cases = [
        (query, {"DOC-A"}),
        (query2, {"DOC-A"}),
        (query3, {"DOC-C"}),
    ]
    print(f"  {'query':<30} {'gold':<7} {'retrieved docs @3':<26} {'r@3':>5} {'p@3':>5}")
    for q, gold in cases:
        got: list[str] = []
        for s in scorer.rerank(q, reciprocal_rank_fusion(
            [bm25.search(q, 6), dense.search(q, 6)]), 3):
            if s.doc_id not in got:
                got.append(s.doc_id)
        found = len(gold & set(got[:3]))
        recall = found / len(gold)
        precision = found / max(1, len(got[:3]))
        print(f"  {q[:28]:<30} {list(gold)[0]:<7} {str(got[:3]):<26} "
              f"{recall:>5.2f} {precision:>5.2f}")
    print("\n  recall    = correct found / correct that EXIST   (denominator: reality)")
    print("  precision = correct found / total RETURNED       (denominator: you)")
    print("\n  Scale this to 43 queries and you have the ablation table. Nothing else")
    print("  about the method changes — only the number of rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
