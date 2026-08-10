"""The six-stage orchestrator.

    decompose -> plan queries -> retrieve -> verify -> aggregate -> gate

Stages 1, 2 and 4 are model calls and go through `run_structured` from the
extraction pipeline, so they inherit the salvage layer, the validator-quoting
repair turn and the refusal handling without reimplementing any of it. Stage 3
is retrieval. Stages 5 and 6 are pure code.

Three defences are wired in here rather than left to the prompt:

*   **Fabricated claim spans die at stage 1.** A claim whose `source_span` is
    not in the input is discarded before it costs a single retrieval — the
    grounding check from the extraction prototype, applied to the decomposer.
*   **Fabricated quotes do not vote.** A stage-4 verdict whose `quoted_evidence`
    is not in the passage is kept for the audit trail with `quote_grounded:
    False` and given zero weight at stage 5.
*   **Injection is counted, not just resisted.** Retrieved passages that address
    the checker are flagged, weighted to zero, and reported — so the number can
    be watched rather than assumed to be zero.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Optional

from extraction.client import Provider
from extraction.grounding import DocumentIndex
from extraction.pipeline import ExtractConfig, run_structured

from . import prompts
from .aggregate import PassageOutcome, gate
from .evidence import Passage, Retriever, looks_adversarial
from .schemas import Claim, ClaimSet, ClaimVerdict, FactCheckReport, PassageVerdict, QueryPlan


@dataclass
class FactCheckConfig:
    queries_per_claim: int = 3
    passages_per_query: int = 3
    max_passages_per_claim: int = 5
    max_attempts: int = 2
    output_mode: str = "strict"
    temperature: Optional[float] = None
    concurrency: int = 4
    # Skip stage 2 and search on the claim text alone. Useful as a baseline:
    # if planned queries do not beat this, stage 2 is not earning its cost.
    skip_planning: bool = False


def _config(cfg: FactCheckConfig, prompt: str) -> ExtractConfig:
    return ExtractConfig(
        prompt=prompt, output_mode=cfg.output_mode, max_attempts=cfg.max_attempts,
        repair_ungrounded=False, temperature=cfg.temperature,
    )


# --------------------------------------------------------------------------- #
# Stage 1
# --------------------------------------------------------------------------- #


def decompose(
    document_text: str, *, provider: Provider, cfg: FactCheckConfig, document_id: str = "doc"
) -> tuple[list[Claim], list[str]]:
    """Split the input into claims, discarding any whose span is fabricated."""
    result = run_structured(
        item_id=f"{document_id}:decompose",
        model_cls=ClaimSet,
        system=prompts.DECOMPOSE_SYSTEM,
        user=prompts.decompose_user(document_text),
        provider=provider,
        config=_config(cfg, "decompose"),
        noun="claim set",
        task="fc_decompose",
    )
    problems: list[str] = []
    if result.status != "ok" or not result.payload:
        return [], [f"stage 1 ({result.status}): could not decompose the input"]

    index = DocumentIndex(document_text)
    claims: list[Claim] = []
    for raw in result.payload.get("claims", []):
        try:
            claim = Claim.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            problems.append(f"stage 1: unparseable claim ({exc})")
            continue
        if not index.has_text(claim.source_span):
            # The decomposer invented a quote. Cheapest possible place to catch it.
            problems.append(f"stage 1: discarded a claim whose span is not in the input: "
                            f"{claim.source_span[:60]!r}")
            continue
        claims.append(claim)
    return claims, problems


# --------------------------------------------------------------------------- #
# Stage 2
# --------------------------------------------------------------------------- #


def plan_queries(claim: Claim, *, provider: Provider, cfg: FactCheckConfig) -> list[str]:
    if cfg.skip_planning:
        return [claim.text]
    result = run_structured(
        item_id=f"plan:{claim.text[:30]}",
        model_cls=QueryPlan,
        system=prompts.PLAN_SYSTEM,
        user=prompts.plan_user(claim.text, claim.entities, claim.time_reference),
        provider=provider,
        config=_config(cfg, "plan"),
        noun="query plan",
        task="fc_plan",
    )
    if result.status != "ok" or not result.payload:
        return [claim.text]  # fall back to the claim itself rather than dropping it
    queries = [q.get("text", "") for q in result.payload.get("queries", []) if q.get("text")]
    return (queries or [claim.text])[: cfg.queries_per_claim]


# --------------------------------------------------------------------------- #
# Stage 3
# --------------------------------------------------------------------------- #


def retrieve(queries: list[str], *, retriever: Retriever, cfg: FactCheckConfig) -> list[Passage]:
    seen: dict[tuple[str, str], Passage] = {}
    for query in queries:
        for passage in retriever.search(query, limit=cfg.passages_per_query):
            key = (passage.source_id, passage.text)
            if key not in seen or passage.score > seen[key].score:
                seen[key] = passage
    ranked = sorted(seen.values(), key=lambda p: -p.score)
    return ranked[: cfg.max_passages_per_claim]


# --------------------------------------------------------------------------- #
# Stage 4
# --------------------------------------------------------------------------- #


def verify(
    claim: Claim, passage: Passage, *, provider: Provider, cfg: FactCheckConfig
) -> Optional[PassageOutcome]:
    result = run_structured(
        item_id=f"verify:{passage.source_id}",
        model_cls=PassageVerdict,
        system=prompts.VERIFY_SYSTEM,
        user=prompts.verify_user(claim.text, passage.text, passage.source_id, passage.published),
        provider=provider,
        config=_config(cfg, "verify"),
        noun="passage verdict",
        task="fc_verify",
    )
    if result.status != "ok" or not result.payload:
        return None

    quote = result.payload.get("quoted_evidence")
    grounded = True
    if quote:
        grounded = _quote_in(quote, passage.text)

    return PassageOutcome(
        source_id=passage.source_id,
        title=passage.title,
        publisher=passage.publisher,
        published=passage.published,
        is_primary=passage.is_primary,
        reliability=passage.reliability,
        relation=result.payload.get("relation", "insufficient"),
        confidence=result.payload.get("confidence", "low"),
        quote=quote,
        quote_grounded=grounded,
        adversarial_source=passage.is_adversarial or looks_adversarial(passage.text),
    )


def _quote_in(quote: str, passage: str) -> bool:
    """Is the quoted sentence actually in the passage?

    Tolerant of whitespace and case — a model that normalises spacing has still
    quoted honestly — but not of paraphrase, which is the failure being caught.
    """
    normalise = lambda s: re.sub(r"\s+", " ", s).strip().casefold()  # noqa: E731
    return normalise(quote) in normalise(passage)


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #


def check_claim(
    claim: Claim, *, provider: Provider, retriever: Retriever, cfg: FactCheckConfig
) -> ClaimVerdict:
    if not claim.checkable:
        return gate(claim, [], passages_examined=0)

    queries = plan_queries(claim, provider=provider, cfg=cfg)
    passages = retrieve(queries, retriever=retriever, cfg=cfg)
    outcomes: list[PassageOutcome] = []
    for passage in passages:
        outcome = verify(claim, passage, provider=provider, cfg=cfg)
        if outcome is not None:
            outcomes.append(outcome)
    return gate(claim, outcomes, passages_examined=len(passages))


def check_document(
    document_text: str,
    *,
    provider_factory,
    retriever: Retriever,
    cfg: FactCheckConfig,
    document_id: str = "doc",
) -> FactCheckReport:
    """Run all six stages over one input document."""
    claims, problems = decompose(
        document_text, provider=provider_factory(), cfg=cfg, document_id=document_id
    )

    def one(claim: Claim) -> ClaimVerdict:
        return check_claim(claim, provider=provider_factory(), retriever=retriever, cfg=cfg)

    with ThreadPoolExecutor(max_workers=max(1, cfg.concurrency)) as pool:
        verdicts = list(pool.map(one, claims))

    # Count on the flag the retriever set for the whole page. Re-deriving it
    # from the quoted sentence is the bug this counter had at first: the quote
    # is one sentence, and the instruction that makes the page dangerous is
    # usually a different one.
    injections = sum(
        1
        for verdict in verdicts
        for ref in verdict.evidence
        if ref.from_adversarial_source
    )
    skipped = [
        f"{v.claim.text} — {v.reason}" for v in verdicts if v.verdict == "not_checkable"
    ]
    return FactCheckReport(
        document_id=document_id,
        verdicts=verdicts,
        skipped=skipped,
        stage_failures=problems,
        injection_attempts_seen=injections,
    )


def report_to_dict(report: FactCheckReport) -> dict[str, Any]:
    return report.model_dump()
