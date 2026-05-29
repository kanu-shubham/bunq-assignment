"""
LLM Advisory layer — mock mode returns canned plausible responses.
Real mode would call Claude API. The LLM ONLY proposes; never commits.
"""
from __future__ import annotations
import os
import json
from typing import Any


MOCK_MODE = os.environ.get("LLM_MOCK", "true").lower() != "false"


# ---------------------------------------------------------------------------
# Mock proposals per workflow type
# ---------------------------------------------------------------------------

_MOCK_PROPOSALS: dict[str, dict] = {
    "SETTLEMENT": {
        "reasoning": (
            "Based on analysis of the trade details, the settlement amount matches "
            "the agreed notional. Counterparty LEI has been verified against our records. "
            "No sanctions hits detected in pre-screening. Proposed settlement via SWIFT MT103 "
            "with T+1 value date."
        ),
        "proposal": {
            "action": "APPROVE_SETTLEMENT",
            "swift_message_type": "MT103",
            "recommended_approvers": ["OPERATIONS", "RISK"],
            "notes": "Standard FX settlement — no unusual features detected.",
        },
    },
    "RECONCILIATION": {
        "reasoning": (
            "Reconciliation break identified between internal books (USD 12,450.00) and "
            "custodian statement (USD 12,500.00). Delta of USD 50.00 likely due to accrued "
            "interest calculation difference. Proposed fix: adjust internal accrual by 50 USD."
        ),
        "proposal": {
            "action": "ADJUST_INTERNAL_ACCRUAL",
            "adjustment_amount": 50.0,
            "adjustment_currency": "USD",
            "root_cause": "accrued_interest_rounding",
            "recommended_approvers": ["OPERATIONS"],
        },
    },
    "DISPUTE": {
        "reasoning": (
            "Counterparty claims the margin call is based on stale pricing. Internal system "
            "shows EOD prices from T-1; counterparty is using real-time. The difference is "
            "within contractual tolerance bands per the CSA agreement (Clause 11.3). "
            "Recommend drafting formal response citing CSA terms."
        ),
        "proposal": {
            "action": "DRAFT_DISPUTE_RESPONSE",
            "csa_clause": "11.3",
            "recommended_response": "Prices are per agreed valuation methodology. Dispute rejected.",
            "recommended_approvers": ["RISK", "LEGAL"],
        },
    },
    "MARGIN_CALL": {
        "reasoning": (
            "Margin call triggered by 3.2% adverse move in EUR/USD portfolio. VaR breach "
            "on 3 positions. Collateral currently posted: USD 4.2M. Additional call: USD 1.8M. "
            "Recommend posting additional gilts as collateral or cash USD."
        ),
        "proposal": {
            "action": "POST_ADDITIONAL_COLLATERAL",
            "collateral_type": "GILTS",
            "amount": 1_800_000,
            "currency": "USD",
            "recommended_approvers": ["RISK", "LEGAL"],
        },
    },
    "REGULATORY_FILING": {
        "reasoning": (
            "EMIR refit filing for trade UTI-2024-EUR-0042. All mandatory fields populated. "
            "Collateral reporting included per updated EMIR Refit requirements. "
            "Trade repository: DTCC EU. LEI validation passed for both counterparties."
        ),
        "proposal": {
            "action": "SUBMIT_EMIR_FILING",
            "trade_repository": "DTCC_EU",
            "filing_format": "ISO20022",
            "mandatory_fields_complete": True,
            "recommended_approvers": ["COMPLIANCE", "LEGAL"],
        },
    },
}


async def get_proposal(workflow_type: str, payload: dict[str, Any], rules_result: dict) -> dict:
    """Return LLM proposal. Mock returns canned response; real mode calls Claude API."""
    if MOCK_MODE:
        mock = _MOCK_PROPOSALS.get(workflow_type, {
            "reasoning": "No specific proposal template for this workflow type.",
            "proposal": {"action": "MANUAL_REVIEW"},
        })
        return {
            "llm_input": json.dumps({"workflow_type": workflow_type, "payload": payload, "rules": rules_result}),
            "llm_output": json.dumps(mock),
            "reasoning": mock["reasoning"],
            "proposal": mock["proposal"],
        }

    # Real Claude API mode
    try:
        import anthropic  # type: ignore
        client = anthropic.Anthropic()
        prompt = (
            f"You are a financial risk advisor. A {workflow_type} workflow requires analysis.\n\n"
            f"Workflow payload: {json.dumps(payload, indent=2)}\n\n"
            f"Rules engine result: {json.dumps(rules_result, indent=2)}\n\n"
            "Provide a JSON response with keys: 'reasoning' (string) and 'proposal' (object). "
            "The proposal must include 'action' and 'recommended_approvers'. "
            "NEVER suggest direct cash movement — only propose actions for human approval."
        )
        msg = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text
        parsed = json.loads(raw)
        return {
            "llm_input": prompt,
            "llm_output": raw,
            "reasoning": parsed.get("reasoning", ""),
            "proposal": parsed.get("proposal", {}),
        }
    except Exception as e:
        return {
            "llm_input": "",
            "llm_output": f"LLM call failed: {e}",
            "reasoning": "LLM unavailable — manual review required.",
            "proposal": {"action": "MANUAL_REVIEW", "error": str(e)},
        }
