---
title: Runbook - direct-debit-mandates
source: confluence
space: engineering
team: payments
doc_type: runbook
updated: 2026-03-15
tags: [runbook, direct, oncall, payments]
---

# Runbook: direct-debit-mandates

## Alert: MandateValidationFailures

Fires when `mandate_validation_failures_total` is above 50 per minute for five minutes.

### Triage

1. Check the direct-debit-mandates dashboard and confirm whether the signal is service-wide or
   confined to one pod. A single unhealthy pod is a restart; a service-wide
   signal is an investigation.
2. Check payments-api. Most of this service's bad days start somewhere else, and
   treating a downstream symptom as a local cause wastes the first twenty
   minutes of every incident.
3. Look at the ratio of `ERR_MANDATE_EXPIRED` in the error breakdown. A spike in that specific
   code points at callers sending requests against a stale view of Mandate
   state, which is a client problem and is fixed by talking to the caller.

### Mitigation

    kubectl -n payments rollout restart deploy/direct-debit-mandates
    kubectl -n payments logs deploy/direct-debit-mandates --since=15m | grep -i error

Restarting clears a wedged connection pool and nothing else. If the alert
returns within ten minutes, stop restarting and escalate — repeated restarts
hide the signal without addressing the cause.

### Escalation

Page the payments lead if customer-visible behaviour is affected for more than
fifteen minutes. A mandate that has not been used for 36 months lapses automatically and cannot be revived; the customer must sign a new one.
