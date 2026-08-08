"""Answer synthesis with verified citations, abstention, and streaming."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from ..config import GenerationConfig
from ..llm import LLM, cached_system_block
from ..obs import metrics
from ..obs.trace import Timings, log
from ..types import Answer, ScoredChunk, Usage
from .citations import GroundingReport, verify
from .prompt import ABSTAIN_TOKEN, ANSWER_SYSTEM, STRICTER_RETRY_SUFFIX, build_user_prompt


@dataclass(frozen=True, slots=True)
class Draft:
    text: str
    usage: Usage
    refused: bool = False


class Answerer:
    def __init__(self, llm: LLM, config: GenerationConfig) -> None:
        self._llm = llm
        self._config = config

    # ---- non-streaming -------------------------------------------------

    def answer(
        self,
        question: str,
        contexts: Sequence[ScoredChunk],
        timings: Timings | None = None,
        trace_id: str = "",
    ) -> Answer:
        timings = timings or Timings()
        cfg = self._config
        prompt = build_user_prompt(question, contexts)

        with timings.stage("generate.answer"):
            draft = self._complete(prompt)

        if draft.refused:
            metrics.incr("answers_refused_total")
            return Answer(
                text=(
                    "I can't answer that one. If this is a legitimate internal question, "
                    "please rephrase it or ask a human owner of the document."
                ),
                citations=(),
                contexts=tuple(contexts),
                abstained=True,
                grounding_score=0.0,
                usage=draft.usage,
                trace_id=trace_id,
                stage_timings_ms=timings.as_dict(),
                refusal=True,
            )

        report = verify(draft.text, contexts)
        usage = draft.usage

        # One corrective pass, and only one: if the model wrote uncited claims,
        # a stricter re-ask usually fixes it. A retry loop here would double
        # p99 latency for a case that a second attempt rarely improves further.
        if (
            not _is_abstention(draft.text)
            and report.grounding_score < cfg.min_grounding_score
            and contexts
        ):
            metrics.incr("answers_retried_total")
            log("grounding_retry", score=round(report.grounding_score, 3))
            with timings.stage("generate.retry"):
                retry = self._complete(prompt + STRICTER_RETRY_SUFFIX)
            if not retry.refused:
                retry_report = verify(retry.text, contexts)
                if retry_report.grounding_score > report.grounding_score:
                    draft, report = retry, retry_report
            usage = usage + retry.usage

        return self._finalize(draft.text, contexts, report, usage, timings, trace_id)

    # ---- streaming -----------------------------------------------------

    def stream_answer(
        self,
        question: str,
        contexts: Sequence[ScoredChunk],
        trace_id: str = "",
    ) -> Iterator[tuple[str, str | Answer]]:
        """Yields ("token", text) then a final ("done", Answer).

        Verification is post-hoc by necessity — you cannot check citations
        against a half-written sentence. The UI streams tokens optimistically
        and reconciles on the final event, which carries the validated citation
        list and the grounding score. If the answer fails verification the
        final event says so; the client marks the already-rendered text as
        unverified rather than silently keeping it.
        """
        timings = Timings()
        prompt = build_user_prompt(question, contexts)
        parts: list[str] = []
        with timings.stage("generate.stream"):
            for token in self._llm.stream(
                model=self._config.model,
                system=[cached_system_block(ANSWER_SYSTEM)],
                user=prompt,
                max_tokens=self._config.max_tokens,
                effort=self._config.effort,
                thinking=self._config.thinking,
                timeout_s=self._config.timeout_s,
            ):
                parts.append(token)
                yield "token", token

        text = "".join(parts)
        report = verify(text, contexts)
        # Usage for the streamed call is recorded to metrics inside the LLM
        # layer; it is not re-attributed here.
        yield "done", self._finalize(text, contexts, report, Usage(), timings, trace_id)

    # ---- shared --------------------------------------------------------

    def _complete(self, prompt: str) -> Draft:
        response = self._llm.complete(
            model=self._config.model,
            system=[cached_system_block(ANSWER_SYSTEM)],
            user=prompt,
            max_tokens=self._config.max_tokens,
            effort=self._config.effort,
            thinking=self._config.thinking,
            timeout_s=self._config.timeout_s,
        )
        return Draft(text=response.text, usage=response.usage, refused=response.refused)

    def _finalize(
        self,
        text: str,
        contexts: Sequence[ScoredChunk],
        report: GroundingReport,
        usage: Usage,
        timings: Timings,
        trace_id: str,
    ) -> Answer:
        abstained = _is_abstention(text)
        if abstained:
            metrics.incr("answers_abstained_total")
        metrics.observe("grounding_score", report.grounding_score)
        if report.invalid_markers:
            metrics.incr("citation_invalid_markers_total", len(report.invalid_markers))
            log("invalid_citation_markers", markers=list(report.invalid_markers))

        return Answer(
            text=text.strip(),
            citations=report.citations,
            contexts=tuple(contexts),
            abstained=abstained,
            grounding_score=report.grounding_score,
            uncited_sentences=report.uncited_sentences,
            usage=usage,
            trace_id=trace_id,
            stage_timings_ms=timings.as_dict(),
        )


def _is_abstention(text: str) -> bool:
    return text.strip().upper().startswith(ABSTAIN_TOKEN)
