"""Composition root.

One place that knows which implementation of each protocol is wired in, so the
rest of the code depends only on the protocols and every stage can be swapped
(or faked) without touching a call site.

`offline=True` builds a system with no network dependency and no API key: the
hashing embedder plus a lexical reranker plus a scripted LLM. That is what CI,
the unit tests, and `make demo` run on.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import RERANK_MODEL, Config
from .embed.base import CachingEmbedder, Embedder
from .embed.hashing import HashingEmbedder
from .generate.answerer import Answerer
from .index.bm25 import BM25Index
from .index.vector_store import InMemoryVectorStore
from .ingest.loaders import load_directory
from .ingest.pipeline import IngestPipeline
from .llm import LLM, AnthropicLLM, ScriptedLLM
from .pipeline import RagPipeline
from .rerank.base import LexicalOverlapReranker, NoopReranker, Reranker
from .rerank.llm_reranker import LLMReranker
from .retriever import HybridRetriever


@dataclass
class RagSystem:
    config: Config
    embedder: Embedder
    vector_store: InMemoryVectorStore
    bm25: BM25Index
    ingest: IngestPipeline
    retriever: HybridRetriever
    reranker: Reranker
    answerer: Answerer
    pipeline: RagPipeline
    llm: LLM

    def ingest_directory(self, root: Path, tenant_id: str) -> None:
        self.ingest.ingest(load_directory(root, tenant_id=tenant_id))


def build(
    config: Config | None = None,
    *,
    offline: bool = False,
    embedder: Embedder | None = None,
    llm: LLM | None = None,
    scripted_handler: Callable[[str, str], str] | None = None,
) -> RagSystem:
    config = config or Config()

    if embedder is None:
        embedder = HashingEmbedder(dim=config.embedding_dim)
    embedder = CachingEmbedder(embedder)

    if llm is None:
        llm = (
            ScriptedLLM(scripted_handler or _default_offline_handler)
            if offline
            else AnthropicLLM(
                timeout_s=config.generation.timeout_s,
                use_server_fallback=config.generation.use_server_fallback,
            )
        )

    vector_store = InMemoryVectorStore(model_id=embedder.model_id)
    bm25 = BM25Index()
    ingest = IngestPipeline(embedder, vector_store, bm25, config.chunking)
    retriever = HybridRetriever(embedder, vector_store, bm25, config.retrieval)

    if not config.rerank.enabled:
        reranker: Reranker = NoopReranker()
    elif offline:
        reranker = LexicalOverlapReranker()
    else:
        reranker = LLMReranker(llm, RERANK_MODEL, config.rerank)

    answerer = Answerer(llm, config.generation)
    pipeline = RagPipeline(retriever, reranker, answerer, config)

    return RagSystem(
        config=config,
        embedder=embedder,
        vector_store=vector_store,
        bm25=bm25,
        ingest=ingest,
        retriever=retriever,
        reranker=reranker,
        answerer=answerer,
        pipeline=pipeline,
        llm=llm,
    )


def _default_offline_handler(model: str, prompt: str) -> str:
    """A stand-in 'model' that quotes the first source back with a citation.

    It exists so the offline demo produces a shaped, verifiable answer — not to
    simulate answer quality.
    """
    if "Sources: none were retrieved" in prompt:
        return "INSUFFICIENT_CONTEXT\nNo documentation was retrieved for this question."
    first = prompt.split("[1] ", 1)[-1].split("\n", 1)
    body = first[1].strip().split("\n\n")[0] if len(first) > 1 else ""
    sentence = body.split(". ")[0].strip()
    if not sentence:
        return "INSUFFICIENT_CONTEXT\nThe retrieved sources were empty."
    return f"{sentence}. [1]"
