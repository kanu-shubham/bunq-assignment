"""Pydantic target schemas plus the JSON-Schema shaping the API's structured
outputs feature requires.

Two rules drive the schema design:

1.  Every field a messy document might omit is `Optional[...]` with a default of
    `None`.  A schema that *forces* a value is a hallucination generator: the
    model cannot satisfy `total_amount: float` on an invoice whose total was
    cropped off the scan, so it invents one.
2.  Constrained vocabularies are `enum`s.  Free-form currency strings drift
    ("$", "usd", "US Dollars"); a seven-member enum does not.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# Keywords the structured-outputs schema compiler does not accept.  Pydantic
# emits several of them from ordinary field declarations, so they are stripped
# before the schema is sent and re-checked client-side by Pydantic afterwards.
_UNSUPPORTED_KEYWORDS = frozenset(
    {
        "default",
        "examples",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "multipleOf",
        "pattern",
        "uniqueItems",
    }
)

Currency = Literal["USD", "EUR", "GBP", "CHF", "SEK", "JPY", "INR"]


class StrictModel(BaseModel):
    """Reject unknown keys so an invented field is a validation error, not a
    silently-ignored one."""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# Invoices
# --------------------------------------------------------------------------- #


class LineItem(StrictModel):
    description: Optional[str] = Field(None, description="Item or service description, verbatim.")
    quantity: Optional[float] = Field(None, description="Units billed.")
    unit_price: Optional[float] = Field(None, description="Price per unit, before tax.")
    amount: Optional[float] = Field(None, description="Line total as printed on the document.")


class Invoice(StrictModel):
    """Fields of a commercial invoice or credit note."""

    document_type: Optional[Literal["invoice", "credit_note"]] = Field(
        None, description="credit_note only when the document says so or all amounts are negative."
    )
    invoice_number: Optional[str] = Field(None, description="Invoice/document identifier as printed.")
    issue_date: Optional[str] = Field(None, description="Issue date normalised to YYYY-MM-DD.")
    due_date: Optional[str] = Field(None, description="Payment due date normalised to YYYY-MM-DD.")
    currency: Optional[Currency] = Field(None, description="ISO-4217 code of the amounts below.")
    vendor_name: Optional[str] = Field(None, description="Legal name of the party issuing the invoice.")
    vendor_tax_id: Optional[str] = Field(None, description="VAT/tax registration number of the vendor.")
    bill_to_name: Optional[str] = Field(None, description="Party being billed.")
    purchase_order: Optional[str] = Field(None, description="Customer PO reference, if one is printed.")
    line_items: list[LineItem] = Field(default_factory=list, description="One entry per billed line.")
    subtotal: Optional[float] = Field(None, description="Net total before tax.")
    tax_amount: Optional[float] = Field(None, description="Total tax charged.")
    total_amount: Optional[float] = Field(None, description="Gross amount payable.")


# --------------------------------------------------------------------------- #
# Resumes
# --------------------------------------------------------------------------- #


class WorkExperience(StrictModel):
    company: Optional[str] = None
    title: Optional[str] = None
    start_date: Optional[str] = Field(None, description="YYYY-MM, or YYYY when only a year is given.")
    end_date: Optional[str] = Field(None, description="YYYY-MM, YYYY, or null when the role is current.")
    is_current: Optional[bool] = Field(None, description="True only when the document says the role is ongoing.")


class Education(StrictModel):
    institution: Optional[str] = None
    degree: Optional[str] = Field(None, description="e.g. BSc, MSc, PhD, MBA — as printed.")
    field_of_study: Optional[str] = None
    graduation_year: Optional[int] = Field(None, description="Four-digit year of completion.")


class Resume(StrictModel):
    """Fields of a CV / resume."""

    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = Field(None, description="City and/or country, as printed.")
    current_title: Optional[str] = Field(None, description="Most recent job title.")
    years_experience: Optional[float] = Field(
        None,
        description=(
            "Only when the document states a total explicitly (e.g. '8+ years'). "
            "Do not derive it from the employment dates."
        ),
    )
    skills: list[str] = Field(default_factory=list, description="Skills listed in a skills section.")
    work_experience: list[WorkExperience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)


SCHEMAS: dict[str, type[StrictModel]] = {"invoice": Invoice, "resume": Resume}


def model_for(kind: str) -> type[StrictModel]:
    try:
        return SCHEMAS[kind]
    except KeyError:  # pragma: no cover - guarded by the CLI's choices=
        raise ValueError(f"unknown document kind {kind!r}; expected one of {sorted(SCHEMAS)}") from None


# --------------------------------------------------------------------------- #
# JSON-Schema shaping
# --------------------------------------------------------------------------- #


def _shape(node: Any) -> Any:
    """Recursively make a Pydantic-emitted schema acceptable to the API.

    Structured outputs require `additionalProperties: false` and an exhaustive
    `required` list on every object, and reject the numeric/string constraint
    keywords Pydantic likes to emit.
    """
    if isinstance(node, list):
        return [_shape(item) for item in node]
    if not isinstance(node, dict):
        return node

    shaped = {k: _shape(v) for k, v in node.items() if k not in _UNSUPPORTED_KEYWORDS}

    if shaped.get("type") == "object" or "properties" in shaped:
        properties = shaped.setdefault("properties", {})
        shaped["additionalProperties"] = False
        # Optional-ness is expressed by the `null` member of each anyOf, not by
        # omission from `required` — the compiler wants every key listed.
        shaped["required"] = list(properties)
    return shaped


def json_schema_for(model: type[BaseModel]) -> dict[str, Any]:
    """The `output_config.format.schema` payload for `model`."""
    return _shape(model.model_json_schema())


def format_config(model: type[BaseModel]) -> dict[str, Any]:
    """The full `output_config` value for a strict-mode request."""
    return {
        "format": {
            "type": "json_schema",
            "name": model.__name__.lower(),
            "schema": json_schema_for(model),
        }
    }
