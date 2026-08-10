"""Prototypes 2-5: the label derivations, the vote, and the judge's arithmetic.

The model-facing behaviour is simulated, so what is worth testing here is
everything *around* the model: that the ground-truth labels are derived
correctly, that voting picks the majority and reports the right margin, that the
judge's precision/recall is computed the way the report claims, and that the
few-shot exemplars do not leak the evaluation set.
"""

from __future__ import annotations

import pytest

from extraction import prompts, scoring
from extraction.corpus import generate_corpus
from extraction.schemas import CoTAudit, DirectAudit, JudgeReport, RoutingDecision, json_schema_for
from prototypes import judge as judge_proto
from prototypes import reasoning, routing


# --------------------------------------------------------------------------- #
# Prototype 2 — chain-of-thought
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "truth, expected, bucket",
    [
        ({"subtotal": 100.0, "tax_amount": 21.0, "total_amount": 121.0}, True, "reconciles"),
        ({"subtotal": 100.0, "tax_amount": 21.0, "total_amount": 121.01}, False, "subtle"),
        ({"subtotal": 100.0, "tax_amount": 21.0, "total_amount": 211.0}, False, "obvious"),
        ({"subtotal": 100.0, "tax_amount": 21.0, "total_amount": None}, None, "missing"),
        ({"subtotal": None, "tax_amount": None, "total_amount": None}, None, "missing"),
    ],
)
def test_audit_label_is_derived_from_the_printed_amounts(truth, expected, bucket):
    assert reasoning.truth_verdict(truth) == (expected, bucket)


def test_cot_schema_puts_the_working_before_the_verdict():
    # Structured outputs generate in schema order. A `reconciles` declared before
    # `steps` would produce rationalisation, not reasoning.
    order = list(json_schema_for(CoTAudit)["properties"])
    assert order.index("steps") < order.index("reconciles")
    assert order.index("computed_total") < order.index("reconciles")
    assert order.index("discrepancy") < order.index("reconciles")


def test_direct_schema_has_nowhere_to_put_the_working():
    assert "steps" not in json_schema_for(DirectAudit)["properties"]


def test_corpus_contains_invoices_that_do_not_reconcile():
    docs = generate_corpus(seed=7)
    buckets = {reasoning.truth_verdict(d.truth)[1] for d in docs if d.kind == "invoice"}
    assert {"reconciles", "missing"} <= buckets
    assert buckets & {"subtle", "obvious"}, "no mismatching invoices — the task would be trivial"


# --------------------------------------------------------------------------- #
# Prototype 3 — few-shot routing
# --------------------------------------------------------------------------- #


def test_routing_labels_cover_every_class():
    docs = generate_corpus(seed=7)
    labels = {routing.truth_class(d) for d in docs}
    assert labels == set(routing.CLASSES)


def test_router_prompt_grows_with_k_and_stops_at_the_exemplar_count():
    assert prompts.router_system_prompt(0).count("Class: ") == 0
    assert prompts.router_system_prompt(2).count("Class: ") == 2
    assert prompts.router_system_prompt(4).count("Class: ") == 4
    # Asking for more shots than exist must not crash or silently repeat.
    assert prompts.router_system_prompt(99).count("Class: ") == len(prompts._ROUTER_EXEMPLARS)


def test_few_shot_exemplars_are_not_drawn_from_the_evaluation_corpus():
    # Sampling exemplars from the eval set leaks answers and the k-curve becomes
    # a memorisation curve. Shared *entities* leak too, more subtly: an example
    # naming a vendor the eval set also uses teaches that vendor's label.
    from extraction import corpus as corpus_mod

    corpus_text = "\n".join(d.text for d in generate_corpus(seed=7))
    for text, _ in prompts._ROUTER_EXEMPLARS:
        first_line = text.split("\n")[0].strip()
        assert first_line not in corpus_text, f"exemplar leaked into the corpus: {first_line}"

    entities = (
        [v[0] for v in corpus_mod._VENDORS]
        + corpus_mod._BUYERS
        + corpus_mod._COMPANIES
        + corpus_mod._UNIS
    )
    exemplar_text = "\n".join(text for text, _ in prompts._ROUTER_EXEMPLARS)
    leaked = [e for e in entities if e in exemplar_text]
    assert not leaked, f"exemplars reuse corpus entities: {leaked}"


def test_routing_schema_is_a_closed_vocabulary():
    schema = json_schema_for(RoutingDecision)
    enum = schema["$defs"]["DocumentClass"]["enum"] if "$defs" in schema else None
    rendered = str(schema)
    for cls in routing.CLASSES:
        assert cls in rendered
    assert enum is None or set(enum) == set(routing.CLASSES)


# --------------------------------------------------------------------------- #
# Prototype 4 — self-consistency
# --------------------------------------------------------------------------- #


def test_vote_takes_the_majority_value():
    payloads = [
        {"invoice_number": "INV-1", "total_amount": 10.0},
        {"invoice_number": "INV-1", "total_amount": 10.0},
        {"invoice_number": "INV-1", "total_amount": 99.0},
    ]
    voted, margins = scoring.vote(payloads)
    assert voted["total_amount"] == 10.0
    assert margins["total_amount"] == pytest.approx(2 / 3)
    assert margins["invoice_number"] == 1.0


def test_vote_treats_formatting_variants_as_the_same_vote():
    voted, margins = scoring.vote(
        [{"vendor_name": "Acme Ltd"}, {"vendor_name": "ACME LTD"}, {"vendor_name": "Globex"}]
    )
    assert margins["vendor_name"] == pytest.approx(2 / 3)
    assert voted["vendor_name"] in ("Acme Ltd", "ACME LTD")


def test_vote_can_elect_null():
    voted, margins = scoring.vote(
        [{"purchase_order": None}, {"purchase_order": None}, {"purchase_order": "PO1"}]
    )
    assert voted["purchase_order"] is None
    assert margins["purchase_order"] == pytest.approx(2 / 3)


def test_vote_of_a_single_sample_is_that_sample():
    voted, margins = scoring.vote([{"a": 1.0}])
    assert voted == {"a": 1.0}
    assert margins == {"a": 1.0}


# --------------------------------------------------------------------------- #
# Prototype 5 — LLM as judge
# --------------------------------------------------------------------------- #


def test_detector_scoring_matches_the_definitions_in_the_report():
    from collections import Counter

    confusion = Counter(
        {"true_positive": 6, "false_positive": 2, "false_negative": 4, "true_negative": 88}
    )
    scores = judge_proto._detector_scores(confusion)
    assert scores["precision"] == pytest.approx(6 / 8, abs=1e-4)
    assert scores["recall"] == pytest.approx(6 / 10, abs=1e-4)
    assert scores["false_alarm_rate"] == pytest.approx(2 / 90, abs=1e-4)
    assert scores["fields"] == 100


@pytest.mark.parametrize(
    "flagged, really_wrong, cell",
    [
        (True, True, "true_positive"),
        (True, False, "false_positive"),
        (False, True, "false_negative"),
        (False, False, "true_negative"),
    ],
)
def test_judge_confusion_cells(flagged, really_wrong, cell):
    assert judge_proto._cell(flagged, really_wrong) == cell


def test_judge_field_paths_are_normalised_to_the_scorer_form():
    assert judge_proto._normalise_path("line_items.0.amount") == "line_items[0].amount"
    assert judge_proto._normalise_path("total_amount") == "total_amount"


def test_judge_schema_forbids_correcting_the_extraction():
    # The judge returns verdicts only; a schema that accepted corrected values
    # would make it a second extractor whose agreement proves nothing.
    properties = json_schema_for(JudgeReport)["properties"]
    assert set(properties) == {"verdicts"}


def test_judge_prompt_carries_both_the_document_and_the_extraction():
    text = prompts.judge_user_prompt("DOC TEXT", '{"a": 1}')
    assert "<document>" in text and "DOC TEXT" in text
    assert "<extraction>" in text and '"a": 1' in text
