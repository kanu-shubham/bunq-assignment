"""ragkit — a production-shaped RAG stack, built to be measured.

Every stage is swappable behind a small protocol, and every stage can be turned
off, because the only way to know whether a component earns its latency is to
run the evaluation with and without it.

    from ragkit import RagPipeline, PipelineConfig, load_corpus

    docs = load_corpus("data/corpus")
    pipeline = RagPipeline.build(docs, PipelineConfig())
    result = pipeline.retrieve("how do I roll back a deploy")
"""

from .answer import (
    AbstainReason,
    Answer,
    ClaudeAnswerer,
    ExtractiveAnswerer,
    build_context,
    verify_citations,
)
from .cache import CacheStats, SemanticCache, measure_false_hits
from .chunking import ChunkConfig, chunk_corpus, chunk_document
from .corpus import corpus_stats, load_corpus
from .dense import DenseIndex
from .embeddings import Embedder, HashingEmbedder, SentenceTransformerEmbedder
from .evaluation import (
    AblationStep,
    EvalReport,
    default_ablation,
    evaluate,
    format_category_table,
    format_table,
    load_queries,
    paired_bootstrap,
    run_ablation,
)
from .filters import MetadataFilter
from .hybrid import HybridRetriever, normalized_score_fusion, reciprocal_rank_fusion
from .lexical import BM25Index
from .parallel import ToolCall, ToolResult, execute_tool_calls, extract_tool_calls
from .pipeline import PipelineConfig, RagPipeline
from .query_rewrite import ClaudeQueryRewriter, NoOpRewriter, RuleBasedRewriter
from .rerank import (
    BiEncoderReranker,
    CrossEncoderReranker,
    LexicalCrossEncoder,
    NoOpReranker,
)
from .tools import ToolRegistry, ToolSpec, build_registry, lint_tool
from .types import Chunk, Document, EvalQuery, RetrievalResult, ScoredChunk

__all__ = [
    "AbstainReason",
    "AblationStep",
    "Answer",
    "BM25Index",
    "BiEncoderReranker",
    "CacheStats",
    "Chunk",
    "ChunkConfig",
    "ClaudeAnswerer",
    "ClaudeQueryRewriter",
    "CrossEncoderReranker",
    "DenseIndex",
    "Document",
    "Embedder",
    "EvalQuery",
    "EvalReport",
    "ExtractiveAnswerer",
    "HashingEmbedder",
    "HybridRetriever",
    "LexicalCrossEncoder",
    "MetadataFilter",
    "NoOpReranker",
    "NoOpRewriter",
    "PipelineConfig",
    "RagPipeline",
    "RetrievalResult",
    "RuleBasedRewriter",
    "ScoredChunk",
    "SemanticCache",
    "SentenceTransformerEmbedder",
    "ToolCall",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "build_context",
    "build_registry",
    "chunk_corpus",
    "chunk_document",
    "corpus_stats",
    "default_ablation",
    "evaluate",
    "execute_tool_calls",
    "extract_tool_calls",
    "format_category_table",
    "format_table",
    "lint_tool",
    "load_corpus",
    "load_queries",
    "measure_false_hits",
    "normalized_score_fusion",
    "reciprocal_rank_fusion",
    "paired_bootstrap",
    "run_ablation",
    "verify_citations",
]

__version__ = "1.0.0"
