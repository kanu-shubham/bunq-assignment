"""Ingestion pipeline: documents in, indices updated, incrementally.

Full reindexing is the default in most RAG demos and the first thing to break
in production: a 40k-document corpus re-embedded on every sync is expensive,
slow, and — because it is slow — run rarely, which is how the index goes stale.

This pipeline diffs at chunk granularity using content hashes:
  * unchanged chunk  -> no embedding call, no write
  * edited chunk     -> new content hash, therefore new chunk id, upserted
  * deleted section  -> chunk id disappears from the document, tombstoned
  * deleted document -> all of its chunk ids deleted from both indices

Both indices are written in the same call so dense and lexical never disagree
about what exists — index skew shows up as citations pointing at chunks the
answer stage cannot resolve.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from ..config import ChunkingConfig
from ..embed.base import Embedder
from ..index.bm25 import BM25Index
from ..index.vector_store import InMemoryVectorStore
from ..obs import metrics
from ..obs.trace import log
from ..types import Chunk, Document
from .chunker import chunk_document


@dataclass
class IngestStats:
    documents: int = 0
    chunks_created: int = 0
    chunks_unchanged: int = 0
    chunks_deleted: int = 0
    embed_calls: int = 0
    docs_deleted: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "documents": self.documents,
            "chunks_created": self.chunks_created,
            "chunks_unchanged": self.chunks_unchanged,
            "chunks_deleted": self.chunks_deleted,
            "embed_calls": self.embed_calls,
            "docs_deleted": self.docs_deleted,
            "errors": self.errors,
        }


class IngestPipeline:
    def __init__(
        self,
        embedder: Embedder,
        vector_store: InMemoryVectorStore,
        bm25: BM25Index,
        chunking: ChunkingConfig,
    ) -> None:
        self.embedder = embedder
        self.vector_store = vector_store
        self.bm25 = bm25
        self.chunking = chunking

    def ingest(self, documents: Iterable[Document]) -> IngestStats:
        stats = IngestStats()
        for doc in documents:
            try:
                self._ingest_one(doc, stats)
            except Exception as exc:  # noqa: BLE001 — one bad doc must not stop a sync
                stats.errors.append(f"{doc.doc_id}: {type(exc).__name__}: {exc}")
                metrics.incr("ingest_errors_total", source=doc.source)
                log("ingest_error", doc_id=doc.doc_id, error=str(exc))
        metrics.incr("ingest_documents_total", stats.documents)
        metrics.incr("ingest_chunks_total", stats.chunks_created)
        log("ingest_complete", **stats.as_dict())
        return stats

    def _ingest_one(self, doc: Document, stats: IngestStats) -> None:
        chunks = chunk_document(doc, self.chunking)
        existing = set(self.vector_store.doc_chunk_ids(doc.doc_id))
        incoming = {c.chunk_id: c for c in chunks}

        new_ids = [cid for cid in incoming if cid not in existing]
        stale_ids = [cid for cid in existing if cid not in incoming]

        new_chunks = [incoming[cid] for cid in new_ids]
        if new_chunks:
            vectors = self.embedder.embed_documents([c.embed_text for c in new_chunks])
            self.vector_store.upsert(new_chunks, vectors)
            self.bm25.upsert(new_chunks)
            stats.embed_calls += 1

        if stale_ids:
            self.vector_store.delete(stale_ids)
            self.bm25.delete(stale_ids)

        stats.documents += 1
        stats.chunks_created += len(new_ids)
        stats.chunks_unchanged += len(incoming) - len(new_ids)
        stats.chunks_deleted += len(stale_ids)

    def delete_documents(self, doc_ids: Sequence[str]) -> IngestStats:
        """Deletion must be as cheap and as reliable as insertion. A deleted
        source document that lingers in the index is a compliance incident, not
        a quality issue."""
        stats = IngestStats()
        for doc_id in doc_ids:
            chunk_ids = self.vector_store.doc_chunk_ids(doc_id)
            self.vector_store.delete(chunk_ids)
            self.bm25.delete(chunk_ids)
            stats.chunks_deleted += len(chunk_ids)
            stats.docs_deleted += 1
        log("ingest_delete", **stats.as_dict())
        return stats

    def all_chunks(self) -> list[Chunk]:
        return self.vector_store.all_chunks()
