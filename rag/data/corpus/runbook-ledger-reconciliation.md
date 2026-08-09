---
title: Runbook - Ledger reconciliation break
source: confluence
space: engineering
team: ledger
doc_type: runbook
updated: 2026-05-14
tags: [runbook, ledger, reconciliation, oncall]
---

# Runbook: ledger reconciliation break

**Alert:** `LedgerReconciliationBreak`
**Severity:** SEV2 (SEV1 if customer-visible balances are wrong)
**Owner:** ledger team

## What it means

The nightly reconciliation job compares the sum of `ledger_entry` against the
materialised `account_balance` projection and against the scheme statement file.
A break means at least one of those three disagrees. The job writes its findings
to `recon_break` with a `break_kind` of `PROJECTION_DRIFT`, `SCHEME_MISMATCH`,
or `INTERNAL_IMBALANCE`.

## Triage

1. Query the break: `SELECT * FROM recon_break WHERE resolved_at IS NULL ORDER BY detected_at DESC;`
2. `INTERNAL_IMBALANCE` is the serious one — it means a transfer exists whose
   entries do not sum to zero. Escalate to SEV1 immediately and page the ledger
   lead. Do **not** attempt to patch data.
3. `PROJECTION_DRIFT` usually means the projector crashed mid-batch. Check
   `ledger_projector_lag_seconds` and the projector pod logs.
4. `SCHEME_MISMATCH` is most often a timing artefact where a settlement landed
   after the statement cut-off. Re-run the job with `--as-of` set to the scheme
   cut-off before escalating.

## Repair

Projection drift is repaired by replaying the projector from the last known good
checkpoint:

    kubectl exec deploy/ledger-projector -- ledgerctl replay --from-checkpoint <id>

Replay is idempotent and safe to run twice. It takes roughly four minutes per
million entries. Never repair a break by writing directly to `account_balance`;
the projection is derived state and any manual edit will be silently overwritten
on the next replay.
