"""Schemas for the fact-checking pipeline.

One schema per stage, and the stage boundaries are where they are so that each
one is separately measurable. A single mega-schema that decomposed, retrieved
and ruled in one turn would be cheaper and completely undiagnosable — you could
not tell a decomposition failure from a retrieval failure from a reasoning
failure, so you could fix neither.

Two field orders in here are load-bearing, for the reason Prototype 2
established: structured outputs are generated in schema declaration order, so
anything that should inform a verdict must be declared before it. `QueryPlan`
puts `what_would_settle_this` before the queries; `PassageVerdict` puts
`quoted_evidence` and `reasoning` before `relation`. Reversed, you get a verdict
followed by a justification of it, which is a different and much less useful
artefact. Both orderings have tests.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from extraction.schemas import StrictModel

ClaimType = Literal["factual", "opinion", "prediction", "definitional", "ambiguous"]
Relation = Literal["supports", "refutes", "irrelevant", "insufficient"]
Confidence = Literal["low", "medium", "high"]
Verdict = Literal["supported", "refuted", "unsupported", "not_checkable"]
QueryIntent = Literal["primary_source", "news", "reference", "contradiction_probe"]


# --------------------------------------------------------------------------- #
# Stage 1 — decompose
# --------------------------------------------------------------------------- #


class Claim(StrictModel):
    text: str = Field(
        description="The claim rewritten to stand alone: no pronouns, no reference to "
        "surrounding sentences. A reader who sees only this string must be able to check it."
    )
    source_span: str = Field(
        description="The verbatim span of the input this claim came from. Copied exactly, "
        "not paraphrased — it is checked against the input."
    )
    claim_type: ClaimType = Field(
        description="factual: an assertion about the world that evidence could settle. "
        "opinion: a value judgement. prediction: about the future. definitional: true by "
        "definition or stipulation. ambiguous: too vague to check as written."
    )
    checkable: bool = Field(
        description="True only for claims a document could settle. Opinions and predictions "
        "are not checkable, and saying so is a correct answer rather than a failure."
    )
    entities: list[str] = Field(
        default_factory=list, description="Named entities the claim is about."
    )
    time_reference: Optional[str] = Field(
        None, description="The period the claim is about, when it is time-bound (e.g. '2024', "
        "'Q3 2023'). Null when the claim is not time-bound."
    )


class ClaimSet(StrictModel):
    claims: list[Claim] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Stage 2 — plan queries
# --------------------------------------------------------------------------- #


class Query(StrictModel):
    text: str = Field(description="The search query.")
    intent: QueryIntent = Field(
        description="contradiction_probe searches for evidence the claim is *wrong*. "
        "At least one query per claim should be one."
    )


class QueryPlan(StrictModel):
    # Declared first on purpose: the queries follow the plan, rather than the
    # plan being written to fit queries already chosen.
    what_would_settle_this: str = Field(
        description="One sentence: what kind of source or figure would decide this claim."
    )
    queries: list[Query] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Stage 4 — verify one (claim, passage) pair
# --------------------------------------------------------------------------- #


class PassageVerdict(StrictModel):
    quoted_evidence: Optional[str] = Field(
        None,
        description="The verbatim sentence from the passage that decides this, or null if "
        "the passage does not decide it. Checked against the passage.",
    )
    reasoning: list[str] = Field(
        default_factory=list,
        description="The steps from the quote to the relation, one per entry.",
    )
    relation: Relation = Field(
        description="supports / refutes: the passage settles the claim. irrelevant: the "
        "passage is about something else. insufficient: on topic but does not settle it."
    )
    confidence: Confidence = Field(description="How decisive the passage is.")


# --------------------------------------------------------------------------- #
# Stages 5 and 6 — aggregate and gate (produced in code, not by a model)
# --------------------------------------------------------------------------- #


class EvidenceRef(StrictModel):
    source_id: str
    title: str
    publisher: str
    published: Optional[str] = None
    is_primary: bool = False
    quote: Optional[str] = None
    relation: Relation = "insufficient"
    confidence: Confidence = "low"
    quote_grounded: bool = True
    # Carried from the source document, not re-derived from `quote` — the quote
    # is one sentence and the instruction that makes the page dangerous is
    # usually in a different one.
    from_adversarial_source: bool = False


class ClaimVerdict(StrictModel):
    claim: Claim
    verdict: Verdict
    confidence: float = Field(description="0-1, derived from the weighted vote margin.")
    margin: float = Field(description="Share of the weighted vote behind the winning relation.")
    reason: str = Field(description="Why this verdict, in one line, including why it abstained.")
    evidence: list[EvidenceRef] = Field(default_factory=list)
    passages_examined: int = 0
    discarded_ungrounded_quotes: int = 0


class FactCheckReport(StrictModel):
    document_id: str
    verdicts: list[ClaimVerdict] = Field(default_factory=list)
    skipped: list[str] = Field(
        default_factory=list,
        description="Claims the system declined to check, and why — never silently dropped.",
    )
    stage_failures: list[str] = Field(default_factory=list)
    injection_attempts_seen: int = 0
