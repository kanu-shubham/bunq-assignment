"""Core data types shared by every stage of the pipeline.

Everything downstream (indexes, rerankers, caches, evaluation) speaks in terms
of these three types, which is what lets the stages be swapped independently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class Document:
    """One source document — a Notion page, a Confluence page, a README."""

    doc_id: str
    title: str
    text: str
    path: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Chunk:
    """A retrievable unit.

    ``body`` is the raw chunk text. ``context_prefix`` carries the document
    title and heading path. They are kept separate because the two indexes want
    them weighted differently: BM25 boosts prefix terms, the embedder simply
    concatenates. Losing that separation is one of the quieter ways a RAG
    system gives up recall.
    """

    chunk_id: str
    doc_id: str
    ordinal: int
    body: str
    context_prefix: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return f"{self.context_prefix}\n\n{self.body}" if self.context_prefix else self.body

    def __len__(self) -> int:  # convenience for size reporting
        return len(self.body)


@dataclass
class ScoredChunk:
    """A chunk with a score and the per-stage components that produced it.

    ``components`` is deliberately open: each stage writes its own key
    (``bm25``, ``dense``, ``rrf``, ``rerank``, ...). Being able to read the
    breakdown after the fact is the difference between debugging a retrieval
    regression in ten minutes and rebuilding the pipeline from scratch.
    """

    chunk: Chunk
    score: float
    components: dict[str, float] = field(default_factory=dict)

    @property
    def doc_id(self) -> str:
        return self.chunk.doc_id

    @property
    def chunk_id(self) -> str:
        return self.chunk.chunk_id


@dataclass(frozen=True)
class EvalQuery:
    """A labelled query: the text plus the documents that genuinely answer it."""

    query_id: str
    text: str
    gold_doc_ids: frozenset[str]
    category: str
    filters: Mapping[str, Any] = field(default_factory=dict)
    note: str = ""


@dataclass
class RetrievalResult:
    """What a pipeline hands back, including the trace of how it got there."""

    query: str
    rewritten_queries: Sequence[str]
    chunks: list[ScoredChunk]
    trace: dict[str, Any] = field(default_factory=dict)

    def doc_ids(self) -> list[str]:
        """Deduplicated document ids in rank order."""
        seen: list[str] = []
        for sc in self.chunks:
            if sc.doc_id not in seen:
                seen.append(sc.doc_id)
        return seen
