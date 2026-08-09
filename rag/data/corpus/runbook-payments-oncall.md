---
title: Runbook - payments-api on-call
source: confluence
space: engineering
team: payments
doc_type: runbook
updated: 2026-06-15
tags: [runbook, payments, oncall, latency, errors]
---

# Runbook: payments-api on-call

## Alert: PaymentsApiHighErrorRate

Fires when the 5xx rate exceeds 1% over five minutes.

1. Check the error breakdown in the payments dashboard, panel "errors by code".
2. `ERR_SCHEME_TIMEOUT` dominating means the scheme gateway is slow. Confirm on
   the scheme status page, then consider flipping
   `payments.sepa_instant.enabled` off to shed instant traffic while regular
   SEPA continues to work.
3. `ERR_LEDGER_UNAVAILABLE` means ledger-core is unhealthy. Payments will queue;
   nothing is lost, but customers see failures. Follow the ledger runbook.
4. `ERR_IDEMPOTENCY_KEY_REUSE` spiking is almost always a partner bug, not ours.
   Identify the client from `client_id` and reach out; do not disable the check.

## Alert: PaymentsSubmissionBacklog

Fires when payments have been stuck in `PENDING_SUBMISSION` for more than ten
minutes. Inspect the submission reaper:

    kubectl logs deploy/payments-reaper --since=15m | grep -i "submit failed"

A backlog with no errors in the reaper log usually means the reaper is not
running at all — check that the deployment has at least one ready replica.

## Escalation

Page the payments lead for anything customer-money-affecting. For a scheme
outage, notify the compliance duty officer as well, because prolonged SEPA
Instant unavailability is a reportable service disruption.
