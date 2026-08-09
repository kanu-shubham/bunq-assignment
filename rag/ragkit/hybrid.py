"""Hybrid retrieval: fusing a lexical ranking with a dense ranking.

The naive approach is to add the two scores. Do not: BM25 is unbounded and
corpus-dependent, cosine is in [-1, 1], and the sum is dominated by whichever
happens to be larger today. Two defensible options:

**Reciprocal rank fusion (default).** Score each document by ``1/(k + rank)`` in
each list and sum. It uses only ranks, so it is immune to scale, needs no tuning
per corpus, and is remarkably hard to beat. ``k`` (60 by convention) controls how
sharply the top of each list dominates.

**Normalised score fusion.** Min-max each list to [0, 1] and take a weighted
sum. Keeps score *magnitude* information — the difference between "the top hit
is a runaway winner" and "the top five are indistinguishable" — which RRF throws
away. Costs you a weight to tune, and the normalisation is sensitive to
outliers.

Both are here. The ablation reports RRF; the fusion demo shows where they differ.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Sequence

import numpy as np

from .dense import DenseIndex
from .filters import MetadataFilter
from .lexical import BM25Index
from .types import Chunk, ScoredChunk

FusionMethod = Literal["rrf", "normalized", "bm25_only", "dense_only"]


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[ScoredChunk]],
    *,
    k: float = 60.0,
    weights: Sequence[float] | None = None,
    component_names: Sequence[str] | None = None,
) -> list[ScoredChunk]:
    weights = list(weights or [1.0] * len(rankings))
    if len(weights) != len(rankings):
        raise ValueError("weights and rankings must be the same length")
    names = list(component_names or [f"list{i}" for i in range(len(rankings))])

    fused: dict[str, ScoredChunk] = {}
    for list_idx, ranking in enumerate(rankings):
        for rank, scored in enumerate(ranking, start=1):
            contribution = weights[list_idx] / (k + rank)
            entry = fused.get(scored.chunk_id)
            if entry is None:
                entry = ScoredChunk(scored.chunk, 0.0, dict(scored.components))
                fused[scored.chunk_id] = entry
            else:
                entry.components.update(scored.components)
            entry.score += contribution
            entry.components[f"rank_{names[list_idx]}"] = float(rank)
            entry.components["rrf"] = entry.score
    return sorted(fused.values(), key=lambda s: -s.score)


def _minmax(values: Sequence[float]) -> list[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return [1.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def normalized_score_fusion(
    rankings: Sequence[Sequence[ScoredChunk]],
    *,
    weights: Sequence[float] | None = None,
) -> list[ScoredChunk]:
    weights = list(weights or [1.0] * len(rankings))
    fused: dict[str, ScoredChunk] = {}
    for list_idx, ranking in enumerate(rankings):
        normed = _minmax([s.score for s in ranking])
        for scored, value in zip(ranking, normed):
            entry = fused.get(scored.chunk_id)
            if entry is None:
                entry = ScoredChunk(scored.chunk, 0.0, dict(scored.components))
                fused[scored.chunk_id] = entry
            else:
                entry.components.update(scored.components)
            entry.score += weights[list_idx] * value
            entry.components["fused"] = entry.score
    return sorted(fused.values(), key=lambda s: -s.score)


@dataclass
class HybridRetriever:
    """First-stage retrieval: BM25 + dense, fused, optionally metadata-filtered.

    ``candidate_k`` is the depth pulled from *each* index before fusion. It is
    the most important knob here and the one most often left at its default: the
    reranker downstream can only reorder what this stage retrieved, so a
    ``candidate_k`` of 10 caps your ceiling at whatever recall@10 the first stage
    achieves, no matter how good the reranker is.
    """

    bm25: BM25Index
    dense: DenseIndex
    method: FusionMethod = "rrf"
    candidate_k: int = 50
    rrf_k: float = 60.0
    weights: tuple[float, float] = (1.0, 1.0)

    @property
    def chunks(self) -> list[Chunk]:
        return self.bm25.chunks

    def search(
        self,
        query: str,
        k: int = 10,
        *,
        metadata_filter: MetadataFilter | None = None,
    ) -> list[ScoredChunk]:
        mask = metadata_filter.mask(self.chunks) if metadata_filter else None
        depth = max(self.candidate_k, k)

        if self.method == "bm25_only":
            return self.bm25.search(query, depth, allowed=mask)[:k]
        if self.method == "dense_only":
            return self.dense.search(query, depth, allowed=mask)[:k]

        lexical = self.bm25.search(query, depth, allowed=mask)
        vector = self.dense.search(query, depth, allowed=mask)

        if self.method == "rrf":
            fused = reciprocal_rank_fusion(
                [lexical, vector],
                k=self.rrf_k,
                weights=self.weights,
                component_names=["bm25", "dense"],
            )
        elif self.method == "normalized":
            fused = normalized_score_fusion([lexical, vector], weights=self.weights)
        else:  # pragma: no cover - guarded by Literal
            raise ValueError(f"unknown fusion method: {self.method}")
        return fused[:k]

    def search_multi(
        self,
        queries: Sequence[str],
        k: int = 10,
        *,
        metadata_filter: MetadataFilter | None = None,
        per_query_k: int | None = None,
    ) -> list[ScoredChunk]:
        """Fuse results across several query variants (see ``query_rewrite``).

        Each variant produces its own fused ranking, and those rankings are
        themselves fused by RRF. Doing it this way rather than concatenating
        means a chunk found by three different phrasings outranks one found only
        by the longest.
        """
        if not queries:
            raise ValueError("search_multi needs at least one query")
        per_query_k = per_query_k or max(self.candidate_k, k)
        rankings = [
            self.search(q, per_query_k, metadata_filter=metadata_filter) for q in queries
        ]
        fused = reciprocal_rank_fusion(
            rankings,
            k=self.rrf_k,
            component_names=[f"q{i}" for i in range(len(rankings))],
        )
        return fused[:k]


def dedupe_by_document(chunks: Iterable[ScoredChunk], max_per_doc: int = 2) -> list[ScoredChunk]:
    """Cap how many chunks any one document contributes.

    Without this, a single long document with a repetitive section can occupy
    the whole context window and crowd out the document that holds the other
    half of the answer. Diversity beats depth once you are past the top result.
    """
    seen: dict[str, int] = {}
    out: list[ScoredChunk] = []
    for sc in chunks:
        count = seen.get(sc.doc_id, 0)
        if count >= max_per_doc:
            continue
        seen[sc.doc_id] = count + 1
        out.append(sc)
    return out
