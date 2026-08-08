"""Typed configuration.

Every knob that changes retrieval or answer quality lives here, so an eval run
can print the exact config it scored and a regression can be bisected to a
config change rather than a code change.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

# Answer synthesis and LLM reranking both run on Claude Opus 5. Reranking is the
# obvious downgrade candidate if cost becomes the binding constraint — see
# DESIGN.md § Cost.
ANSWER_MODEL = "claude-opus-5"
RERANK_MODEL = "claude-opus-5"
JUDGE_MODEL = "claude-opus-5"

# Server-side refusal fallback: on a policy decline the API re-runs the request
# on Anthropic's recommended fallback model instead of handing us a refusal.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    target_tokens: int = 512
    overlap_tokens: int = 64
    min_tokens: int = 48  # below this, a chunk gets merged into its neighbour
    max_tokens: int = 900  # hard ceiling; atomic blocks may exceed target
    keep_tables_atomic: bool = True
    keep_code_atomic: bool = True
    contextualize: bool = True  # prepend title + heading path to embed_text


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    dense_k: int = 50
    lexical_k: int = 50
    fusion: Literal["rrf", "weighted"] = "rrf"
    rrf_k: int = 60
    dense_weight: float = 0.5  # only used by `weighted`
    lexical_weight: float = 0.5
    candidates_k: int = 40  # what goes into the reranker
    final_k: int = 8  # what goes into the prompt
    mmr_lambda: float = 0.0  # 0 disables MMR; 0.7 is a sane starting point
    per_doc_cap: int = 3  # stop one verbose doc from owning the whole context


@dataclass(frozen=True, slots=True)
class RerankConfig:
    enabled: bool = True
    batch_size: int = 20  # candidates scored per LLM call
    timeout_s: float = 12.0
    max_snippet_chars: int = 900
    # On any reranker failure we fall back to fusion order rather than failing
    # the request. A degraded answer beats a 500.
    fail_open: bool = True


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    model: str = ANSWER_MODEL
    max_tokens: int = 2048
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    thinking: bool = True
    stream: bool = True
    timeout_s: float = 60.0
    use_prompt_cache: bool = True
    use_server_fallback: bool = True
    # Below this fraction of sentences carrying a valid citation we downgrade
    # the answer to "low confidence" and surface it to the caller.
    min_grounding_score: float = 0.6


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    max_question_chars: int = 2000
    request_timeout_s: float = 90.0
    default_tenant: str = "acme"


@dataclass(frozen=True, slots=True)
class Config:
    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    rerank: RerankConfig = field(default_factory=RerankConfig)
    generation: GenerationConfig = field(default_factory=GenerationConfig)
    service: ServiceConfig = field(default_factory=ServiceConfig)
    embedding_dim: int = 384

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_env() -> Config:
        """Only the knobs an operator realistically flips at 3am."""
        cfg = Config()
        final_k = int(os.getenv("RAGX_FINAL_K", cfg.retrieval.final_k))
        rerank_enabled = os.getenv("RAGX_RERANK", "1") != "0"
        stream = os.getenv("RAGX_STREAM", "1") != "0"
        return Config(
            chunking=cfg.chunking,
            retrieval=RetrievalConfig(**{**asdict(cfg.retrieval), "final_k": final_k}),
            rerank=RerankConfig(**{**asdict(cfg.rerank), "enabled": rerank_enabled}),
            generation=GenerationConfig(**{**asdict(cfg.generation), "stream": stream}),
            service=cfg.service,
            embedding_dim=cfg.embedding_dim,
        )


DEFAULT_CONFIG = Config()
