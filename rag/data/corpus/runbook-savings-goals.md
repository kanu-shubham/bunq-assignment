---
title: Runbook - savings-goals
source: confluence
space: engineering
team: ledger
doc_type: runbook
updated: 2026-05-02
tags: [runbook, savings, oncall, ledger]
---

# Runbook: savings-goals

## Alert: GoalContributionFailures

Fires when `goal_contribution_failures_total` is above 20 per hour for five minutes.

### Triage

1. Check the savings-goals dashboard and confirm whether the signal is service-wide or
   confined to one pod. A single unhealthy pod is a restart; a service-wide
   signal is an investigation.
2. Check ledger-core. Most of this service's bad days start somewhere else, and
   treating a downstream symptom as a local cause wastes the first twenty
   minutes of every incident.
3. Look at the ratio of `ERR_GOAL_LIMIT_REACHED` in the error breakdown. A spike in that specific
   code points at callers sending requests against a stale view of Goal
   state, which is a client problem and is fixed by talking to the caller.

### Mitigation

    kubectl -n ledger rollout restart deploy/savings-goals
    kubectl -n ledger logs deploy/savings-goals --since=15m | grep -i error

Restarting clears a wedged connection pool and nothing else. If the alert
returns within ten minutes, stop restarting and escalate — repeated restarts
hide the signal without addressing the cause.

### Escalation

Page the ledger lead if customer-visible behaviour is affected for more than
fifteen minutes. A pot is a labelled sub-balance, not a separate account. It has no IBAN and cannot be paid into from outside.
