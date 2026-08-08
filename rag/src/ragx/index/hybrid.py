"""Hybrid retrieval: run both channels, fuse, diversify.

Fusion strategy defaults to **Reciprocal Rank Fusion**. RRF combines *ranks*,
not scores, which is the whole point: BM25 scores are unbounded and corpus
dependent, cosine scores sit in a narrow band near the top, and normalising
them against each other means re-tuning every time the corpus or the embedding
model changes. RRF has one parameter (k=60, and it is famously insensitive) and
needs no calibration.

`weighted` normalisation is kept as an option because when you *do* have
labelled data, a tuned weighted blend beats RRF by a few points of nDCG. Which
is exactly the trade: RRF for day one, weighted once the eval set is real.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..config import RetrievalConfig
from ..embed.base import Vector, cosine
from ..types import Chunk, ScoredChunk

Hit = tuple[str, float]


def reciprocal_rank_fusion(
    channels: dict[str, Sequence[Hit]], k: int = 60
) -> dict[str, tuple[float, dict[str, int], dict[str, float]]]:
    fused: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    raw: dict[str, dict[str, float]] = {}
    for channel, hits in channels.items():
        for rank, (chunk_id, score) in enumerate(hits, start=1):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (k + rank)
            ranks.setdefault(chunk_id, {})[channel] = rank
            raw.setdefault(chunk_id, {})[channel] = score
    return {cid: (fused[cid], ranks[cid], raw[cid]) for cid in fused}


def weighted_fusion(
    channels: dict[str, Sequence[Hit]], weights: dict[str, float]
) -> dict[str, tuple[float, dict[str, int], dict[str, float]]]:
    """Min-max normalise each channel to [0,1], then take a weighted sum.

    A chunk missing from a channel scores 0 there — not "average" — so a hit
    that only one channel found has to earn its place on that channel's margin.
    """
    fused: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    raw: dict[str, dict[str, float]] = {}
    for channel, hits in channels.items():
        if not hits:
            continue
        scores = [s for _, s in hits]
        lo, hi = min(scores), max(scores)
        span = (hi - lo) or 1.0
        weight = weights.get(channel, 0.0)
        for rank, (chunk_id, score) in enumerate(hits, start=1):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + weight * ((score - lo) / span)
            ranks.setdefault(chunk_id, {})[channel] = rank
            raw.setdefault(chunk_id, {})[channel] = score
    return {cid: (fused[cid], ranks[cid], raw[cid]) for cid in fused}


def fuse(
    channels: dict[str, Sequence[Hit]],
    config: RetrievalConfig,
    resolve: Callable[[str], Chunk | None],
) -> list[ScoredChunk]:
    if config.fusion == "weighted":
        combined = weighted_fusion(
            channels,
            {"dense": config.dense_weight, "lexical": config.lexical_weight},
        )
    else:
        combined = reciprocal_rank_fusion(channels, k=config.rrf_k)

    out: list[ScoredChunk] = []
    for chunk_id, (score, ranks, raw) in combined.items():
        chunk = resolve(chunk_id)
        if chunk is None:  # index skew between channels; drop rather than guess
            continue
        out.append(
            ScoredChunk(
                chunk=chunk,
                score=score,
                channel="fused",
                component_scores=raw,
                component_ranks=ranks,
            )
        )
    out.sort(key=lambda s: (s.score, s.chunk.chunk_id), reverse=True)
    return out


def cap_per_document(scored: Sequence[ScoredChunk], cap: int) -> list[ScoredChunk]:
    """One long policy page can otherwise occupy the entire context window."""
    if cap <= 0:
        return list(scored)
    seen: dict[str, int] = {}
    out: list[ScoredChunk] = []
    for item in scored:
        count = seen.get(item.chunk.doc_id, 0)
        if count >= cap:
            continue
        seen[item.chunk.doc_id] = count + 1
        out.append(item)
    return out


def mmr(
    scored: Sequence[ScoredChunk],
    vectors: dict[str, Vector],
    k: int,
    lambda_: float,
) -> list[ScoredChunk]:
    """Maximal Marginal Relevance: trade a little relevance for coverage.

    Matters when a question has several partial answers spread across documents
    ("what changed in the travel policy?") and the top-5 by score are five
    paraphrases of the same paragraph.
    """
    if lambda_ <= 0.0 or not scored:
        return list(scored)[:k]
    candidates = list(scored)
    selected: list[ScoredChunk] = []
    while candidates and len(selected) < k:
        best_idx, best_value = 0, float("-inf")
        for idx, cand in enumerate(candidates):
            cand_vec = vectors.get(cand.chunk.chunk_id)
            redundancy = 0.0
            if cand_vec is not None and selected:
                redundancy = max(
                    cosine(cand_vec, vectors[s.chunk.chunk_id])
                    for s in selected
                    if s.chunk.chunk_id in vectors
                )
            value = lambda_ * cand.score - (1 - lambda_) * redundancy
            if value > best_value:
                best_idx, best_value = idx, value
        selected.append(candidates.pop(best_idx))
    return selected


@dataclass(frozen=True, slots=True)
class RetrievalDebug:
    """Returned alongside results so a bad answer is attributable to a stage."""

    dense_hits: int
    lexical_hits: int
    fused_hits: int
    after_cap: int
    fusion: str
