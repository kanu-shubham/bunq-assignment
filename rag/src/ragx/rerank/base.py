"""Reranker protocol and non-LLM implementations."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ..index.bm25 import tokenize
from ..types import ScoredChunk


@runtime_checkable
class Reranker(Protocol):
    def rerank(
        self, query: str, candidates: Sequence[ScoredChunk], k: int
    ) -> list[ScoredChunk]: ...


class NoopReranker:
    """Keeps fusion order. This is what a failing LLM reranker degrades to."""

    def rerank(
        self, query: str, candidates: Sequence[ScoredChunk], k: int
    ) -> list[ScoredChunk]:
        return list(candidates)[:k]


class LexicalOverlapReranker:
    """Cheap deterministic reranker: query-term coverage over the chunk.

    Not competitive with a cross-encoder or an LLM, but it is free, has no
    network dependency, and is a useful control arm in the eval harness — if a
    change to the LLM reranker cannot beat this, the change is not paying for
    itself.
    """

    def rerank(
        self, query: str, candidates: Sequence[ScoredChunk], k: int
    ) -> list[ScoredChunk]:
        query_terms = set(tokenize(query))
        if not query_terms:
            return list(candidates)[:k]
        rescored: list[ScoredChunk] = []
        for cand in candidates:
            terms = set(tokenize(cand.chunk.embed_text))
            coverage = len(query_terms & terms) / len(query_terms)
            rescored.append(
                ScoredChunk(
                    chunk=cand.chunk,
                    score=coverage,
                    channel="rerank",
                    component_scores={**cand.component_scores, "fused": cand.score},
                    component_ranks=cand.component_ranks,
                )
            )
        rescored.sort(key=lambda s: (s.score, s.chunk.chunk_id), reverse=True)
        return rescored[:k]
