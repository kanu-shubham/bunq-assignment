"""Dense vector index.

A flat (exhaustive) cosine index. At corpus scale this is the right default: a
brute-force scan over a few hundred thousand vectors is milliseconds of numpy,
and it is *exact*. Approximate indexes (HNSW, IVF-PQ) trade recall for latency,
and the recall they cost is invisible unless you measure it — which is the whole
theme here. The system design note in ``docs/SYSTEM_DESIGN.md`` covers where the
crossover actually is and what an ANN index does to the numbers.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from .embeddings import Embedder
from .types import Chunk, ScoredChunk


class DenseIndex:
    """Exhaustive cosine similarity index over chunk embeddings."""

    def __init__(self, chunks: Sequence[Chunk], embedder: Embedder, *, batch_size: int = 256) -> None:
        if not chunks:
            raise ValueError("DenseIndex requires at least one chunk")
        self.chunks = list(chunks)
        self.embedder = embedder
        texts = [c.text for c in self.chunks]
        blocks = [
            embedder.encode(texts[i : i + batch_size]) for i in range(0, len(texts), batch_size)
        ]
        self.matrix = np.vstack(blocks).astype(np.float32)
        if self.matrix.shape[0] != len(self.chunks):
            raise RuntimeError("embedder returned the wrong number of vectors")

    @property
    def dim(self) -> int:
        return int(self.matrix.shape[1])

    def raw_scores(self, query: str) -> np.ndarray:
        qvec = self.embedder.encode([query])[0]
        return self.matrix @ qvec

    def search(
        self,
        query: str,
        k: int = 10,
        *,
        allowed: np.ndarray | None = None,
        min_score: float = 0.0,
    ) -> list[ScoredChunk]:
        scores = self.raw_scores(query)
        if allowed is not None:
            scores = np.where(allowed, scores, -np.inf)
        finite = int(np.isfinite(scores).sum())
        k = min(k, finite)
        if k <= 0:
            return []
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top], kind="stable")]
        return [
            ScoredChunk(self.chunks[i], float(scores[i]), {"dense": float(scores[i])})
            for i in top
            if np.isfinite(scores[i]) and scores[i] > min_score
        ]
