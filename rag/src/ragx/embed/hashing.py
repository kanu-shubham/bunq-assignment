"""Deterministic, offline, dependency-free embedder.

This is a *stand-in*, not a model: hashed unigrams + bigrams + character
4-grams with sublinear term frequency, L2 normalised. It gives stable, ordered
similarity for tests, CI, and the demo without a network call or an API key,
and it makes the hybrid-retrieval plumbing exercisable end to end.

In production you swap in `VoyageEmbedder` (or any hosted model) — nothing else
in the pipeline changes. Do not benchmark retrieval quality against this.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

from .base import Vector, l2_normalize

_WORD_RE = re.compile(r"[a-z0-9]+")


def _features(text: str) -> dict[str, float]:
    lowered = text.lower()
    words = _WORD_RE.findall(lowered)
    counts: dict[str, float] = {}
    for word in words:
        counts[f"w:{word}"] = counts.get(f"w:{word}", 0.0) + 1.0
    for a, b in zip(words, words[1:], strict=False):
        key = f"b:{a}_{b}"
        counts[key] = counts.get(key, 0.0) + 1.0
    squashed = re.sub(r"\s+", " ", lowered)
    for i in range(len(squashed) - 3):
        key = f"c:{squashed[i : i + 4]}"
        counts[key] = counts.get(key, 0.0) + 0.5
    return counts


class HashingEmbedder:
    def __init__(self, dim: int = 384, seed: int = 17) -> None:
        self.dim = dim
        self.seed = seed
        self.model_id = f"hashing-{dim}-v1"

    def _bucket(self, feature: str) -> tuple[int, float]:
        digest = hashlib.blake2b(
            feature.encode(), digest_size=8, key=str(self.seed).encode()
        ).digest()
        value = int.from_bytes(digest, "big")
        # Signed hashing keeps collisions from systematically inflating scores.
        return value % self.dim, 1.0 if (value >> 63) & 1 else -1.0

    def _embed(self, text: str) -> Vector:
        vec = [0.0] * self.dim
        for feature, count in _features(text).items():
            idx, sign = self._bucket(feature)
            vec[idx] += sign * (1.0 + math.log(count))
        return l2_normalize(vec)

    def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> Vector:
        return self._embed(text)
