"""The retrieval stage: dense + lexical, fused, capped, optionally diversified."""

from __future__ import annotations

from dataclasses import dataclass

from .config import RetrievalConfig
from .embed.base import Embedder
from .index.bm25 import BM25Index
from .index.hybrid import RetrievalDebug, cap_per_document, fuse, mmr
from .index.vector_store import AccessFilter, InMemoryVectorStore
from .obs import metrics
from .obs.trace import Timings
from .types import ScoredChunk


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    candidates: tuple[ScoredChunk, ...]
    debug: RetrievalDebug


class HybridRetriever:
    def __init__(
        self,
        embedder: Embedder,
        vector_store: InMemoryVectorStore,
        bm25: BM25Index,
        config: RetrievalConfig,
    ) -> None:
        self.embedder = embedder
        self.vector_store = vector_store
        self.bm25 = bm25
        self.config = config

    def retrieve(
        self, query: str, access: AccessFilter, timings: Timings | None = None
    ) -> RetrievalResult:
        timings = timings or Timings()
        cfg = self.config

        with timings.stage("retrieve.dense"):
            query_vector = self.embedder.embed_query(query)
            dense_hits = self.vector_store.search(query_vector, cfg.dense_k, access)

        with timings.stage("retrieve.lexical"):
            lexical_hits = self.bm25.search(query, cfg.lexical_k, access)

        with timings.stage("retrieve.fuse"):
            fused = fuse(
                {"dense": dense_hits, "lexical": lexical_hits},
                cfg,
                resolve=self.vector_store.get,
            )
            capped = cap_per_document(fused, cfg.per_doc_cap)
            candidates = capped[: cfg.candidates_k]
            if cfg.mmr_lambda > 0:
                vectors = {
                    c.chunk.chunk_id: v
                    for c in candidates
                    if (v := self.vector_store.vector(c.chunk.chunk_id)) is not None
                }
                candidates = mmr(candidates, vectors, cfg.candidates_k, cfg.mmr_lambda)

        metrics.incr("retrieval_requests_total")
        metrics.observe("retrieval_candidates", len(candidates))
        if not candidates:
            metrics.incr("retrieval_empty_total")

        return RetrievalResult(
            candidates=tuple(candidates),
            debug=RetrievalDebug(
                dense_hits=len(dense_hits),
                lexical_hits=len(lexical_hits),
                fused_hits=len(fused),
                after_cap=len(capped),
                fusion=cfg.fusion,
            ),
        )
