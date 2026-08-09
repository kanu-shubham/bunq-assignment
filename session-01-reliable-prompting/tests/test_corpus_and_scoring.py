from __future__ import annotations

import random

import pytest

from extraction.corpus import generate_corpus, noisy
from extraction.schemas import Invoice, Resume, json_schema_for, model_for
from extraction.scoring import agreement, score_document


# --------------------------------------------------------------------------- #
# Corpus
# --------------------------------------------------------------------------- #


def test_corpus_is_reproducible():
    a = generate_corpus(seed=7)
    b = generate_corpus(seed=7)
    assert [d.doc_id for d in a] == [d.doc_id for d in b]
    assert [d.text for d in a] == [d.text for d in b]


def test_corpus_shape():
    docs = generate_corpus(seed=7, size=50)
    assert len(docs) == 50
    assert {d.kind for d in docs} == {"invoice", "resume"}
    assert {d.medium for d in docs} == {"pdf", "email", "text"}
    hard = [d for d in docs if any(t.startswith("hard:") for t in d.tags)]
    assert len(hard) >= 15, "the corpus needs enough planted failure modes to be interesting"


def test_every_document_has_ground_truth_matching_its_schema():
    for doc in generate_corpus(seed=7):
        model = model_for(doc.kind)
        model.model_validate(doc.truth)  # raises if the corpus and schema disagree


def test_noise_never_touches_a_protected_value():
    rng = random.Random(0)
    text = "Invoice No. INV-2025-4410 issued to Contoso Retail Group for services rendered"
    protected = ["INV-2025-4410", "Contoso Retail Group"]
    for _ in range(50):
        out = noisy(text, protected, rng, rate=0.9)
        for value in protected:
            assert value in out


def test_injection_documents_carry_a_canary():
    docs = [d for d in generate_corpus(seed=7) if "hard:injection" in d.tags]
    assert docs
    for doc in docs:
        assert doc.injection_canary
        assert doc.injection_canary in doc.text


def test_refusal_bait_is_marked():
    docs = [d for d in generate_corpus(seed=7) if d.refusal_ok]
    assert docs, "at least one document should make a refusal defensible"


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("model", [Invoice, Resume])
def test_json_schema_is_shaped_for_structured_outputs(model):
    schema = json_schema_for(model)

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for forbidden in ("minLength", "maximum", "pattern", "default"):
                assert forbidden not in node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)


def test_every_schema_field_tolerates_an_absent_value():
    # A schema that forces values is a hallucination generator.
    assert Invoice.model_validate({}).total_amount is None
    assert Resume.model_validate({}).full_name is None


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


def _score(truth, payload, **kw):
    return score_document(
        doc_id="d", kind="invoice", tags=[], truth=truth, payload=payload, status="ok", **kw
    )


def test_absent_truth_plus_invented_value_is_a_hallucination():
    score = _score({"purchase_order": None}, {"purchase_order": "PO123456"})
    assert score.outcomes["hallucination"] == 1


def test_absent_truth_plus_null_is_a_win_not_a_blank():
    score = _score({"purchase_order": None}, {"purchase_order": None})
    assert score.outcomes["correct_null"] == 1
    assert score.accuracy == 1.0


def test_present_truth_plus_null_is_an_omission():
    score = _score({"total_amount": 120.0}, {"total_amount": None})
    assert score.outcomes["omission"] == 1


def test_equivalent_formats_count_as_correct():
    score = _score(
        {"issue_date": "2025-03-14", "total_amount": 1234.56},
        {"issue_date": "14/03/2025", "total_amount": "1.234,56"},
    )
    assert score.outcomes["correct"] == 2


def test_skills_are_compared_as_a_set_with_tolerance():
    truth = {"skills": ["Python", "Go", "Kafka", "PostgreSQL"]}
    close = {"skills": ["Go", "python", "Kafka", "PostgreSQL", "SQL"]}
    assert score_document(
        doc_id="d", kind="resume", tags=[], truth=truth, payload=close, status="ok"
    ).outcomes["correct"] == 1


def test_missing_list_entries_are_counted_per_field():
    truth = {"line_items": [{"description": "a", "amount": 1.0}, {"description": "b", "amount": 2.0}]}
    partial = {"line_items": [{"description": "a", "amount": 1.0}]}
    score = _score(truth, partial)
    assert score.outcomes["omission"] == 2  # the whole second line item
    assert score.outcomes["correct"] == 2


def test_agreement_is_total_when_every_repetition_matches():
    payload = {"invoice_number": "INV-1", "total_amount": 10.0}
    stats = agreement([payload, dict(payload), dict(payload)])
    assert stats["field_agreement"] == 1.0
    assert stats["distinct_outputs"] == 1
    assert stats["unstable_fields"] == []


def test_agreement_names_the_field_that_moved():
    stats = agreement(
        [
            {"invoice_number": "INV-1", "total_amount": 10.0},
            {"invoice_number": "INV-1", "total_amount": 100.0},
        ]
    )
    assert stats["unstable_fields"] == ["total_amount"]
    assert stats["field_agreement"] == 0.5


def test_formatting_only_variation_is_semantically_stable_but_not_identical():
    stats = agreement([{"vendor_name": "Acme Ltd"}, {"vendor_name": "ACME LTD"}])
    assert stats["unstable_fields"] == []
    assert stats["distinct_outputs"] == 2
