"""Stages 5 and 6 — aggregate and gate. No model calls in this file.

The inputs are a handful of labelled relations with confidences and source
weights. That is a scoring function, and a deterministic one is auditable, free,
reproducible, and — the reason that matters most here — cannot be talked into
anything by a retrieved page. Every prompt-injection path in this system ends at
stage 4; if aggregation were also a model call, a single poisoned passage would
get a second chance to decide the verdict.

The weighting is deliberately simple and deliberately explicit. Simple, because
a weighting nobody can explain is a weighting nobody can debug; explicit,
because these constants are the system's editorial policy and belong in version
control where they can be argued about and tuned against the labelled set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .schemas import ClaimVerdict, Confidence, EvidenceRef, Relation, Verdict

# How much a passage's vote counts for.
CONFIDENCE_WEIGHT: dict[Confidence, float] = {"low": 0.4, "medium": 0.7, "high": 1.0}
PRIMARY_SOURCE_BONUS = 1.5

# Refutation outweighs support at equal weight: a specific contradiction from a
# source that names the figure is stronger evidence than a passage that merely
# fails to contradict. Tuned against the labelled set, not guessed at.
REFUTE_WEIGHT = 1.25

# Below this margin the sources disagree enough that the honest answer is
# "unsupported, and here is both sides".
MIN_MARGIN = 0.60
# Below this total weight there simply is not enough evidence to rule.
MIN_WEIGHT = 0.5


@dataclass
class PassageOutcome:
    """One stage-4 result, with the provenance stage 5 needs to weigh it."""

    source_id: str
    title: str
    publisher: str
    published: Optional[str]
    is_primary: bool
    reliability: float
    relation: Relation
    confidence: Confidence
    quote: Optional[str]
    quote_grounded: bool = True
    adversarial_source: bool = False

    def weight(self) -> float:
        if not self.quote_grounded:
            return 0.0  # a verdict whose quote is not in the passage does not vote
        weight = CONFIDENCE_WEIGHT[self.confidence] * max(self.reliability, 0.0)
        if self.is_primary:
            weight *= PRIMARY_SOURCE_BONUS
        if self.relation == "refutes":
            weight *= REFUTE_WEIGHT
        if self.adversarial_source:
            # A page that instructs the checker is not evidence about the world.
            weight *= 0.0
        return weight


def aggregate(outcomes: Iterable[PassageOutcome]) -> tuple[Verdict, float, float, str]:
    """Combine passage outcomes into (verdict, confidence, margin, reason)."""
    decisive = [o for o in outcomes if o.relation in ("supports", "refutes")]
    weights = {"supports": 0.0, "refutes": 0.0}
    for outcome in decisive:
        weights[outcome.relation] += outcome.weight()

    total = weights["supports"] + weights["refutes"]
    if total < MIN_WEIGHT:
        return (
            "unsupported",
            0.0,
            0.0,
            "no passage decided the claim"
            if not decisive
            else "the only passages that decided it were discarded or unreliable",
        )

    winner: Relation = "supports" if weights["supports"] >= weights["refutes"] else "refutes"
    margin = weights[winner] / total

    if margin < MIN_MARGIN:
        return (
            "unsupported",
            round(margin, 3),
            round(margin, 3),
            f"sources conflict (support {weights['supports']:.2f} vs refute "
            f"{weights['refutes']:.2f}); both sides are listed rather than picking one",
        )

    verdict: Verdict = "supported" if winner == "supports" else "refuted"
    # Confidence blends how one-sided the vote was with how much evidence there
    # was at all: a unanimous single low-confidence passage is not a strong result.
    volume = min(total / 2.0, 1.0)
    confidence = round(margin * volume, 3)
    return verdict, confidence, round(margin, 3), f"{len(decisive)} passage(s) decided it"


def gate(claim, outcomes: list[PassageOutcome], *, passages_examined: int) -> ClaimVerdict:
    """Stage 6 — turn an aggregate into an action.

    The output distinguishes "we checked and found no support" from "we could
    not check". Collapsing those two is the most damaging simplification
    available in this design: one is a finding about the claim, the other is a
    finding about the system, and a reader who cannot tell them apart will trust
    the wrong one.
    """
    if not claim.checkable:
        return ClaimVerdict(
            claim=claim,
            verdict="not_checkable",
            confidence=1.0,
            margin=1.0,
            reason=f"claim type is {claim.claim_type}; no evidence could settle it",
            evidence=[],
            passages_examined=0,
        )

    verdict, confidence, margin, reason = aggregate(outcomes)
    discarded = sum(1 for o in outcomes if not o.quote_grounded)
    if discarded:
        reason += f"; {discarded} verdict(s) discarded for an ungrounded quote"
    if passages_examined == 0:
        reason = "retrieval returned nothing for this claim"

    return ClaimVerdict(
        claim=claim,
        verdict=verdict,
        confidence=confidence,
        margin=margin,
        reason=reason,
        evidence=[
            EvidenceRef(
                source_id=o.source_id, title=o.title, publisher=o.publisher,
                published=o.published, is_primary=o.is_primary, quote=o.quote,
                relation=o.relation, confidence=o.confidence, quote_grounded=o.quote_grounded,
                from_adversarial_source=o.adversarial_source,
            )
            for o in outcomes
            if o.relation in ("supports", "refutes")
        ],
        passages_examined=passages_examined,
        discarded_ungrounded_quotes=discarded,
    )
