---
title: Runbook - fx-pricing
source: confluence
space: engineering
team: payments
doc_type: runbook
updated: 2026-04-22
tags: [runbook, fx, oncall, payments]
---

# Runbook: fx-pricing

## Alert: FxRateFeedStale

Fires when `fx_quote_age_seconds` is above 90 seconds for five minutes.

### Triage

1. Check the fx-pricing dashboard and confirm whether the signal is service-wide or
   confined to one pod. A single unhealthy pod is a restart; a service-wide
   signal is an investigation.
2. Check the rate provider feed. Most of this service's bad days start somewhere else, and
   treating a downstream symptom as a local cause wastes the first twenty
   minutes of every incident.
3. Look at the ratio of `ERR_QUOTE_EXPIRED` in the error breakdown. A spike in that specific
   code points at callers sending requests against a stale view of FxQuote
   state, which is a client problem and is fixed by talking to the caller.

### Mitigation

    kubectl -n payments rollout restart deploy/fx-pricing
    kubectl -n payments logs deploy/fx-pricing --since=15m | grep -i error

Restarting clears a wedged connection pool and nothing else. If the alert
returns within ten minutes, stop restarting and escalate — repeated restarts
hide the signal without addressing the cause.

### Escalation

Page the payments lead if customer-visible behaviour is affected for more than
fifteen minutes. Quotes are valid for 60 seconds. An expired quote is never silently refreshed — the caller must request a new one and show the customer the new rate.
