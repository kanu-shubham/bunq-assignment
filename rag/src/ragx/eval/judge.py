"""LLM-as-judge.

Used for the two things a deterministic check cannot decide: whether a sentence
is *entailed* by its cited passage (rather than merely lexically similar), and
whether an answer matches a reference answer written by a human.

Rules this implementation follows, all of which exist because the naive version
produces numbers that look fine and mean nothing:

* **Judge per claim, not per answer.** "Rate this answer 1-5 for faithfulness"
  yields 4s for everything. Splitting the answer into claims and asking a
  supported/unsupported/contradicted question per claim gives a metric that
  moves when quality moves.
* **A fixed rubric with a defined middle.** Every label the judge may return is
  spelled out, including what makes something `unsupported` rather than
  `contradicted`.
* **The judge sees only the cited passages, not the retrieved set.** Otherwise
  it credits a claim to a passage the answer never cited, and mis-citation
  becomes invisible.
* **Structured output.** Free-text judgements have to be regex-parsed, and the
  parse failures are correlated with the hard cases.
* **Validate the judge.** `agreement_with_humans` exists because a judge that
  has never been checked against a human-labelled subset is an unvalidated
  measuring instrument. Target: >80% agreement on a 50-case sample before any
  threshold is allowed to gate CI.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..llm import LLM, cached_system_block, parse_json_response
from ..obs.trace import log
from ..types import Answer

FAITHFULNESS_SYSTEM = """You check whether each claim in an answer is supported by the passages it cites.

For each claim, return exactly one verdict:
- "supported": the cited passages state this claim, or state something it follows from directly.
- "unsupported": the cited passages do not contain this information. Use this when the claim is plausible but simply absent — this includes correct-sounding detail the passages never mention.
- "contradicted": the cited passages state something incompatible with the claim.

Judge only against the cited passages shown. Do not use outside knowledge, and do not reward a claim for being true in general.
Ignore style, tone, completeness, and whether the answer is helpful. You are checking attribution only."""

FAITHFULNESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "verdict": {
                        "type": "string",
                        "enum": ["supported", "unsupported", "contradicted"],
                    },
                    "reason": {"type": "string"},
                },
                "required": ["claim", "verdict", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}

CORRECTNESS_SYSTEM = """You compare a candidate answer against a reference answer written by a subject-matter expert.

Return one verdict:
- "correct": the candidate conveys the same substantive facts as the reference. Different wording, ordering, or extra correct detail is fine.
- "partial": the candidate gets some required facts right and omits or garbles others.
- "incorrect": the candidate contradicts the reference or misses its substance entirely.

A candidate that correctly declines to answer when the reference also declines is "correct".
Do not reward length, confidence, or formatting."""

CORRECTNESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["correct", "partial", "incorrect"]},
        "reason": {"type": "string"},
        "missing_facts": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class FaithfulnessResult:
    supported: int
    unsupported: int
    contradicted: int
    details: tuple[dict[str, str], ...] = ()

    @property
    def total(self) -> int:
        return self.supported + self.unsupported + self.contradicted

    @property
    def score(self) -> float:
        """Fraction of claims supported. Contradictions count against, hard."""
        if self.total == 0:
            return 1.0
        return self.supported / self.total

    @property
    def has_contradiction(self) -> bool:
        return self.contradicted > 0


@dataclass(frozen=True, slots=True)
class CorrectnessResult:
    verdict: str
    reason: str = ""
    missing_facts: tuple[str, ...] = ()

    @property
    def score(self) -> float:
        return {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}.get(self.verdict, 0.0)


class Judge:
    def __init__(self, llm: LLM, model: str, effort: str = "medium") -> None:
        self._llm = llm
        self._model = model
        self._effort = effort

    def faithfulness(self, answer: Answer) -> FaithfulnessResult:
        if not answer.text.strip() or answer.abstained:
            return FaithfulnessResult(0, 0, 0)

        cited_ids = {c.chunk_id for c in answer.citations}
        cited_blocks = [
            f"[{i}] {c.chunk.display_path}\n{c.chunk.text}"
            for i, c in enumerate(answer.contexts, start=1)
            if c.chunk.chunk_id in cited_ids
        ]
        if not cited_blocks:
            # An answer with no citations at all is unsupported by definition;
            # do not spend a judge call to learn that.
            return FaithfulnessResult(supported=0, unsupported=1, contradicted=0)

        prompt = (
            "Cited passages:\n\n"
            + "\n\n".join(cited_blocks)
            + "\n\n---\n\nAnswer to check:\n"
            + answer.text
            + "\n\nSplit the answer into its factual claims and judge each one."
        )
        response = self._llm.complete(
            model=self._model,
            system=[cached_system_block(FAITHFULNESS_SYSTEM)],
            user=prompt,
            max_tokens=2048,
            effort=self._effort,
            json_schema=FAITHFULNESS_SCHEMA,
        )
        if response.refused:
            log("judge_refused", kind="faithfulness")
            return FaithfulnessResult(0, 0, 0)
        payload = parse_json_response(response.text)
        claims = payload.get("claims", [])
        counts = {"supported": 0, "unsupported": 0, "contradicted": 0}
        for claim in claims:
            verdict = claim.get("verdict")
            if verdict in counts:
                counts[verdict] += 1
        return FaithfulnessResult(
            supported=counts["supported"],
            unsupported=counts["unsupported"],
            contradicted=counts["contradicted"],
            details=tuple(claims),
        )

    def correctness(self, question: str, candidate: str, reference: str) -> CorrectnessResult:
        prompt = (
            f"Question: {question}\n\n"
            f"Reference answer:\n{reference}\n\n"
            f"Candidate answer:\n{candidate}"
        )
        response = self._llm.complete(
            model=self._model,
            system=[cached_system_block(CORRECTNESS_SYSTEM)],
            user=prompt,
            max_tokens=1024,
            effort=self._effort,
            json_schema=CORRECTNESS_SCHEMA,
        )
        if response.refused:
            log("judge_refused", kind="correctness")
            return CorrectnessResult(verdict="incorrect", reason="judge declined")
        payload = parse_json_response(response.text)
        return CorrectnessResult(
            verdict=payload.get("verdict", "incorrect"),
            reason=payload.get("reason", ""),
            missing_facts=tuple(payload.get("missing_facts", ())),
        )


def agreement_with_humans(
    judge_labels: Sequence[str], human_labels: Sequence[str]
) -> float:
    """Run this before trusting any judge-derived threshold in CI.

    Below ~0.8 the judge is measuring its own preferences, and tightening a
    threshold against it will reject good changes and accept bad ones.
    """
    if not judge_labels or len(judge_labels) != len(human_labels):
        raise ValueError("label lists must be non-empty and the same length")
    return sum(1 for a, b in zip(judge_labels, human_labels, strict=True) if a == b) / len(judge_labels)
