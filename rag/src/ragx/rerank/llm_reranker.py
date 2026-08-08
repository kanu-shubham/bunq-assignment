"""LLM reranking.

Retrieval optimises for recall (get the right chunk into the top 40); reranking
optimises for precision (get it into the top 5, at the top). They are different
objectives and that is why the two-stage shape exists at all: the reranker is
too expensive to run over the corpus, and the retriever is too blunt to order
the head of the list.

Implementation choices worth defending:

* **Listwise, in batches of ~20.** Pointwise scoring ("rate this 0-10") gives
  poorly separated scores and costs one call per candidate. Fully listwise over
  40 candidates blows the snippet budget and degrades in the middle of a long
  list. Batching keeps each call comparable and parallelisable.
* **Scores, not just an ordering.** A returned ordering cannot be merged across
  batches; scores can. Batch position bias is real, so within-batch scores are
  z-normalised before merging, and the fusion score breaks ties.
* **Fail open.** A reranker timeout returns fusion order with a metric
  incremented — a slightly worse answer, not a 500. This is the single most
  important operational property of the stage.
* **Cache.** Keyed on (query, candidate id set). Repeated and near-identical
  questions are the norm in an internal assistant.
"""

from __future__ import annotations

import hashlib
import statistics
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..config import RerankConfig
from ..llm import LLM, cached_system_block, parse_json_response
from ..obs import metrics
from ..obs.trace import log
from ..tokens import truncate_to_tokens
from ..types import ScoredChunk

RERANK_SYSTEM = """You score how well each candidate passage answers a user's question.

Scoring scale (use the whole range):
10 - directly and completely answers the question
7-9 - contains the specific fact needed, possibly with extra material
4-6 - about the right topic, but does not contain the answer
1-3 - same general domain, not useful for this question
0 - unrelated

Rules:
- Judge only whether the passage helps answer THIS question. Ignore writing quality, length, and recency.
- A passage that contradicts the question's premise is still relevant; score it on the information it carries.
- Score every candidate you are given, exactly once, using the id you were given.
- Do not invent ids."""

RERANK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "score": {"type": "number"},
                },
                "required": ["id", "score"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["scores"],
    "additionalProperties": False,
}


class LLMReranker:
    def __init__(
        self,
        llm: LLM,
        model: str,
        config: RerankConfig,
        cache: dict[str, dict[str, float]] | None = None,
        max_workers: int = 4,
    ) -> None:
        self._llm = llm
        self._model = model
        self._config = config
        self._cache = cache if cache is not None else {}
        self._max_workers = max_workers

    def rerank(
        self, query: str, candidates: Sequence[ScoredChunk], k: int
    ) -> list[ScoredChunk]:
        if not candidates:
            return []
        cache_key = self._cache_key(query, candidates)
        cached = self._cache.get(cache_key)
        if cached is not None:
            metrics.incr("rerank_cache_hits_total")
            return self._apply(candidates, cached, k)

        batches = [
            list(candidates[i : i + self._config.batch_size])
            for i in range(0, len(candidates), self._config.batch_size)
        ]
        try:
            if len(batches) == 1:
                results = [self._score_batch(query, batches[0])]
            else:
                with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
                    results = list(pool.map(lambda b: self._score_batch(query, b), batches))
        except Exception as exc:  # noqa: BLE001 — degrade, do not fail the request
            metrics.incr("rerank_failures_total")
            log("rerank_failed", error=f"{type(exc).__name__}: {exc}")
            if not self._config.fail_open:
                raise
            return list(candidates)[:k]

        merged: dict[str, float] = {}
        for batch_scores in results:
            merged.update(_normalize(batch_scores))
        self._cache[cache_key] = merged
        metrics.incr("rerank_calls_total", len(batches))
        return self._apply(candidates, merged, k)

    def _score_batch(
        self, query: str, batch: Sequence[ScoredChunk]
    ) -> dict[str, float]:
        prompt = self._render(query, batch)
        response = self._llm.complete(
            model=self._model,
            system=[cached_system_block(RERANK_SYSTEM)],
            user=prompt,
            max_tokens=1024,
            effort="low",  # ranking is a judgement call, not a reasoning marathon
            thinking=True,
            json_schema=RERANK_SCHEMA,
            timeout_s=self._config.timeout_s,
        )
        if response.refused:
            raise RuntimeError("reranker request was declined")
        payload = parse_json_response(response.text)
        by_index = {int(row["id"]): float(row["score"]) for row in payload["scores"]}
        scores: dict[str, float] = {}
        for idx, cand in enumerate(batch, start=1):
            if idx in by_index:
                scores[cand.chunk.chunk_id] = by_index[idx]
        return scores

    def _render(self, query: str, batch: Sequence[ScoredChunk]) -> str:
        lines = [f"Question: {query}", "", "Candidates:"]
        for idx, cand in enumerate(batch, start=1):
            snippet = truncate_to_tokens(
                cand.chunk.text.replace("\n", " "), self._config.max_snippet_chars // 4
            )
            lines.append(f"[{idx}] ({cand.chunk.display_path}) {snippet}")
        lines.append("")
        lines.append("Return a score for every candidate id above.")
        return "\n".join(lines)

    def _apply(
        self, candidates: Sequence[ScoredChunk], scores: dict[str, float], k: int
    ) -> list[ScoredChunk]:
        rescored = [
            ScoredChunk(
                chunk=c.chunk,
                # Missing scores sink to the bottom but keep fusion order among
                # themselves, so a partially-failed batch degrades gracefully.
                score=scores.get(c.chunk.chunk_id, -1.0),
                channel="rerank",
                component_scores={**c.component_scores, "fused": c.score},
                component_ranks=c.component_ranks,
            )
            for c in candidates
        ]
        rescored.sort(key=lambda s: (s.score, s.component_scores.get("fused", 0.0)), reverse=True)
        return rescored[:k]

    @staticmethod
    def _cache_key(query: str, candidates: Sequence[ScoredChunk]) -> str:
        ids = ",".join(sorted(c.chunk.chunk_id for c in candidates))
        return hashlib.sha256(f"{query}\x00{ids}".encode()).hexdigest()


def _normalize(scores: dict[str, float]) -> dict[str, float]:
    """Z-normalise within a batch so scores from different calls are comparable.

    Without this, a batch where the model happened to be generous outranks a
    batch containing the actually-correct chunk.
    """
    if len(scores) < 2:
        return dict(scores)
    values = list(scores.values())
    mean = statistics.fmean(values)
    stdev = statistics.pstdev(values)
    if stdev == 0:
        return {k: 0.0 for k in scores}
    return {k: (v - mean) / stdev for k, v in scores.items()}
