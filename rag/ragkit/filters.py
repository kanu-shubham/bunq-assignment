"""Metadata filtering.

Filters are compiled once into a boolean mask over the chunk array and handed to
the indexes, which apply them *before* top-k selection. The alternative —
retrieve k, then drop non-matching results — is the most common bug in
production RAG: with a filter that matches 5% of the corpus, a post-filtered
top-10 usually returns zero rows, and the failure looks like "the retriever
found nothing" rather than "the filter was applied in the wrong place".

Filters also have to participate in the semantic cache key. Two identical
queries under different filters are different questions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from .types import Chunk


@dataclass(frozen=True)
class MetadataFilter:
    """A conjunction of metadata predicates.

    ``equals``      field must equal the value
    ``any_of``      field must be one of the values
    ``has_tags``    the chunk's ``tags`` list must contain all of these
    ``updated_after`` / ``updated_before``  ISO date bounds on ``updated``
    """

    equals: Mapping[str, Any] = field(default_factory=dict)
    any_of: Mapping[str, Sequence[Any]] = field(default_factory=dict)
    has_tags: Sequence[str] = field(default_factory=tuple)
    updated_after: str | None = None
    updated_before: str | None = None

    def __bool__(self) -> bool:
        return bool(
            self.equals or self.any_of or self.has_tags or self.updated_after or self.updated_before
        )

    def matches(self, chunk: Chunk) -> bool:
        meta = chunk.metadata
        for key, value in self.equals.items():
            if str(meta.get(key)) != str(value):
                return False
        for key, values in self.any_of.items():
            if str(meta.get(key)) not in {str(v) for v in values}:
                return False
        if self.has_tags:
            tags = {str(t).lower() for t in meta.get("tags", [])}
            if not {t.lower() for t in self.has_tags}.issubset(tags):
                return False
        updated = str(meta.get("updated", ""))
        if self.updated_after and updated and updated < self.updated_after:
            return False
        if self.updated_before and updated and updated > self.updated_before:
            return False
        return True

    def mask(self, chunks: Sequence[Chunk]) -> np.ndarray | None:
        """Boolean mask, or ``None`` when the filter is empty (skip the work)."""
        if not self:
            return None
        return np.fromiter((self.matches(c) for c in chunks), dtype=bool, count=len(chunks))

    def cache_key(self) -> str:
        """Stable string for use inside a cache key. Sorted, so it is canonical."""
        payload = {
            "equals": dict(sorted(self.equals.items())),
            "any_of": {k: sorted(map(str, v)) for k, v in sorted(self.any_of.items())},
            "has_tags": sorted(self.has_tags),
            "updated_after": self.updated_after,
            "updated_before": self.updated_before,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @classmethod
    def parse(cls, spec: Mapping[str, Any] | None) -> "MetadataFilter":
        if not spec:
            return cls()
        known = {"equals", "any_of", "has_tags", "updated_after", "updated_before"}
        unknown = set(spec) - known
        if unknown:
            # Shorthand: a flat mapping is treated as `equals`. Convenient in
            # eval files, and unambiguous because the keys never collide.
            if not (set(spec) & known):
                return cls(equals=dict(spec))
            raise ValueError(f"unknown filter keys: {sorted(unknown)}")
        return cls(
            equals=dict(spec.get("equals", {})),
            any_of={k: list(v) for k, v in spec.get("any_of", {}).items()},
            has_tags=tuple(spec.get("has_tags", ())),
            updated_after=spec.get("updated_after"),
            updated_before=spec.get("updated_before"),
        )


def selectivity(f: MetadataFilter, chunks: Iterable[Chunk]) -> float:
    """Fraction of the corpus a filter admits. Below ~0.02, reconsider top-k."""
    chunks = list(chunks)
    if not chunks:
        return 0.0
    m = f.mask(chunks)
    return 1.0 if m is None else float(m.mean())
