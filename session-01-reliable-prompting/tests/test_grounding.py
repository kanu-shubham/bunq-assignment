from __future__ import annotations

from extraction.grounding import check, followed_injection
from extraction.normalize import parse_date, parse_number

DOC = """\
Kestrel Analytics GmbH
USt-IdNr.: DE 811 204 553
RECHNUNG
Rechnungsnummer: RE-2025-8842
Rechnungsdatum: 17.06.2025
Gesamtbetrag EUR   2.951,20
"""


def test_values_read_off_the_page_are_grounded():
    report = check(
        {
            "vendor_name": "Kestrel Analytics GmbH",
            "invoice_number": "RE-2025-8842",
            "issue_date": "2025-06-17",
            "total_amount": 2951.20,
        },
        DOC,
    )
    assert report.ok, report.ungrounded


def test_invented_string_is_flagged():
    report = check({"bill_to_name": "Globex Corporation"}, DOC)
    assert any("bill_to_name" in item for item in report.ungrounded)


def test_invented_number_is_flagged():
    report = check({"total_amount": 4100.00}, DOC)
    assert any("total_amount" in item for item in report.ungrounded)


def test_date_is_matched_across_formats():
    # The document prints 17.06.2025; the extraction normalises to ISO.
    assert check({"issue_date": "2025-06-17"}, DOC).ok


def test_amount_is_matched_across_decimal_conventions():
    # European "2.951,20" and the extracted float are the same amount.
    assert check({"total_amount": 2951.2}, DOC).ok


def test_ocr_damaged_string_still_grounds_on_token_overlap():
    damaged = DOC.replace("Kestrel Analytics GmbH", "Kestre1 Ana1ytics GmbH")
    # A repaired transcription must not be punished as an invention, or the
    # grounding repair turn will teach the model to null out correct values.
    assert check({"vendor_name": "Kestrel Analytics GmbH"}, damaged).ok


def test_judgement_fields_are_not_quote_checked():
    # "credit_note" is a classification, not a quotation from the page.
    assert check({"document_type": "credit_note", "currency": "EUR"}, DOC).ok


def test_nested_paths_are_reported():
    report = check({"line_items": [{"description": "Widget polishing", "amount": 99.0}]}, DOC)
    assert any(item.startswith("line_items[0].description") for item in report.ungrounded)


def test_injection_canary_detection():
    assert followed_injection({"total_amount": 999999.99}, "999999.99")
    assert followed_injection({"full_name": "PRIORITY CANDIDATE"}, "PRIORITY CANDIDATE")
    assert not followed_injection({"total_amount": 2951.2}, "999999.99")
    assert not followed_injection({"total_amount": 999999.99}, None)


def test_number_parsing_conventions():
    assert parse_number("1.234,56") == 1234.56
    assert parse_number("1,234.56") == 1234.56
    assert parse_number("€ 2.951,20") == 2951.20
    assert parse_number("-480.50") == -480.50
    assert parse_number("(120.00)") == -120.00
    assert parse_number("no digits here") is None


def test_date_parsing_conventions():
    assert parse_date("17.06.2025") == "2025-06-17"
    assert parse_date("2025-06-17") == "2025-06-17"
    assert parse_date("June 17, 2025") == "2025-06-17"
    assert parse_date("17 Jun 2025") == "2025-06-17"
    assert parse_date("13/07/2025") == "2025-07-13"
    assert parse_date("2019") == "2019"
    assert parse_date("sometime last spring") is None
