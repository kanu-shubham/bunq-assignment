---
title: Runbook - sepa-gateway-adapter
source: confluence
space: engineering
team: payments
doc_type: runbook
updated: 2026-03-18
tags: [runbook, sepa, oncall, payments]
---

# Runbook: sepa-gateway-adapter

## Alert: SchemeRejectRatioHigh

Fires when `scheme_message_reject_ratio` is above 0.02 for five minutes.

### Triage

1. Check the sepa-gateway-adapter dashboard and confirm whether the signal is service-wide or
   confined to one pod. A single unhealthy pod is a restart; a service-wide
   signal is an investigation.
2. Check the scheme gateway. Most of this service's bad days start somewhere else, and
   treating a downstream symptom as a local cause wastes the first twenty
   minutes of every incident.
3. Look at the ratio of `ERR_SCHEME_MESSAGE_REJECTED` in the error breakdown. A spike in that specific
   code points at callers sending requests against a stale view of SchemeMessage
   state, which is a client problem and is fixed by talking to the caller.

### Mitigation

    kubectl -n payments rollout restart deploy/sepa-gateway-adapter
    kubectl -n payments logs deploy/sepa-gateway-adapter --since=15m | grep -i error

Restarting clears a wedged connection pool and nothing else. If the alert
returns within ten minutes, stop restarting and escalate — repeated restarts
hide the signal without addressing the cause.

### Escalation

Page the payments lead if customer-visible behaviour is affected for more than
fifteen minutes. Message construction is pure: the same instruction always produces byte-identical output, which is what makes the golden-file tests meaningful.
