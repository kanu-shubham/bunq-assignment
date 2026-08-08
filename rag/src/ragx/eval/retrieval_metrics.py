"""Retrieval metrics.

Which number to watch, and why:

* **recall@k_candidates** is the ceiling. If the right document is not in the
  candidate set, no reranker and no model can recover it. This is the first
  metric to look at when answers are wrong.
* **nDCG@k_final** measures whether the reranker put it in a position the
  answering model will actually use. Position matters: a correct chunk ranked
  8th of 8 competes with seven distractors.
* **MRR** is a useful single number for "how deep does the user have to look",
  and it is the metric to report when there is exactly one right answer.

All of these are computed at *document* granularity by default so they survive
a chunker change (see `dataset.py`).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass


def recall_at_k(retrieved: Sequence[str], relevant: Sequence[str], k: int) -> float:
    if not relevant:
        return 1.0
    top = set(retrieved[:k])
    return len(top & set(relevant)) / len(set(relevant))


def precision_at_k(retrieved: Sequence[str], relevant: Sequence[str], k: int) -> float:
    if k == 0:
        return 0.0
    top = retrieved[:k]
    if not top:
        return 0.0
    return sum(1 for r in top if r in set(relevant)) / len(top)


def hit_rate_at_k(retrieved: Sequence[str], relevant: Sequence[str], k: int) -> float:
    return 1.0 if set(retrieved[:k]) & set(relevant) else 0.0


def reciprocal_rank(retrieved: Sequence[str], relevant: Sequence[str]) -> float:
    relevant_set = set(relevant)
    for rank, item in enumerate(retrieved, start=1):
        if item in relevant_set:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved: Sequence[str], relevant: Sequence[str], k: int) -> float:
    """Binary-gain nDCG. Graded relevance would be better, but binary labels are
    what an internal team will realistically maintain, and a label set nobody
    maintains is worse than a coarser one that stays true."""
    relevant_set = set(relevant)
    if not relevant_set:
        return 1.0
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, item in enumerate(retrieved[:k], start=1)
        if item in relevant_set
    )
    ideal = sum(
        1.0 / math.log2(rank + 1) for rank in range(1, min(len(relevant_set), k) + 1)
    )
    return dcg / ideal if ideal else 0.0


@dataclass(frozen=True, slots=True)
class RetrievalScores:
    recall_at_candidates: float
    recall_at_final: float
    ndcg_at_final: float
    mrr: float
    hit_rate: float
    precision_at_final: float

    def as_dict(self) -> dict[str, float]:
        return {
            "recall@candidates": round(self.recall_at_candidates, 4),
            "recall@final": round(self.recall_at_final, 4),
            "ndcg@final": round(self.ndcg_at_final, 4),
            "mrr": round(self.mrr, 4),
            "hit_rate@final": round(self.hit_rate, 4),
            "precision@final": round(self.precision_at_final, 4),
        }


def score_case(
    candidate_ids: Sequence[str],
    final_ids: Sequence[str],
    relevant: Sequence[str],
    final_k: int,
) -> RetrievalScores:
    return RetrievalScores(
        recall_at_candidates=recall_at_k(candidate_ids, relevant, len(candidate_ids)),
        recall_at_final=recall_at_k(final_ids, relevant, final_k),
        ndcg_at_final=ndcg_at_k(final_ids, relevant, final_k),
        mrr=reciprocal_rank(final_ids, relevant),
        hit_rate=hit_rate_at_k(final_ids, relevant, final_k),
        precision_at_final=precision_at_k(final_ids, relevant, final_k),
    )


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0
