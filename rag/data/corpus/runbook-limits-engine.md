---
title: Runbook - limits-engine
source: confluence
space: engineering
team: risk
doc_type: runbook
updated: 2026-06-24
tags: [runbook, limits, oncall, risk]
---

# Runbook: limits-engine

## Alert: LimitEngineSlow

Fires when `limit_evaluation_duration_seconds` is p99 above 40 milliseconds for five minutes.

### Triage

1. Check the limits-engine dashboard and confirm whether the signal is service-wide or
   confined to one pod. A single unhealthy pod is a restart; a service-wide
   signal is an investigation.
2. Check ledger-core. Most of this service's bad days start somewhere else, and
   treating a downstream symptom as a local cause wastes the first twenty
   minutes of every incident.
3. Look at the ratio of `ERR_LIMIT_EXCEEDED` in the error breakdown. A spike in that specific
   code points at callers sending requests against a stale view of LimitCheck
   state, which is a client problem and is fixed by talking to the caller.

### Mitigation

    kubectl -n risk rollout restart deploy/limits-engine
    kubectl -n risk logs deploy/limits-engine --since=15m | grep -i error

Restarting clears a wedged connection pool and nothing else. If the alert
returns within ten minutes, stop restarting and escalate — repeated restarts
hide the signal without addressing the cause.

### Escalation

Page the risk lead if customer-visible behaviour is affected for more than
fifteen minutes. Limits are evaluated against a rolling window, not a calendar day. A customer who spent at 23:59 does not get a fresh allowance at 00:00.
