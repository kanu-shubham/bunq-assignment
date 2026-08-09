#!/usr/bin/env python3
"""Calibrate the abstention threshold from data.

    python scripts/calibrate_abstention.py

An abstention threshold picked by eye is the reason RAG systems answer questions
their corpus cannot answer. The right way to choose one is to score two
populations — questions the corpus *can* answer and questions it *cannot* — and
read the trade-off directly.

The output is a curve, not a number. Where you sit on it is a product decision:
an internal documentation assistant should refuse rather than guess, so this
repository picks the lowest threshold with no false answers on the probe set,
plus margin, and accepts the answer rate that costs.
"""

from __future__ import annotations

import argparse
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ragkit import (  # noqa: E402
    LexicalCrossEncoder,
    PipelineConfig,
    RagPipeline,
    load_corpus,
    load_queries,
)
from ragkit.textutil import sentences, tokenize  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Questions this corpus genuinely cannot answer. They are deliberately varied:
# general knowledge, plausible-sounding-but-absent policy, and questions that
# share vocabulary with the corpus without being about it. That last group is
# the one that matters — "capital of France" reaches "capital statements", and
# a threshold tuned only against obviously-unrelated questions will not catch it.
OUT_OF_CORPUS = [
    "what is the capital of France",
    "how do I bake sourdough bread",
    "who won the world cup in 1998",
    "what is the boiling point of mercury",
    "recommend a good italian restaurant in rome",
    "how tall is mount kilimanjaro",
    "what year did the berlin wall fall",
    "how do I train for a marathon",
    "what is the plot of hamlet",
    "how do I change a car tyre",
    "what is photosynthesis",
    "who painted the mona lisa",
    # Shares vocabulary with the corpus, still unanswerable by it:
    "what is our policy on unlimited vacation",
    "how much equity do engineers get",
    "what is the bonus structure for the payments team",
    "which cloud provider do we use for the ledger",
    "what is the headcount of the risk team",
    "when is the next company offsite",
    "what is the SLA we promise partners in the contract",
    "how much do we pay the KYC vendor per check",
    "what is the salary band for a staff engineer",
    "who is the CTO",
    "what is the interest rate on savings pots",
    "how many customers do we have",
]


def best_sentence_score(pipeline, scorer, query: str, k: int) -> float:
    chunks = pipeline.retrieve(query, k).chunks
    if not chunks:
        return 0.0
    qvec = pipeline.embedder.encode([query])[0]
    best = 0.0
    for sc in chunks:
        for sentence in sentences(sc.chunk.body):
            if len(tokenize(sentence)) < 4:
                continue
            score, _ = scorer.score_pair(
                query, sentence, sc.chunk.context_prefix, qvec=qvec, cache_key=sc.chunk_id
            )
            best = max(best, score)
    return best


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-k", type=int, default=5)
    parser.add_argument("--margin", type=float, default=0.35,
                        help="safety margin above the highest out-of-corpus score")
    args = parser.parse_args()

    docs = load_corpus(ROOT / "data" / "corpus")
    pipeline = RagPipeline.build(docs, PipelineConfig(top_k=args.k))
    scorer = LexicalCrossEncoder.from_embedder(pipeline.embedder)
    queries = load_queries(ROOT / "data" / "eval" / "queries.jsonl")

    answerable = sorted(best_sentence_score(pipeline, scorer, q.text, args.k) for q in queries)
    unanswerable_scored = sorted(
        ((best_sentence_score(pipeline, scorer, q, args.k), q) for q in OUT_OF_CORPUS),
        reverse=True,
    )
    unanswerable = sorted(score for score, _ in unanswerable_scored)

    print("=" * 74)
    print("SCORE DISTRIBUTIONS (best sentence in the retrieved context)")
    print("=" * 74)
    print(f"  answerable    n={len(answerable):<4} min={answerable[0]:.2f}  "
          f"p25={answerable[len(answerable) // 4]:.2f}  median={statistics.median(answerable):.2f}  "
          f"max={answerable[-1]:.2f}")
    print(f"  unanswerable  n={len(unanswerable):<4} min={unanswerable[0]:.2f}  "
          f"median={statistics.median(unanswerable):.2f}  "
          f"p90={unanswerable[int(len(unanswerable) * 0.9)]:.2f}  max={unanswerable[-1]:.2f}")

    print()
    print("=" * 74)
    print("THRESHOLD SWEEP")
    print("=" * 74)
    print(f"  {'threshold':>10} {'answered':>10} {'false answers':>15}")
    lo = max(0.5, unanswerable[0] - 0.5)
    hi = max(answerable[-1], unanswerable[-1]) + 0.5
    steps = 18
    best_threshold = None
    for i in range(steps + 1):
        t = lo + (hi - lo) * i / steps
        answered = sum(1 for v in answerable if v >= t) / len(answerable)
        false = sum(1 for v in unanswerable if v >= t) / len(unanswerable)
        flag = ""
        if false == 0.0 and best_threshold is None:
            best_threshold = t
            flag = "  <- first threshold with no false answers"
        print(f"  {t:>10.2f} {answered:>10.1%} {false:>15.1%}{flag}")

    print()
    print("=" * 74)
    print("THE PROBES THAT BREAK IT")
    print("=" * 74)
    for score, question in unanswerable_scored[:6]:
        print(f"  {score:>6.2f}  {question}")

    strict = unanswerable[-1] + args.margin
    strict_rate = sum(1 for v in answerable if v >= strict) / len(answerable)

    print()
    print("=" * 74)
    print("WHAT THIS ACTUALLY SHOWS")
    print("=" * 74)
    print(
        f"  A threshold with zero false answers on this probe set needs to sit at\n"
        f"  {strict:.2f}, which answers only {strict_rate:.0%} of the questions the corpus can\n"
        "  genuinely answer. That is not a usable operating point, and no amount of\n"
        "  tuning fixes it, because the failure is not a calibration problem.\n\n"
        "  Look at the probes above. The high scorers are the ones whose vocabulary\n"
        "  overlaps the corpus — 'what is our policy on unlimited vacation' retrieves\n"
        "  the leave policy and scores like a hit, because relevance scoring answers\n"
        "  'is this passage about this topic', and the question being asked is 'does\n"
        "  this passage answer this question'. Those are different questions, and no\n"
        "  scalar derived from the first can decide the second.\n\n"
        "  So the threshold is a cheap first gate and nothing more. It catches\n"
        "  'how do I bake sourdough'. It will never catch 'do we offer unlimited\n"
        "  vacation'. The two mechanisms that do catch it are both downstream:\n\n"
        "    1. Entailment. Ask the generator to decide whether the context answers\n"
        "       the question and to return that decision as a structured field —\n"
        "       ClaudeAnswerer's `answerable`. A model can tell that a passage\n"
        "       stating '27 days of annual leave' does not support 'unlimited'.\n"
        "    2. Quote verification. Every claim carries a verbatim span that is\n"
        "       checked against the source after generation. A fabricated premise\n"
        "       cannot produce a quote that verifies.\n\n"
        f"  This repository ships min_relevance={_recommended_gate(unanswerable):.1f} as the gate and relies on\n"
        "  those two for the guarantee. Re-run this whenever the corpus, the embedder,\n"
        "  or the reranker changes — the numbers are a property of all three."
    )
    return 0


def _recommended_gate(unanswerable: list[float]) -> float:
    """The 75th percentile of unanswerable scores.

    High enough to filter the obviously-unrelated, low enough to keep the answer
    rate usable. It is explicitly *not* trying to be the safety guarantee — the
    entailment and quote-verification steps are.
    """
    ordered = sorted(unanswerable)
    return round(ordered[int(len(ordered) * 0.75)] * 2) / 2


if __name__ == "__main__":
    raise SystemExit(main())
