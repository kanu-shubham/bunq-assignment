"""Semantic caching.

An exact-match cache on the query string catches almost nothing: real users do
not repeat themselves byte-for-byte. A semantic cache embeds the query and
returns a stored answer when a previous query is close enough in vector space.
On a support-style workload where the same twenty questions arrive in a hundred
phrasings, this is the single largest latency and cost win available.

It is also the component most likely to serve a confidently wrong answer, so
three things are non-negotiable and all three are implemented here:

1. **The similarity threshold is a correctness knob, not a hit-rate knob.**
   "How do I roll back a deploy" and "how do I roll back a migration" are close
   in any embedding space and have different answers. ``measure_false_hits``
   exists so the threshold is chosen from data rather than from optimism.
2. **Filters and scope are part of the key.** The same question asked with
   ``team=payments`` is a different question. Caching across a scope boundary is
   a data-leak bug, not a tuning issue.
3. **Entries expire.** Documentation changes. A cache with no TTL is a slow way
   to serve last quarter's runbook.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, Iterable, Sequence, TypeVar

import numpy as np

from .embeddings import Embedder
from .textutil import normalize

V = TypeVar("V")


@dataclass
class CacheEntry(Generic[V]):
    query: str
    scope: str
    value: V
    created_at: float
    last_used_at: float
    hits: int = 0


@dataclass
class CacheStats:
    lookups: int = 0
    exact_hits: int = 0
    semantic_hits: int = 0
    misses: int = 0
    evictions: int = 0
    expirations: int = 0
    similarity_of_hits: list[float] = field(default_factory=list)

    @property
    def hits(self) -> int:
        return self.exact_hits + self.semantic_hits

    @property
    def hit_rate(self) -> float:
        return self.hits / self.lookups if self.lookups else 0.0

    def as_dict(self) -> dict[str, Any]:
        sims = self.similarity_of_hits
        return {
            "lookups": self.lookups,
            "hits": self.hits,
            "exact_hits": self.exact_hits,
            "semantic_hits": self.semantic_hits,
            "misses": self.misses,
            "hit_rate": round(self.hit_rate, 4),
            "evictions": self.evictions,
            "expirations": self.expirations,
            "mean_hit_similarity": round(float(np.mean(sims)), 4) if sims else None,
            "min_hit_similarity": round(float(np.min(sims)), 4) if sims else None,
        }


@dataclass
class CacheLookup(Generic[V]):
    hit: bool
    value: V | None = None
    kind: str = "miss"  # miss | exact | semantic
    similarity: float = 0.0
    matched_query: str | None = None


class SemanticCache(Generic[V]):
    """Embedding-similarity cache with exact fast path, TTL, and LRU eviction."""

    def __init__(
        self,
        embedder: Embedder,
        *,
        threshold: float = 0.90,
        max_entries: int = 1000,
        ttl_seconds: float | None = 3600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0.0 < threshold <= 1.0:
            raise ValueError("threshold must be in (0, 1]")
        self.embedder = embedder
        self.threshold = threshold
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: list[CacheEntry[V]] = []
        self._vectors: np.ndarray = np.zeros((0, embedder.dim), dtype=np.float32)
        self._exact: dict[tuple[str, str], int] = {}
        self.stats = CacheStats()

    # -------------------------------------------------------------- internals

    @staticmethod
    def _exact_key(query: str, scope: str) -> tuple[str, str]:
        return (" ".join(normalize(query).split()), scope)

    def _expired(self, entry: CacheEntry[V]) -> bool:
        return self.ttl_seconds is not None and (self._clock() - entry.created_at) > self.ttl_seconds

    def _rebuild(self, keep: Sequence[int]) -> None:
        self._entries = [self._entries[i] for i in keep]
        self._vectors = self._vectors[list(keep)] if keep else np.zeros(
            (0, self.embedder.dim), dtype=np.float32
        )
        self._exact = {
            self._exact_key(e.query, e.scope): i for i, e in enumerate(self._entries)
        }

    def _purge_expired(self) -> None:
        if self.ttl_seconds is None or not self._entries:
            return
        keep = [i for i, e in enumerate(self._entries) if not self._expired(e)]
        if len(keep) != len(self._entries):
            self.stats.expirations += len(self._entries) - len(keep)
            self._rebuild(keep)

    def _evict_lru(self) -> None:
        while len(self._entries) > self.max_entries:
            victim = min(range(len(self._entries)), key=lambda i: self._entries[i].last_used_at)
            keep = [i for i in range(len(self._entries)) if i != victim]
            self.stats.evictions += 1
            self._rebuild(keep)

    # ------------------------------------------------------------------- API

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, query: str, *, scope: str = "") -> CacheLookup[V]:
        self.stats.lookups += 1
        self._purge_expired()

        idx = self._exact.get(self._exact_key(query, scope))
        if idx is not None:
            entry = self._entries[idx]
            entry.hits += 1
            entry.last_used_at = self._clock()
            self.stats.exact_hits += 1
            self.stats.similarity_of_hits.append(1.0)
            return CacheLookup(True, entry.value, "exact", 1.0, entry.query)

        if not self._entries:
            self.stats.misses += 1
            return CacheLookup(False)

        qvec = self.embedder.encode([query])[0]
        sims = self._vectors @ qvec
        # Scope is part of the key: never match across scopes, whatever the
        # similarity says.
        for i, entry in enumerate(self._entries):
            if entry.scope != scope:
                sims[i] = -1.0

        best = int(np.argmax(sims))
        best_sim = float(sims[best])
        if best_sim >= self.threshold:
            entry = self._entries[best]
            entry.hits += 1
            entry.last_used_at = self._clock()
            self.stats.semantic_hits += 1
            self.stats.similarity_of_hits.append(best_sim)
            return CacheLookup(True, entry.value, "semantic", best_sim, entry.query)

        self.stats.misses += 1
        return CacheLookup(False, similarity=max(0.0, best_sim))

    def put(self, query: str, value: V, *, scope: str = "") -> None:
        now = self._clock()
        key = self._exact_key(query, scope)
        existing = self._exact.get(key)
        if existing is not None:
            self._entries[existing] = CacheEntry(query, scope, value, now, now)
            self._vectors[existing] = self.embedder.encode([query])[0]
            return
        self._entries.append(CacheEntry(query, scope, value, now, now))
        vec = self.embedder.encode([query])
        self._vectors = np.vstack([self._vectors, vec]) if len(self._vectors) else vec
        self._exact[key] = len(self._entries) - 1
        self._evict_lru()

    def clear(self) -> None:
        self._rebuild([])
        self.stats = CacheStats()

    def entries(self) -> list[CacheEntry[V]]:
        return list(self._entries)


def measure_false_hits(
    cache: SemanticCache[Any],
    pairs: Iterable[tuple[str, frozenset[str]]],
    *,
    thresholds: Sequence[float] = (0.80, 0.85, 0.90, 0.95, 0.98),
    embedder: Embedder | None = None,
) -> list[dict[str, Any]]:
    """Sweep the threshold and report hit rate *against* false-hit rate.

    ``pairs`` is ``(query, gold_doc_ids)``. A cache hit is counted as *false*
    when the matched query's gold set is disjoint from this query's — i.e. the
    cache would have served an answer about something else. This is the curve
    you need before picking a threshold, and it is the reason the default here
    is a conservative 0.90 rather than the 0.8 that maximises hit rate.
    """
    embedder = embedder or cache.embedder
    pairs = list(pairs)
    queries = [q for q, _ in pairs]
    golds = [g for _, g in pairs]
    vectors = embedder.encode(queries)

    rows: list[dict[str, Any]] = []
    for threshold in thresholds:
        hits = false_hits = 0
        stored: list[int] = []
        for i in range(len(pairs)):
            if stored:
                sims = vectors[stored] @ vectors[i]
                best_local = int(np.argmax(sims))
                if float(sims[best_local]) >= threshold:
                    hits += 1
                    matched = stored[best_local]
                    if not (golds[i] & golds[matched]):
                        false_hits += 1
                    continue
            stored.append(i)
        rows.append(
            {
                "threshold": threshold,
                "hit_rate": round(hits / len(pairs), 4),
                "false_hit_rate": round(false_hits / len(pairs), 4),
                "false_hits_among_hits": round(false_hits / hits, 4) if hits else 0.0,
                "hits": hits,
                "false_hits": false_hits,
            }
        )
    return rows
