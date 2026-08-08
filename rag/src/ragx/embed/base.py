"""Embedder protocol + caching wrapper.

Anthropic does not serve an embeddings endpoint, so the dense side of this
system is deliberately provider-agnostic: implement `Embedder` and the rest of
the pipeline does not change. Two implementations ship here — a deterministic
offline one (`HashingEmbedder`, used by tests and the demo) and a real hosted
one (`VoyageEmbedder`).

`embed_documents` and `embed_query` are separate methods because most modern
embedding models are asymmetric: they take an input-type hint and produce
measurably better retrieval when you use it.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

Vector = list[float]


@runtime_checkable
class Embedder(Protocol):
    dim: int
    model_id: str

    def embed_documents(self, texts: Sequence[str]) -> list[Vector]: ...

    def embed_query(self, text: str) -> Vector: ...


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=False):  # tolerate a stale-dim vector rather than raising mid-query
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / math.sqrt(na * nb)


def l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [v / norm for v in vec]


class CachingEmbedder:
    """Content-addressed embedding cache.

    Re-ingesting a 40k-document corpus after editing three files should cost
    three embedding calls, not 40k. The key is the model id plus the content
    hash, so a model upgrade correctly misses every entry.
    """

    def __init__(self, inner: Embedder, store: dict[str, Vector] | None = None) -> None:
        self._inner = inner
        self._store: dict[str, Vector] = store if store is not None else {}
        self.dim = inner.dim
        self.model_id = inner.model_id
        self.hits = 0
        self.misses = 0

    def _key(self, text: str) -> str:
        return f"{self.model_id}:{hashlib.sha256(text.encode()).hexdigest()}"

    def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        keys = [self._key(t) for t in texts]
        missing_idx = [i for i, k in enumerate(keys) if k not in self._store]
        self.hits += len(texts) - len(missing_idx)
        self.misses += len(missing_idx)
        if missing_idx:
            fresh = self._inner.embed_documents([texts[i] for i in missing_idx])
            for i, vec in zip(missing_idx, fresh, strict=True):
                self._store[keys[i]] = vec
        return [self._store[k] for k in keys]

    def embed_query(self, text: str) -> Vector:
        return self._inner.embed_query(text)
