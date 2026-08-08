"""End-to-end query pipeline: retrieve → rerank → answer → verify."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass

from .config import Config
from .generate.answerer import Answerer
from .index.vector_store import AccessFilter
from .obs import metrics
from .obs.trace import Timings, log, new_trace_id, trace_context
from .rerank.base import Reranker
from .retriever import HybridRetriever
from .types import Answer, Principal


@dataclass(frozen=True, slots=True)
class AskResult:
    answer: Answer
    debug: dict[str, object]


class RagPipeline:
    def __init__(
        self,
        retriever: HybridRetriever,
        reranker: Reranker,
        answerer: Answerer,
        config: Config,
    ) -> None:
        self._retriever = retriever
        self._reranker = reranker
        self._answerer = answerer
        self._config = config

    def ask(self, question: str, principal: Principal, trace_id: str | None = None) -> AskResult:
        started = time.perf_counter()
        with trace_context(trace_id or new_trace_id()) as tid:
            timings = Timings()
            log("ask", question_chars=len(question), tenant=principal.tenant_id)

            contexts, retrieval_debug = self.select_contexts(question, principal, timings)

            if not contexts:
                # No permitted context: abstain without paying for a generation
                # call. This is also the correct answer for "is there a policy
                # about X" when there is not.
                metrics.incr("answers_abstained_total")
                metrics.incr("retrieval_empty_total")
                answer = Answer(
                    text=(
                        "I couldn't find anything in the documentation you have access to "
                        "that answers this."
                    ),
                    citations=(),
                    contexts=(),
                    abstained=True,
                    grounding_score=1.0,
                    trace_id=tid,
                    stage_timings_ms=timings.as_dict(),
                )
            else:
                answer = self._answerer.answer(question, contexts, timings, trace_id=tid)

            total_ms = (time.perf_counter() - started) * 1000
            metrics.observe("ask_latency_ms", total_ms)
            log(
                "ask_complete",
                ms=round(total_ms, 1),
                abstained=answer.abstained,
                citations=len(answer.citations),
                grounding=round(answer.grounding_score, 3),
            )
            return AskResult(
                answer=answer,
                debug={
                    **retrieval_debug,
                    "timings_ms": timings.as_dict(),
                    "total_ms": round(total_ms, 1),
                },
            )

    def stream(
        self, question: str, principal: Principal, trace_id: str | None = None
    ) -> Iterator[tuple[str, object]]:
        with trace_context(trace_id or new_trace_id()) as tid:
            timings = Timings()
            contexts, retrieval_debug = self.select_contexts(question, principal, timings)
            yield "context", {
                "contexts": [
                    {
                        "marker": i,
                        "chunk_id": c.chunk.chunk_id,
                        "title": c.chunk.title,
                        "path": c.chunk.display_path,
                        "uri": c.chunk.uri,
                        "score": round(c.score, 4),
                    }
                    for i, c in enumerate(contexts, start=1)
                ],
                "debug": retrieval_debug,
            }
            if not contexts:
                yield "done", Answer(
                    text="I couldn't find anything in the documentation you have access to "
                    "that answers this.",
                    citations=(),
                    contexts=(),
                    abstained=True,
                    trace_id=tid,
                    stage_timings_ms=timings.as_dict(),
                )
                return
            yield from self._answerer.stream_answer(question, contexts, trace_id=tid)

    def select_contexts(self, question: str, principal: Principal, timings: Timings):
        access = AccessFilter.for_principal(principal)
        result = self._retriever.retrieve(question, access, timings)
        candidates = result.candidates
        final_k = self._config.retrieval.final_k

        if self._config.rerank.enabled and candidates:
            with timings.stage("rerank"):
                contexts = self._reranker.rerank(question, candidates, final_k)
        else:
            contexts = list(candidates)[:final_k]

        debug = {
            "retrieval": {
                "dense_hits": result.debug.dense_hits,
                "lexical_hits": result.debug.lexical_hits,
                "fused_hits": result.debug.fused_hits,
                "after_cap": result.debug.after_cap,
                "fusion": result.debug.fusion,
                "reranked": self._config.rerank.enabled,
            }
        }
        return list(contexts), debug
