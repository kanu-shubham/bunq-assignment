"""Dense index + the access filter every retrieval path must honour.

The in-memory store is exact brute-force cosine — correct, trivially testable,
and fine to a few hundred thousand chunks. It sits behind `VectorStore` so the
production swap (pgvector with HNSW, or a managed vector DB) is a single class.

The important part is not the ANN algorithm, it is `AccessFilter`: it is applied
**before** top-k, not after. Post-filtering a top-50 down to the documents a
user may see is how RAG systems quietly serve empty or truncated context to
exactly the users with the narrowest permissions — and how a
confidential chunk ends up in a prompt when someone later removes the filter.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from ..embed.base import Vector, cosine
from ..types import Chunk, Principal, Visibility

_VISIBILITY_ORDER = {
    Visibility.PUBLIC: 0,
    Visibility.INTERNAL: 1,
    Visibility.CONFIDENTIAL: 2,
}


@dataclass(frozen=True, slots=True)
class AccessFilter:
    tenant_id: str
    groups: frozenset[str] = frozenset()
    max_visibility: Visibility = Visibility.INTERNAL
    sources: frozenset[str] | None = None
    updated_after: datetime | None = None

    @staticmethod
    def for_principal(principal: Principal, **kwargs) -> AccessFilter:
        return AccessFilter(
            tenant_id=principal.tenant_id,
            groups=principal.groups,
            max_visibility=principal.max_visibility,
            **kwargs,
        )

    def matches(self, chunk: Chunk) -> bool:
        if chunk.tenant_id != self.tenant_id:
            return False
        if _VISIBILITY_ORDER[chunk.visibility] > _VISIBILITY_ORDER[self.max_visibility]:
            return False
        # An empty acl_groups set means "no group restriction beyond visibility".
        if chunk.acl_groups and not (chunk.acl_groups & self.groups):
            return False
        if self.sources is not None and chunk.source not in self.sources:
            return False
        if self.updated_after is not None and chunk.updated_at < self.updated_after:
            return False
        return True


@runtime_checkable
class VectorStore(Protocol):
    def upsert(self, chunks: Sequence[Chunk], vectors: Sequence[Vector]) -> None: ...

    def delete(self, chunk_ids: Iterable[str]) -> int: ...

    def search(
        self, query_vector: Vector, k: int, access: AccessFilter
    ) -> list[tuple[str, float]]: ...

    def get(self, chunk_id: str) -> Chunk | None: ...


@dataclass
class InMemoryVectorStore:
    """Exact cosine search. Also acts as the canonical chunk store."""

    model_id: str = "unknown"
    _chunks: dict[str, Chunk] = field(default_factory=dict)
    _vectors: dict[str, Vector] = field(default_factory=dict)

    def upsert(self, chunks: Sequence[Chunk], vectors: Sequence[Vector]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must be the same length")
        for chunk, vector in zip(chunks, vectors, strict=True):
            self._chunks[chunk.chunk_id] = chunk
            self._vectors[chunk.chunk_id] = vector

    def delete(self, chunk_ids: Iterable[str]) -> int:
        removed = 0
        for chunk_id in list(chunk_ids):
            if self._chunks.pop(chunk_id, None) is not None:
                self._vectors.pop(chunk_id, None)
                removed += 1
        return removed

    def search(
        self, query_vector: Vector, k: int, access: AccessFilter
    ) -> list[tuple[str, float]]:
        scored = (
            (chunk_id, cosine(query_vector, self._vectors[chunk_id]))
            for chunk_id, chunk in self._chunks.items()
            if access.matches(chunk)  # pre-filter, see module docstring
        )
        # Ties break on chunk_id so results are deterministic across runs.
        return heapq.nlargest(k, scored, key=lambda pair: (pair[1], pair[0]))

    def get(self, chunk_id: str) -> Chunk | None:
        return self._chunks.get(chunk_id)

    def all_chunks(self) -> list[Chunk]:
        return list(self._chunks.values())

    def doc_chunk_ids(self, doc_id: str) -> list[str]:
        return [c.chunk_id for c in self._chunks.values() if c.doc_id == doc_id]

    def vector(self, chunk_id: str) -> Vector | None:
        return self._vectors.get(chunk_id)

    def __len__(self) -> int:
        return len(self._chunks)
