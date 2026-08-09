"""BM25 lexical index.

Uses ``rank_bm25`` when it is installed and falls back to a self-contained
Okapi BM25 otherwise, so the repository runs with zero third-party packages if
it has to. Both paths produce the same ranking on the same tokens; the fallback
exists because "works on a laptop with no network" is a feature.

Two details here are load-bearing and usually missing from tutorial code:

* **Field boosting.** A chunk's document title and heading path are indexed with
  extra weight. A query like "deploy rollback" should reach the chunk under the
  heading "Manual rollback" even when the body never repeats the words.
* **Pre-filtering, not post-filtering.** Metadata restrictions are applied
  *before* top-k selection. Post-filtering (take top-k, then discard the ones
  that fail the filter) is the common shortcut and it silently returns fewer
  than k results — or none — exactly when the filter is selective, which is when
  the user cared most.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Sequence

import numpy as np

from .textutil import tokenize
from .types import Chunk, ScoredChunk

try:  # pragma: no cover - import guard
    from rank_bm25 import BM25Okapi

    _HAS_RANK_BM25 = True
except ImportError:  # pragma: no cover - import guard
    BM25Okapi = None  # type: ignore[assignment]
    _HAS_RANK_BM25 = False


class _FallbackBM25:
    """Okapi BM25 over an inverted index. Used when rank_bm25 is unavailable."""

    def __init__(self, corpus: Sequence[Sequence[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.n_docs = len(corpus)
        self.doc_len = np.array([len(d) for d in corpus], dtype=np.float32)
        self.avgdl = float(self.doc_len.mean()) if self.n_docs else 0.0
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        df: Counter[str] = Counter()
        for idx, doc in enumerate(corpus):
            for term, tf in Counter(doc).items():
                self.postings[term].append((idx, tf))
                df[term] += 1
        self.idf = {
            term: math.log(1.0 + (self.n_docs - count + 0.5) / (count + 0.5))
            for term, count in df.items()
        }

    def get_scores(self, query: Sequence[str]) -> np.ndarray:
        scores = np.zeros(self.n_docs, dtype=np.float32)
        for term in query:
            postings = self.postings.get(term)
            if not postings:
                continue
            idf = self.idf[term]
            for idx, tf in postings:
                denom = tf + self.k1 * (1 - self.b + self.b * self.doc_len[idx] / self.avgdl)
                scores[idx] += idf * (tf * (self.k1 + 1)) / denom
        return scores


class BM25Index:
    """Sparse lexical index over chunks."""

    def __init__(self, chunks: Sequence[Chunk], *, prefix_weight: int = 2) -> None:
        if not chunks:
            raise ValueError("BM25Index requires at least one chunk")
        self.chunks = list(chunks)
        self.prefix_weight = prefix_weight
        self._corpus = [self._chunk_tokens(c) for c in self.chunks]
        if _HAS_RANK_BM25:
            self._bm25: object = BM25Okapi(self._corpus)
            self.backend = "rank_bm25"
        else:
            self._bm25 = _FallbackBM25(self._corpus)
            self.backend = "fallback"

    def _chunk_tokens(self, chunk: Chunk) -> list[str]:
        body = tokenize(chunk.body)
        prefix = tokenize(chunk.context_prefix)
        tags = tokenize(" ".join(str(t) for t in chunk.metadata.get("tags", [])))
        return body + (prefix + tags) * self.prefix_weight

    def raw_scores(self, query: str) -> np.ndarray:
        tokens = tokenize(query)
        if not tokens:
            return np.zeros(len(self.chunks), dtype=np.float32)
        return np.asarray(self._bm25.get_scores(tokens), dtype=np.float32)  # type: ignore[attr-defined]

    def search(
        self,
        query: str,
        k: int = 10,
        *,
        allowed: np.ndarray | None = None,
    ) -> list[ScoredChunk]:
        """Top-k chunks. ``allowed`` is a boolean mask applied *before* top-k."""
        scores = self.raw_scores(query)
        if allowed is not None:
            scores = np.where(allowed, scores, -np.inf)
        k = min(k, int(np.isfinite(scores).sum()))
        if k <= 0:
            return []
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top], kind="stable")]
        return [
            ScoredChunk(self.chunks[i], float(scores[i]), {"bm25": float(scores[i])})
            for i in top
            if np.isfinite(scores[i]) and scores[i] > 0
        ]
