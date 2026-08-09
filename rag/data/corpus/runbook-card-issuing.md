---
title: Runbook - card-issuing
source: confluence
space: engineering
team: payments
doc_type: runbook
updated: 2026-02-08
tags: [runbook, card, oncall, payments]
---

# Runbook: card-issuing

## Alert: CardIssuingVendorErrors

Fires when `card_activation_latency_seconds` is p95 above 3 seconds for five minutes.

### Triage

1. Check the card-issuing dashboard and confirm whether the signal is service-wide or
   confined to one pod. A single unhealthy pod is a restart; a service-wide
   signal is an investigation.
2. Check identity-kyc. Most of this service's bad days start somewhere else, and
   treating a downstream symptom as a local cause wastes the first twenty
   minutes of every incident.
3. Look at the ratio of `ERR_CARD_ALREADY_ACTIVATED` in the error breakdown. A spike in that specific
   code points at callers sending requests against a stale view of CardOrder
   state, which is a client problem and is fixed by talking to the caller.

### Mitigation

    kubectl -n payments rollout restart deploy/card-issuing
    kubectl -n payments logs deploy/card-issuing --since=15m | grep -i error

Restarting clears a wedged connection pool and nothing else. If the alert
returns within ten minutes, stop restarting and escalate — repeated restarts
hide the signal without addressing the cause.

### Escalation

Page the payments lead if customer-visible behaviour is affected for more than
fifteen minutes. Card PANs never enter our systems; the issuer processor holds them and we store a token plus the last four digits.
