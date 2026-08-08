"""Core domain types.

Everything that crosses a module boundary in this system is one of these.
They are frozen dataclasses so a chunk that has been indexed cannot be mutated
by a downstream stage.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal


class Visibility(StrEnum):
    """Coarse access tier. Fine-grained access lives in `Chunk.acl_groups`."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is asking. Every retrieval call requires one."""

    tenant_id: str
    subject_id: str
    groups: frozenset[str] = frozenset()
    max_visibility: Visibility = Visibility.INTERNAL


@dataclass(frozen=True, slots=True)
class Document:
    """A source document as pulled from a connector, before chunking."""

    doc_id: str
    tenant_id: str
    title: str
    text: str
    uri: str
    source: str = "unknown"  # confluence | gdrive | github | notion | ...
    visibility: Visibility = Visibility.INTERNAL
    acl_groups: frozenset[str] = frozenset()
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    version: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Chunk:
    """An indexed unit of retrieval.

    `text` is what gets shown to the answering model. `embed_text` is what gets
    embedded and BM25-indexed: it carries the contextualisation prefix (title +
    heading path) so a chunk that says "it must be approved within 30 days"
    still matches a query about expense reports.
    """

    chunk_id: str
    doc_id: str
    tenant_id: str
    text: str
    embed_text: str
    title: str
    uri: str
    heading_path: tuple[str, ...] = ()
    ordinal: int = 0
    token_count: int = 0
    content_hash: str = ""
    source: str = "unknown"
    visibility: Visibility = Visibility.INTERNAL
    acl_groups: frozenset[str] = frozenset()
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    kind: Literal["prose", "code", "table", "list"] = "prose"

    @property
    def display_path(self) -> str:
        return " › ".join((self.title, *self.heading_path))


RetrievalChannel = Literal["dense", "lexical", "fused", "rerank"]


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    """A chunk plus the provenance of *why* it is here.

    `component_scores` and `component_ranks` are kept all the way to the API
    response: without them, a bad answer is unattributable to a stage.
    """

    chunk: Chunk
    score: float
    channel: RetrievalChannel = "fused"
    component_scores: dict[str, float] = field(default_factory=dict)
    component_ranks: dict[str, int] = field(default_factory=dict)

    def with_score(self, score: float, channel: RetrievalChannel) -> ScoredChunk:
        return replace(self, score=score, channel=channel)


@dataclass(frozen=True, slots=True)
class Citation:
    """A validated pointer from a sentence of the answer back to a chunk."""

    marker: int  # the [n] the model wrote
    chunk_id: str
    doc_id: str
    title: str
    uri: str
    display_path: str
    quote: str | None = None


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_input_tokens + other.cache_read_input_tokens,
            self.cache_creation_input_tokens + other.cache_creation_input_tokens,
        )


@dataclass(frozen=True, slots=True)
class Answer:
    """The end-to-end result. `abstained` is a first-class outcome, not an error."""

    text: str
    citations: tuple[Citation, ...]
    contexts: tuple[ScoredChunk, ...]
    abstained: bool = False
    grounding_score: float = 1.0
    uncited_sentences: tuple[str, ...] = ()
    usage: Usage = field(default_factory=Usage)
    trace_id: str = ""
    stage_timings_ms: dict[str, float] = field(default_factory=dict)
    refusal: bool = False


@dataclass(frozen=True, slots=True)
class EvalCase:
    """One row of the golden set."""

    case_id: str
    question: str
    relevant_chunk_ids: tuple[str, ...] = ()
    relevant_doc_ids: tuple[str, ...] = ()
    reference_answer: str | None = None
    unanswerable: bool = False
    principal_groups: frozenset[str] = frozenset()
    principal_max_visibility: Visibility = Visibility.INTERNAL
    tags: tuple[str, ...] = ()


def dedupe_preserving_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def top_k(scored: Sequence[ScoredChunk], k: int) -> list[ScoredChunk]:
    return sorted(scored, key=lambda s: s.score, reverse=True)[:k]
