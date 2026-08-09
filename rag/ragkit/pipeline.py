"""The composed retrieval pipeline.

Every stage is behind a flag so the ablation can turn one thing on at a time and
attribute the change to it. That is not a testing convenience — it is the only
way to know whether the reranker you shipped is doing anything, and the usual
alternative (turn everything on, observe that it works, ship) is how teams end
up paying for a reranker that costs 80ms and moves nothing.

Order of operations, and why:

    query
      -> semantic cache lookup          (skip everything on a hit)
      -> query rewriting                (raises the recall ceiling)
      -> BM25 + dense, per variant      (candidate generation, deep)
      -> rank fusion                    (scale-free combination)
      -> per-document diversity cap     (stop one doc eating the context)
      -> cross-encoder re-ranking       (precision, on a short list)
      -> top-k

Rewriting comes before retrieval because it can surface documents nothing else
would find. Re-ranking comes last because it is the expensive per-pair stage and
should only ever see a short list.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from .cache import SemanticCache
from .chunking import ChunkConfig, chunk_corpus
from .dense import DenseIndex
from .embeddings import Embedder, HashingEmbedder
from .filters import MetadataFilter
from .hybrid import FusionMethod, HybridRetriever, dedupe_by_document
from .lexical import BM25Index
from .query_rewrite import NoOpRewriter, QueryRewriter, RuleBasedRewriter
from .rerank import LexicalCrossEncoder, NoOpReranker, Reranker
from .types import Chunk, Document, RetrievalResult, ScoredChunk


@dataclass
class PipelineConfig:
    """Every knob, in one place, with the defaults the ablation recommends."""

    top_k: int = 5
    candidate_k: int = 50
    fusion: FusionMethod = "rrf"
    use_rewrite: bool = True
    use_rerank: bool = True
    rerank_depth: int = 30  # how many candidates the cross-encoder sees
    max_per_doc: int = 2
    use_cache: bool = False
    cache_threshold: float = 0.90
    cache_ttl_seconds: float | None = 3600.0
    chunk: ChunkConfig = field(default_factory=ChunkConfig)

    def label(self) -> str:
        parts = [self.fusion]
        if self.use_rewrite:
            parts.append("+rewrite")
        if self.use_rerank:
            parts.append("+rerank")
        if self.use_cache:
            parts.append("+cache")
        return " ".join(parts)


class RagPipeline:
    """Retrieval only. Answer generation lives in ``answer.py``."""

    def __init__(
        self,
        documents: Sequence[Document],
        chunks: Sequence[Chunk],
        embedder: Embedder,
        bm25: BM25Index,
        dense: DenseIndex,
        config: PipelineConfig,
        *,
        rewriter: QueryRewriter | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.documents = {d.doc_id: d for d in documents}
        self.chunks = list(chunks)
        self.embedder = embedder
        self.bm25 = bm25
        self.dense = dense
        self.config = config
        self.rewriter = rewriter or (RuleBasedRewriter() if config.use_rewrite else NoOpRewriter())
        self.reranker = reranker or (
            LexicalCrossEncoder.from_embedder(embedder) if config.use_rerank else NoOpReranker()
        )
        self.retriever = HybridRetriever(
            bm25=bm25,
            dense=dense,
            method=config.fusion,
            candidate_k=config.candidate_k,
        )
        self.cache: SemanticCache[RetrievalResult] | None = (
            SemanticCache(
                embedder,
                threshold=config.cache_threshold,
                ttl_seconds=config.cache_ttl_seconds,
            )
            if config.use_cache
            else None
        )

    # ---------------------------------------------------------------- build

    @classmethod
    def build(
        cls,
        documents: Sequence[Document],
        config: PipelineConfig | None = None,
        *,
        embedder: Embedder | None = None,
        rewriter: QueryRewriter | None = None,
        reranker: Reranker | None = None,
    ) -> "RagPipeline":
        config = config or PipelineConfig()
        chunks = chunk_corpus(documents, config.chunk)
        if embedder is None:
            embedder = HashingEmbedder()
            embedder.fit(c.text for c in chunks)
        bm25 = BM25Index(chunks)
        dense = DenseIndex(chunks, embedder)
        return cls(
            documents, chunks, embedder, bm25, dense, config,
            rewriter=rewriter, reranker=reranker,
        )

    def with_config(self, config: PipelineConfig, **kwargs) -> "RagPipeline":
        """Rebuild the cheap parts against a new config, reusing the indexes.

        Chunking and embedding are the expensive steps and they do not depend on
        the retrieval flags, so an ablation over ten configurations builds the
        index once.
        """
        if config.chunk != self.config.chunk:
            raise ValueError("chunk config differs — rebuild with .build() instead")
        return RagPipeline(
            list(self.documents.values()), self.chunks, self.embedder,
            self.bm25, self.dense, config, **kwargs,
        )

    # ------------------------------------------------------------- retrieve

    def retrieve(
        self,
        query: str,
        k: int | None = None,
        *,
        metadata_filter: MetadataFilter | None = None,
    ) -> RetrievalResult:
        k = k or self.config.top_k
        cfg = self.config
        scope = metadata_filter.cache_key() if metadata_filter else ""
        started = time.perf_counter()

        nearest_similarity = 0.0
        if self.cache is not None:
            lookup = self.cache.get(query, scope=scope)
            # Record the near-miss similarity even when we miss: the distance
            # between "just missed" and "nowhere near" is what tells you whether
            # the threshold is wrong or the cache is simply cold.
            nearest_similarity = lookup.similarity
            if lookup.hit and lookup.value is not None:
                cached = lookup.value
                trace = dict(cached.trace)
                trace.update(
                    cache={"hit": True, "kind": lookup.kind, "similarity": lookup.similarity,
                           "matched_query": lookup.matched_query},
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
                return RetrievalResult(query, cached.rewritten_queries, list(cached.chunks), trace)

        rewrite = self.rewriter.rewrite(query)
        queries = rewrite.queries
        # A model-backed rewriter can also extract filters; an explicit filter
        # from the caller always wins over an inferred one.
        if metadata_filter is None and rewrite.filters:
            metadata_filter = MetadataFilter.parse(dict(rewrite.filters))

        rewrite_ms = (time.perf_counter() - started) * 1000

        depth = max(cfg.candidate_k, cfg.rerank_depth, k)
        t0 = time.perf_counter()
        if len(queries) == 1:
            candidates = self.retriever.search(queries[0], depth, metadata_filter=metadata_filter)
        else:
            candidates = self.retriever.search_multi(queries, depth, metadata_filter=metadata_filter)
        retrieve_ms = (time.perf_counter() - t0) * 1000

        if cfg.max_per_doc > 0:
            candidates = dedupe_by_document(candidates, cfg.max_per_doc)

        t0 = time.perf_counter()
        if cfg.use_rerank:
            final = self.reranker.rerank(query, candidates[: cfg.rerank_depth], k)
        else:
            final = list(candidates[:k])
        rerank_ms = (time.perf_counter() - t0) * 1000

        result = RetrievalResult(
            query=query,
            rewritten_queries=queries,
            chunks=final,
            trace={
                "cache": {"hit": False, "nearest_similarity": round(nearest_similarity, 4)},
                "n_variants": len(queries),
                "n_candidates": len(candidates),
                "rewrite_ms": round(rewrite_ms, 2),
                "retrieve_ms": round(retrieve_ms, 2),
                "rerank_ms": round(rerank_ms, 2),
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "filter": metadata_filter.cache_key() if metadata_filter else None,
                "config": cfg.label(),
            },
        )
        if self.cache is not None:
            self.cache.put(query, result, scope=scope)
        return result

    # ------------------------------------------------------- tool adapters

    def tool_search_docs(
        self,
        query: str,
        top_k: int = 5,
        doc_type: str | None = None,
        team: str | None = None,
    ) -> list[dict[str, Any]]:
        equals = {k: v for k, v in (("doc_type", doc_type), ("team", team)) if v}
        result = self.retrieve(query, top_k, metadata_filter=MetadataFilter(equals=equals) if equals else None)
        return [
            {
                "doc_id": sc.doc_id,
                "chunk_id": sc.chunk_id,
                "title": str(sc.chunk.metadata.get("title", "")),
                "heading": sc.chunk.context_prefix,
                "doc_type": str(sc.chunk.metadata.get("doc_type", "")),
                "team": str(sc.chunk.metadata.get("team", "")),
                "updated": str(sc.chunk.metadata.get("updated", "")),
                "score": round(sc.score, 4),
                "text": sc.chunk.body,
            }
            for sc in result.chunks
        ]

    def tool_fetch_document(self, doc_id: str) -> dict[str, Any]:
        doc = self.documents.get(doc_id)
        if doc is None:
            from .tools import ToolExecutionError

            raise ToolExecutionError(
                f"no document with id {doc_id!r}. Use search_docs to find a valid id."
            )
        return {
            "doc_id": doc.doc_id,
            "title": doc.title,
            "metadata": dict(doc.metadata),
            "text": doc.text,
        }

    def tool_define_term(self, term: str) -> dict[str, Any]:
        glossary = self.documents.get("glossary")
        if glossary is None:
            return {"term": term, "definition": None, "found": False}
        needle = term.strip().lower()
        for line in glossary.text.splitlines():
            if line.startswith("**") and needle in line.lower().split("**")[1].lower():
                return {"term": term, "definition": line.strip("* ").strip(), "found": True}
        return {"term": term, "definition": None, "found": False,
                "hint": "Not in the glossary. Try search_docs instead."}

    def tool_list_documents(
        self, doc_type: str | None = None, team: str | None = None
    ) -> list[dict[str, str]]:
        out = []
        for doc in self.documents.values():
            if doc_type and str(doc.metadata.get("doc_type")) != doc_type:
                continue
            if team and str(doc.metadata.get("team")) != team:
                continue
            out.append(
                {
                    "doc_id": doc.doc_id,
                    "title": doc.title,
                    "doc_type": str(doc.metadata.get("doc_type", "")),
                    "team": str(doc.metadata.get("team", "")),
                }
            )
        return sorted(out, key=lambda d: d["doc_id"])

    def build_tool_registry(self):
        from .tools import build_registry

        return build_registry(
            self.tool_search_docs,
            self.tool_fetch_document,
            self.tool_define_term,
            self.tool_list_documents,
        )

    # ------------------------------------------------------------ reporting

    def stats(self) -> dict[str, Any]:
        lengths = [len(c.body) for c in self.chunks]
        return {
            "documents": len(self.documents),
            "chunks": len(self.chunks),
            "chunks_per_doc": round(len(self.chunks) / max(1, len(self.documents)), 2),
            "mean_chunk_chars": round(sum(lengths) / max(1, len(lengths))),
            "max_chunk_chars": max(lengths) if lengths else 0,
            "embedding_dim": self.dense.dim,
            "bm25_backend": self.bm25.backend,
        }
