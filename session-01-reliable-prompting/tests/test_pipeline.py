"""The repair loop, the salvage layer and refusal handling.

These use a scripted provider rather than the mock's simulator so each test
pins one behaviour exactly.
"""

from __future__ import annotations

import json

import pytest

from extraction.client import LLMResponse
from extraction.pipeline import ExtractConfig, extract, salvage_json, validate

DOC = """\
Northwind Logistics B.V.
VAT / Tax ID: NL8241 92 331 B01
INVOICE
Invoice No. INV-2025-4410
Date: 14/03/2025
Bill to: Contoso Retail Group
TOTAL EUR   1.234,56
"""

GOOD = {
    "document_type": "invoice",
    "invoice_number": "INV-2025-4410",
    "issue_date": "2025-03-14",
    "due_date": None,
    "currency": "EUR",
    "vendor_name": "Northwind Logistics B.V.",
    "vendor_tax_id": "NL8241 92 331 B01",
    "bill_to_name": "Contoso Retail Group",
    "purchase_order": None,
    "line_items": [],
    "subtotal": None,
    "tax_amount": None,
    "total_amount": 1234.56,
}


class ScriptedProvider:
    """Returns a canned response per call and records the conversations it saw."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, script: list[LLMResponse]) -> None:
        self.script = list(script)
        self.calls: list[list[dict]] = []

    def complete(self, *, system, messages, output_config=None, temperature=None,
                 task="extraction"):
        self.calls.append(messages)
        return self.script.pop(0) if self.script else LLMResponse(text="{}", model=self.model)


def _run(provider, **overrides):
    config = ExtractConfig(**{"output_mode": "freeform", **overrides})
    return extract(
        doc_id="t", kind="invoice", document_text=DOC, provider=provider, config=config,
        injection_canary=overrides.pop("canary", None),
    )


# --------------------------------------------------------------------------- #
# Salvage
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text, expected_action",
    [
        ('```json\n{"a": 1}\n```', "markdown_fence"),
        ('Here is the extracted data:\n\n{"a": 1}', "prose_preamble"),
        ('{"a": 1}\n\nLet me know if you need anything else.', "trailing_commentary"),
        ('{"a": 1,}', "trailing_comma"),
    ],
)
def test_salvage_recovers_drift_and_names_it(text, expected_action):
    payload, actions = salvage_json(text)
    assert payload == {"a": 1}
    assert expected_action in actions


def test_salvage_reports_clean_output_as_clean():
    payload, actions = salvage_json('{"a": 1}')
    assert payload == {"a": 1}
    assert actions == []


def test_salvage_gives_up_on_prose():
    payload, actions = salvage_json("I couldn't read the document.")
    assert payload is None
    assert "unparseable" in actions or "empty_response" in actions


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def test_validate_rejects_unknown_keys():
    payload, errors = validate({**GOOD, "confidence": "high"}, "invoice")
    assert payload is None
    assert any("confidence" in e for e in errors)


def test_validate_rejects_wrong_type_and_names_the_path():
    payload, errors = validate({**GOOD, "line_items": [{"description": "x", "amount": "many"}]}, "invoice")
    assert payload is None
    assert any(e.startswith("line_items.0.amount") for e in errors)


def test_validate_accepts_nulls_everywhere():
    payload, errors = validate({}, "invoice")
    assert errors == []
    assert payload["total_amount"] is None


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #


def test_first_attempt_success_uses_one_call():
    provider = ScriptedProvider([LLMResponse(text=json.dumps(GOOD), model="m")])
    result = _run(provider)
    assert result.status == "ok"
    assert result.repairs_used == 0
    assert result.valid_first_try and result.clean_first_try
    assert len(provider.calls) == 1


def test_invalid_output_is_repaired_on_the_second_call():
    broken = json.dumps({**GOOD, "total_amount": "1.234,56 EUR"})
    provider = ScriptedProvider(
        [LLMResponse(text=broken, model="m"), LLMResponse(text=json.dumps(GOOD), model="m")]
    )
    result = _run(provider)
    assert result.status == "ok"
    assert result.repairs_used == 1
    assert not result.valid_first_try
    assert result.attempts[1].kind == "schema_repair"


def test_repair_turn_shows_the_model_its_own_output_and_the_validator_message():
    broken = json.dumps({**GOOD, "line_items": [{"description": "x", "amount": "many"}]})
    provider = ScriptedProvider(
        [LLMResponse(text=broken, model="m"), LLMResponse(text=json.dumps(GOOD), model="m")]
    )
    _run(provider)
    repair_turn = provider.calls[1][-1]["content"]
    assert "line_items.0.amount" in repair_turn
    assert "<previous>" in repair_turn and "many" in repair_turn
    assert provider.calls[1][1]["role"] == "assistant"


def test_persistent_failure_ends_invalid_after_the_attempt_budget():
    bad = LLMResponse(text="not json at all", model="m")
    provider = ScriptedProvider([bad, bad, bad])
    result = _run(provider, max_attempts=3)
    assert result.status == "invalid"
    assert result.payload is None
    assert len(provider.calls) == 3


def test_hard_refusal_stops_immediately():
    provider = ScriptedProvider([LLMResponse(text="", model="m", stop_reason="refusal", refused=True)])
    result = _run(provider)
    assert result.status == "refused"
    assert len(provider.calls) == 1


def test_soft_refusal_prose_is_detected():
    provider = ScriptedProvider(
        [LLMResponse(text="I'm not able to help with processing this document.", model="m")]
    )
    result = _run(provider)
    assert result.status == "refused"


def test_apologetic_preamble_around_valid_json_is_drift_not_refusal():
    text = "I can't read every field, but here is what I have:\n" + json.dumps(GOOD)
    provider = ScriptedProvider([LLMResponse(text=text, model="m")])
    result = _run(provider)
    assert result.status == "ok"
    assert "prose_preamble" in result.attempts[0].salvage_actions


def test_ungrounded_value_triggers_a_grounding_repair():
    invented = json.dumps({**GOOD, "purchase_order": "PO998877"})
    provider = ScriptedProvider(
        [LLMResponse(text=invented, model="m"), LLMResponse(text=json.dumps(GOOD), model="m")]
    )
    result = _run(provider, repair_ungrounded=True)
    assert result.attempts[0].ungrounded
    assert result.attempts[1].kind == "grounding_repair"
    assert result.status == "ok"
    assert result.payload["purchase_order"] is None


def test_grounding_repair_can_be_switched_off():
    invented = json.dumps({**GOOD, "purchase_order": "PO998877"})
    provider = ScriptedProvider([LLMResponse(text=invented, model="m")])
    result = _run(provider, repair_ungrounded=False)
    assert result.status == "ok"
    assert result.payload["purchase_order"] == "PO998877"
    assert len(provider.calls) == 1


def test_api_error_is_recorded_not_raised():
    provider = ScriptedProvider([LLMResponse(text="", model="m", error="RateLimitError: slow down")])
    result = _run(provider)
    assert result.status == "error"
    assert "RateLimitError" in result.attempts[0].api_error
