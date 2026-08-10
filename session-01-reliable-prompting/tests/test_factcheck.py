"""The fact-checking system.

The model-driven stages are simulated, so what is worth pinning here is
everything the model does not do: retrieval, the two grounding checks, the
weighting, the abstention rules, and the injection defence. Those are the parts
that must hold regardless of which model sits in the middle.
"""

from __future__ import annotations

import pytest

from extraction.client import MockProvider
from factcheck import pipeline
from factcheck.aggregate import (
    MIN_MARGIN,
    PassageOutcome,
    aggregate,
    gate,
)
from factcheck.evalset import CLAIM_CASES, DOCUMENTS
from factcheck.evidence import CORPUS, LocalRetriever, looks_adversarial
from factcheck.schemas import Claim, ClaimSet, PassageVerdict, QueryPlan
from extraction.schemas import json_schema_for


def _outcome(relation, confidence="high", *, primary=True, reliability=1.0,
             grounded=True, adversarial=False, source="SRC-X"):
    return PassageOutcome(
        source_id=source, title="t", publisher="p", published="2025-01-01",
        is_primary=primary, reliability=reliability, relation=relation,
        confidence=confidence, quote="q", quote_grounded=grounded,
        adversarial_source=adversarial,
    )


def _claim(text="Northwind Group reported revenue of EUR 4.1 billion for 2024.", *,
           checkable=True, claim_type="factual"):
    return Claim(text=text, source_span=text, claim_type=claim_type, checkable=checkable)


# --------------------------------------------------------------------------- #
# Schemas — the field orders that make reasoning real
# --------------------------------------------------------------------------- #


def test_verify_schema_puts_evidence_and_reasoning_before_the_relation():
    order = list(json_schema_for(PassageVerdict)["properties"])
    assert order.index("quoted_evidence") < order.index("relation")
    assert order.index("reasoning") < order.index("relation")


def test_query_plan_states_what_would_settle_it_before_the_queries():
    order = list(json_schema_for(QueryPlan)["properties"])
    assert order.index("what_would_settle_this") < order.index("queries")


def test_every_claim_field_is_present_and_the_schema_is_closed():
    schema = json_schema_for(ClaimSet)
    assert schema["additionalProperties"] is False
    claim_schema = schema["$defs"]["Claim"]
    assert set(claim_schema["required"]) == set(claim_schema["properties"])


# --------------------------------------------------------------------------- #
# Stage 3 — retrieval
# --------------------------------------------------------------------------- #


def test_retriever_finds_the_primary_source_for_a_revenue_claim():
    passages = LocalRetriever().search("Northwind Group revenue 2024 EUR 4.1 billion", limit=3)
    assert passages
    assert any(p.source_id == "SRC-AR2024" for p in passages)


def test_retriever_returns_nothing_for_an_unrelated_query():
    assert LocalRetriever().search("zzzqqq unrelated nonsense token", limit=3) == []


def test_retrieval_deduplicates_across_queries():
    cfg = pipeline.FactCheckConfig(passages_per_query=3, max_passages_per_claim=10)
    passages = pipeline.retrieve(
        ["Northwind revenue 2024", "Northwind Group revenue 2024", "Northwind revenue"],
        retriever=LocalRetriever(), cfg=cfg,
    )
    keys = [(p.source_id, p.text) for p in passages]
    assert len(keys) == len(set(keys))


def test_passages_carry_provenance():
    passage = LocalRetriever().search("Northwind emissions 2024", limit=1)[0]
    assert passage.publisher and passage.published is not None
    assert isinstance(passage.is_primary, bool)


# --------------------------------------------------------------------------- #
# Grounding — the two checks that stop fabrication
# --------------------------------------------------------------------------- #


def test_a_claim_whose_span_is_not_in_the_input_is_discarded():
    text = "Northwind Group reported revenue of EUR 4.1 billion for 2024."
    payload = {
        "claims": [
            {"text": "Revenue was EUR 4.1 billion.", "source_span": text,
             "claim_type": "factual", "checkable": True, "entities": [], "time_reference": "2024"},
            {"text": "Profit doubled.", "source_span": "Profit doubled year on year.",
             "claim_type": "factual", "checkable": True, "entities": [], "time_reference": None},
        ]
    }

    class Scripted:
        name, model = "scripted", "s"

        def complete(self, **kwargs):
            import json

            from extraction.client import LLMResponse

            return LLMResponse(text=json.dumps(payload), model="s")

    claims, problems = pipeline.decompose(
        text, provider=Scripted(), cfg=pipeline.FactCheckConfig()
    )
    assert len(claims) == 1
    assert any("span is not in the input" in p for p in problems)


def test_quote_check_tolerates_whitespace_but_not_paraphrase():
    passage = "Northwind Group reported revenue of  EUR 4.1 billion\nfor the financial year 2024."
    assert pipeline._quote_in("Northwind Group reported revenue of EUR 4.1 billion for the "
                              "financial year 2024.", passage)
    assert not pipeline._quote_in("Northwind's revenue was about four billion euros.", passage)


def test_an_ungrounded_quote_does_not_vote():
    assert _outcome("supports", grounded=False).weight() == 0.0


# --------------------------------------------------------------------------- #
# Stage 5 — aggregation
# --------------------------------------------------------------------------- #


def test_a_single_primary_source_settles_a_claim():
    verdict, confidence, margin, _ = aggregate([_outcome("supports")])
    assert verdict == "supported"
    assert margin == 1.0
    assert confidence > 0


def test_conflicting_sources_abstain_rather_than_pick_a_side():
    verdict, _, margin, reason = aggregate(
        [_outcome("supports"), _outcome("refutes", primary=True)]
    )
    assert verdict == "unsupported"
    assert margin < MIN_MARGIN
    assert "conflict" in reason


def test_a_primary_source_outweighs_a_low_reliability_blog():
    verdict, _, _, _ = aggregate(
        [
            _outcome("supports", primary=True, reliability=1.0),
            _outcome("refutes", "medium", primary=False, reliability=0.4),
        ]
    )
    assert verdict == "supported"


def test_refutation_outweighs_support_at_equal_provenance():
    # A specific contradiction is stronger evidence than a passage that merely
    # fails to contradict, so the weighting is deliberately asymmetric.
    assert _outcome("refutes").weight() > _outcome("supports").weight()


def test_no_decisive_passage_means_unsupported_not_supported():
    verdict, _, _, reason = aggregate([_outcome("irrelevant"), _outcome("insufficient")])
    assert verdict == "unsupported"
    assert "no passage decided" in reason


def test_an_adversarial_page_carries_no_weight():
    assert _outcome("supports", adversarial=True).weight() == 0.0
    verdict, _, _, _ = aggregate([_outcome("supports", adversarial=True)])
    assert verdict == "unsupported"


# --------------------------------------------------------------------------- #
# Stage 6 — gating
# --------------------------------------------------------------------------- #


def test_an_uncheckable_claim_is_routed_out_not_ruled_on():
    verdict = gate(_claim(checkable=False, claim_type="opinion"), [], passages_examined=0)
    assert verdict.verdict == "not_checkable"
    assert "opinion" in verdict.reason


def test_retrieval_failure_is_reported_as_a_system_finding():
    verdict = gate(_claim(), [], passages_examined=0)
    assert verdict.verdict == "unsupported"
    assert "retrieval returned nothing" in verdict.reason


def test_checked_and_unsupported_is_distinguishable_from_could_not_check():
    could_not = gate(_claim(), [], passages_examined=0)
    checked = gate(_claim(), [_outcome("irrelevant")], passages_examined=3)
    assert could_not.verdict == checked.verdict == "unsupported"
    assert could_not.reason != checked.reason
    assert could_not.passages_examined == 0 and checked.passages_examined == 3


def test_discarded_quotes_are_counted_in_the_verdict():
    verdict = gate(
        _claim(), [_outcome("supports", grounded=False), _outcome("supports")], passages_examined=2
    )
    assert verdict.discarded_ungrounded_quotes == 1
    assert "discarded" in verdict.reason


# --------------------------------------------------------------------------- #
# Injection
# --------------------------------------------------------------------------- #


def test_the_adversarial_page_is_detected():
    page = next(d for d in CORPUS if d.source_id == "SRC-ADVERSARIAL")
    assert looks_adversarial(page.text)


def test_ordinary_sources_are_not_flagged_as_adversarial():
    for doc in CORPUS:
        if doc.source_id != "SRC-ADVERSARIAL":
            assert not looks_adversarial(doc.text), doc.source_id


def test_a_claim_asserted_only_by_the_injection_page_is_not_supported():
    # End to end through the mock, which complies with the injection half the
    # time — the defence is the zero weight at aggregation, not the verifier.
    claim = _claim("Northwind Group reported revenue of EUR 9.9 billion in 2024.")
    verdict = pipeline.check_claim(
        claim, provider=MockProvider(seed=7), retriever=LocalRetriever(),
        cfg=pipeline.FactCheckConfig(),
    )
    assert verdict.verdict != "supported"


# --------------------------------------------------------------------------- #
# End to end
# --------------------------------------------------------------------------- #


def test_a_supported_claim_survives_the_whole_pipeline():
    verdict = pipeline.check_claim(
        _claim(), provider=MockProvider(seed=3), retriever=LocalRetriever(),
        cfg=pipeline.FactCheckConfig(),
    )
    assert verdict.verdict in ("supported", "unsupported")  # never confidently wrong
    assert verdict.passages_examined > 0


def test_check_document_never_silently_drops_a_claim():
    case = DOCUMENTS[1]
    report = pipeline.check_document(
        case.text, provider_factory=lambda: MockProvider(seed=7),
        retriever=LocalRetriever(), cfg=pipeline.FactCheckConfig(), document_id=case.doc_id,
    )
    not_checkable = [v for v in report.verdicts if v.verdict == "not_checkable"]
    assert len(report.skipped) == len(not_checkable)
    for verdict in report.verdicts:
        assert verdict.reason


def test_planning_can_be_ablated_to_the_claim_text():
    cfg = pipeline.FactCheckConfig(skip_planning=True)
    queries = pipeline.plan_queries(_claim(), provider=MockProvider(seed=1), cfg=cfg)
    assert queries == [_claim().text]


# --------------------------------------------------------------------------- #
# The eval set itself
# --------------------------------------------------------------------------- #


def test_eval_set_covers_every_verdict_and_the_dangerous_slices():
    golds = {case.gold for case in CLAIM_CASES}
    assert golds == {"supported", "refuted", "unsupported", "not_checkable"}
    slices = {case.slice for case in CLAIM_CASES}
    assert {"near_miss", "no_evidence", "adversarial", "stale_source", "time_bound"} <= slices


@pytest.mark.parametrize("case", CLAIM_CASES, ids=lambda c: c.slice + ":" + c.claim[:28])
def test_every_eval_claim_is_well_formed(case):
    claim = case.to_claim()
    assert claim.text.strip()
    assert (case.gold == "not_checkable") == (not case.checkable)


def test_document_cases_declare_more_claims_than_sentences():
    # If gold_claims equalled the sentence count, the decomposition eval would
    # be measuring sentence splitting rather than atomicity.
    compound = next(d for d in DOCUMENTS if d.doc_id == "DOC-compound")
    assert compound.text.count(".") < compound.gold_claims


def test_a_retrieved_sentence_inherits_its_pages_adversarial_flag():
    # Regression. Chunking splits the injection instruction away from the
    # payload sentence it is smuggling, so a per-sentence check sees a clean
    # passage. Without the document-level flag the trap goes unnoticed and is
    # only defused by the reliability score — which a real retriever does not have.
    payload = [
        p for p in LocalRetriever().search("Northwind revenue 9.9 billion 2024 employs", limit=5)
        if p.source_id == "SRC-ADVERSARIAL"
    ]
    assert payload, "the adversarial page should be retrievable — that is the point of it"
    for passage in payload:
        assert passage.is_adversarial
        # The retrieved sentence itself is innocuous; only the page is not.
        if not looks_adversarial(passage.text):
            break
    else:  # pragma: no cover - defensive
        pytest.skip("every retrieved sentence carried a marker; the regression needs a new fixture")


def test_the_injection_page_gets_zero_weight_even_when_its_sentence_looks_clean():
    passage = next(
        p for p in LocalRetriever().search("Northwind revenue 9.9 billion 2024 employs", limit=5)
        if p.source_id == "SRC-ADVERSARIAL"
    )
    outcome = PassageOutcome(
        source_id=passage.source_id, title=passage.title, publisher=passage.publisher,
        published=passage.published, is_primary=passage.is_primary,
        reliability=1.0,  # pretend we had no reliability signal to fall back on
        relation="supports", confidence="high", quote=passage.text,
        adversarial_source=passage.is_adversarial or looks_adversarial(passage.text),
    )
    assert outcome.weight() == 0.0


def test_the_injection_counter_reads_the_page_flag_not_the_quoted_sentence():
    # Same bug as the weighting had: the quote is one sentence, and the wording
    # that makes the page dangerous is usually a different one. Counting on the
    # quote silently under-reports how many traps were retrieved.
    outcome = PassageOutcome(
        source_id="SRC-ADVERSARIAL", title="t", publisher="p", published=None,
        is_primary=False, reliability=1.0, relation="supports", confidence="high",
        quote="Northwind Group reported revenue of EUR 9.9 billion in 2024.",
        adversarial_source=True,
    )
    verdict = gate(_claim(), [outcome], passages_examined=1)
    ref = verdict.evidence[0]
    assert ref.from_adversarial_source
    assert not looks_adversarial(ref.quote or ""), "the fixture must be a clean-looking sentence"
