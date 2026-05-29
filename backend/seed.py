"""
Seed script — demonstrates all 5 workflow types.
Run with: docker compose --profile seed run seed
Or directly: python seed.py (with backend running)
"""
import asyncio
import httpx
import os
import json
import time

API_URL = os.environ.get("API_URL", "http://localhost:8000")

WORKFLOWS = [
    # 1. T+1 Settlement Break
    {
        "type": "SETTLEMENT",
        "title": "T+1 Settlement Break — EUR/USD FX Trade TRD-2024-0042",
        "description": "Settlement break detected between internal system and SWIFT confirmation. Amount mismatch of USD 2.5M.",
        "payload": {
            "amount": 2500000,
            "currency": "USD",
            "counterparty": "DEUTSCHE_BANK",
            "trade_id": "TRD-2024-0042",
            "is_cross_border": True,
            "value_date": "2024-12-20",
            "swift_ref": "BNQNL2A",
            "internal_amount": 2500000,
            "confirmed_amount": 2499950,
            "break_reason": "rounding_difference",
        },
        "created_by": "alice_trader",
        "approve_as": [
            {"actor": "bob_risk", "role": "RISK", "decision": "approve", "comment": "Break within tolerance"},
            {"actor": "dave_ops", "role": "OPERATIONS", "decision": "approve", "comment": "Confirmed with counterparty"},
        ],
    },
    # 2. Margin Call Dispute
    {
        "type": "DISPUTE",
        "title": "Margin Call Dispute — Counterparty Pricing Disagreement",
        "description": "Counterparty Deutsche Bank disputes our margin call of USD 1.8M citing stale pricing in our system.",
        "payload": {
            "dispute_reason": "Counterparty disputes margin call based on T-1 pricing vs real-time",
            "original_trade_id": "TRD-2024-0039",
            "disputed_amount": 1800000,
            "currency": "USD",
            "csa_reference": "CSA-2023-001",
            "legal_action_threatened": False,
            "counterparty": "DEUTSCHE_BANK",
        },
        "created_by": "dave_ops",
        # Leave in AWAITING_APPROVAL state (no auto-approve)
        "approve_as": [],
    },
    # 3. Cross-border SWIFT Settlement
    {
        "type": "SETTLEMENT",
        "title": "Cross-border Settlement — SWIFT MT103 Wire SGD 5M",
        "description": "Cross-border settlement to Singapore counterparty. Requires KYC/sanctions screen and 2-of-3 approval.",
        "payload": {
            "amount": 5000000,
            "currency": "SGD",
            "counterparty": "DBS_SINGAPORE",
            "trade_id": "TRD-2024-0088",
            "is_cross_border": True,
            "value_date": "2024-12-21",
            "ordering_customer": "BUNQ_NV",
            "beneficiary": "DBS_SGP",
            "swift_message_type": "MT103",
        },
        "created_by": "alice_trader",
        "approve_as": [],
    },
    # 4. EMIR Regulatory Filing
    {
        "type": "REGULATORY_FILING",
        "title": "EMIR Refit Filing — OTC Derivative UTI-2024-EUR-0042",
        "description": "Mandatory EMIR refit regulatory filing for OTC interest rate swap. T+1 deadline.",
        "payload": {
            "regime": "EMIR",
            "uti": "UTI-2024-EUR-0042",
            "trade_repository": "DTCC_EU",
            "reporting_counterparty": "BUNQ_NV",
            "counterparty_lei": "724500PM2FQT54OS4E22",
            "asset_class": "IR_SWAP",
            "notional": 10000000,
            "currency": "EUR",
            "maturity_date": "2029-12-15",
            "late_filing": False,
        },
        "created_by": "carol_compliance",
        "approve_as": [
            {"actor": "carol_compliance", "role": "COMPLIANCE", "decision": "approve", "comment": "All fields validated"},
        ],
    },
    # 5. Reconciliation Exception
    {
        "type": "RECONCILIATION",
        "title": "Daily Rec Exception — EUR Custody Account Break USD 12,450",
        "description": "End-of-day reconciliation flagged a USD 12,450 break between internal books and custodian BNP Paribas statement.",
        "payload": {
            "break_amount": 12450.0,
            "currency": "USD",
            "internal_balance": 4987550.0,
            "custodian_balance": 5000000.0,
            "tolerance": 0.01,
            "rec_date": "2024-12-19",
            "custody_account": "EUR-CUST-001",
            "custodian": "BNP_PARIBAS",
            "root_cause_candidate": "accrued_interest_rounding",
        },
        "created_by": "dave_ops",
        "approve_as": [
            {"actor": "dave_ops2", "role": "OPERATIONS", "decision": "approve", "comment": "Adjustment booked"},
        ],
    },
]


async def seed():
    print(f"Seeding {API_URL}...")
    # Wait for backend to be ready
    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(20):
            try:
                r = await client.get(f"{API_URL}/health")
                if r.status_code == 200:
                    print("Backend ready.")
                    break
            except Exception:
                pass
            print(f"  Waiting for backend... ({attempt+1}/20)")
            await asyncio.sleep(2)
        else:
            print("Backend not ready after 40s — aborting.")
            return

        created = []
        for wf_data in WORKFLOWS:
            approve_actions = wf_data.pop("approve_as", [])
            try:
                r = await client.post(f"{API_URL}/api/workflows/", json=wf_data)
                r.raise_for_status()
                wf = r.json()
                print(f"  Created: [{wf['type']}] {wf['title'][:50]}... id={wf['id'][:8]}")
                created.append(wf)

                # Submit any pre-defined approval actions
                for action in approve_actions:
                    try:
                        ar = await client.post(
                            f"{API_URL}/api/workflows/{wf['id']}/approve",
                            json=action,
                        )
                        if ar.status_code == 200:
                            res = ar.json()
                            print(f"    Approval by {action['actor']}: {res.get('gate_status')}")
                        else:
                            print(f"    Approval failed: {ar.text[:100]}")
                    except Exception as e:
                        print(f"    Approval error: {e}")
            except Exception as e:
                print(f"  Error creating workflow: {e}")

        print(f"\nSeeding complete. {len(created)} workflows created.")
        print("\nSummary:")
        for wf in created:
            print(f"  {wf['id'][:8]}  [{wf['state']:20}]  {wf['title'][:55]}")


if __name__ == "__main__":
    asyncio.run(seed())
