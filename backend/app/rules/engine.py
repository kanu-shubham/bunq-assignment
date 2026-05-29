"""
Pure-Python deterministic rules engine.
All validation functions are side-effect free and synchronous.
The LLM layer calls these BEFORE proposing; the commit layer re-validates.
"""
from __future__ import annotations
from typing import Any
from datetime import datetime


class RuleViolation(Exception):
    def __init__(self, rule: str, detail: str):
        self.rule = rule
        self.detail = detail
        super().__init__(f"[{rule}] {detail}")


# ---------------------------------------------------------------------------
# Settlement rules
# ---------------------------------------------------------------------------

def validate_settlement(payload: dict[str, Any]) -> list[str]:
    """Returns list of validation messages (empty = pass)."""
    issues = []
    amount = payload.get("amount", 0)
    if not isinstance(amount, (int, float)) or amount <= 0:
        issues.append("Settlement amount must be positive")
    if amount > 50_000_000:
        issues.append("Settlement amount exceeds single-trade limit of 50M; escalation required")
    currency = payload.get("currency", "")
    if currency not in {"USD", "EUR", "GBP", "JPY", "CHF", "SGD"}:
        issues.append(f"Currency '{currency}' not in approved list")
    counterparty = payload.get("counterparty", "")
    if not counterparty:
        issues.append("Counterparty is required")
    # Sanctions stub — real impl would call OFAC/UN list
    sanctioned = {"SANCTIONED_BANK", "BLOCKED_ENTITY"}
    if counterparty.upper() in sanctioned:
        issues.append(f"Counterparty '{counterparty}' appears on sanctions list — BLOCKED")
    return issues


def validate_swift_mt103(payload: dict[str, Any]) -> list[str]:
    """Validate a proposed SWIFT MT103 message."""
    issues = []
    required = ["ordering_customer", "beneficiary", "amount", "currency", "value_date"]
    for f in required:
        if not payload.get(f):
            issues.append(f"SWIFT MT103 missing required field: {f}")
    try:
        datetime.strptime(payload.get("value_date", ""), "%Y-%m-%d")
    except ValueError:
        issues.append("value_date must be YYYY-MM-DD")
    return issues


# ---------------------------------------------------------------------------
# Reconciliation rules
# ---------------------------------------------------------------------------

def validate_reconciliation(payload: dict[str, Any]) -> list[str]:
    issues = []
    tolerance = payload.get("tolerance", 0.01)
    break_amount = abs(payload.get("break_amount", 0))
    if break_amount > 1_000_000:
        issues.append(f"Break amount {break_amount:,.2f} exceeds escalation threshold of 1M")
    if tolerance < 0 or tolerance > 0.05:
        issues.append("Tolerance must be between 0 and 5%")
    return issues


# ---------------------------------------------------------------------------
# Dispute rules
# ---------------------------------------------------------------------------

def validate_dispute(payload: dict[str, Any]) -> list[str]:
    issues = []
    if not payload.get("dispute_reason"):
        issues.append("Dispute reason is required")
    if not payload.get("original_trade_id"):
        issues.append("Original trade ID must be referenced in dispute")
    return issues


# ---------------------------------------------------------------------------
# Margin call rules
# ---------------------------------------------------------------------------

def validate_margin_call(payload: dict[str, Any]) -> list[str]:
    issues = []
    call_amount = payload.get("call_amount", 0)
    if call_amount <= 0:
        issues.append("Margin call amount must be positive")
    if call_amount > 10_000_000:
        issues.append("Margin call > 10M requires senior risk officer approval")
    if not payload.get("portfolio_id"):
        issues.append("Portfolio ID required for margin call")
    return issues


# ---------------------------------------------------------------------------
# Regulatory filing rules
# ---------------------------------------------------------------------------

def validate_regulatory_filing(payload: dict[str, Any]) -> list[str]:
    issues = []
    regime = payload.get("regime", "")
    if regime not in {"EMIR", "MIFID2", "DODD_FRANK", "SFTR"}:
        issues.append(f"Reporting regime '{regime}' not supported")
    if not payload.get("trade_repository"):
        issues.append("Trade repository must be specified")
    if not payload.get("uti"):
        issues.append("UTI (Unique Trade Identifier) is required")
    return issues


# ---------------------------------------------------------------------------
# Risk scoring
# ---------------------------------------------------------------------------

def compute_risk_score(workflow_type: str, payload: dict[str, Any]) -> float:
    """Heuristic 0-1 risk score. Deterministic, no ML."""
    score = 0.0
    if workflow_type == "SETTLEMENT":
        amount = payload.get("amount", 0)
        score += min(amount / 50_000_000, 0.5)
        if payload.get("is_cross_border"):
            score += 0.2
        if payload.get("currency") not in {"USD", "EUR"}:
            score += 0.1
    elif workflow_type == "MARGIN_CALL":
        call_amount = payload.get("call_amount", 0)
        score += min(call_amount / 10_000_000, 0.6)
        if payload.get("disputed"):
            score += 0.3
    elif workflow_type == "DISPUTE":
        score += 0.5
        if payload.get("legal_action_threatened"):
            score += 0.4
    elif workflow_type == "REGULATORY_FILING":
        score += 0.3
        if payload.get("late_filing"):
            score += 0.4
    elif workflow_type == "RECONCILIATION":
        break_amount = abs(payload.get("break_amount", 0))
        score += min(break_amount / 1_000_000, 0.5)
    return min(round(score, 3), 1.0)


VALIDATORS = {
    "SETTLEMENT": validate_settlement,
    "RECONCILIATION": validate_reconciliation,
    "DISPUTE": validate_dispute,
    "MARGIN_CALL": validate_margin_call,
    "REGULATORY_FILING": validate_regulatory_filing,
}


def run_rules(workflow_type: str, payload: dict[str, Any]) -> dict:
    """Run all applicable rules. Returns {passed, issues, risk_score}."""
    validator = VALIDATORS.get(workflow_type)
    issues = validator(payload) if validator else []
    risk_score = compute_risk_score(workflow_type, payload)
    return {
        "passed": len(issues) == 0,
        "issues": issues,
        "risk_score": risk_score,
    }
