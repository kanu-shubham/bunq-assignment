---
title: Runbook - merchant-directory
source: confluence
space: engineering
team: platform
doc_type: runbook
updated: 2026-06-09
tags: [runbook, merchant, oncall, platform]
---

# Runbook: merchant-directory

## Alert: MerchantEnrichmentHitRatioLow

Fires when `merchant_enrichment_hit_ratio` is below 0.85 for five minutes.

### Triage

1. Check the merchant-directory dashboard and confirm whether the signal is service-wide or
   confined to one pod. A single unhealthy pod is a restart; a service-wide
   signal is an investigation.
2. Check the enrichment vendor feed. Most of this service's bad days start somewhere else, and
   treating a downstream symptom as a local cause wastes the first twenty
   minutes of every incident.
3. Look at the ratio of `ERR_MERCHANT_NOT_FOUND` in the error breakdown. A spike in that specific
   code points at callers sending requests against a stale view of MerchantRecord
   state, which is a client problem and is fixed by talking to the caller.

### Mitigation

    kubectl -n platform rollout restart deploy/merchant-directory
    kubectl -n platform logs deploy/merchant-directory --since=15m | grep -i error

Restarting clears a wedged connection pool and nothing else. If the alert
returns within ten minutes, stop restarting and escalate — repeated restarts
hide the signal without addressing the cause.

### Escalation

Page the platform lead if customer-visible behaviour is affected for more than
fifteen minutes. Unmatched counterparties fall back to the raw scheme descriptor. Showing a raw descriptor is ugly; showing the wrong merchant is a support ticket.
