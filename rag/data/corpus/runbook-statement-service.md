---
title: Runbook - statement-service
source: confluence
space: engineering
team: ledger
doc_type: runbook
updated: 2026-01-01
tags: [runbook, statement, oncall, ledger]
---

# Runbook: statement-service

## Alert: StatementRenderBacklog

Fires when `statement_render_duration_seconds` is p99 above 12 seconds for five minutes.

### Triage

1. Check the statement-service dashboard and confirm whether the signal is service-wide or
   confined to one pod. A single unhealthy pod is a restart; a service-wide
   signal is an investigation.
2. Check ledger-core. Most of this service's bad days start somewhere else, and
   treating a downstream symptom as a local cause wastes the first twenty
   minutes of every incident.
3. Look at the ratio of `ERR_STATEMENT_PERIOD_OPEN` in the error breakdown. A spike in that specific
   code points at callers sending requests against a stale view of StatementRun
   state, which is a client problem and is fixed by talking to the caller.

### Mitigation

    kubectl -n ledger rollout restart deploy/statement-service
    kubectl -n ledger logs deploy/statement-service --since=15m | grep -i error

Restarting clears a wedged connection pool and nothing else. If the alert
returns within ten minutes, stop restarting and escalate — repeated restarts
hide the signal without addressing the cause.

### Escalation

Page the ledger lead if customer-visible behaviour is affected for more than
fifteen minutes. Statements for a period are immutable once the period closes; a correction is issued as a supplementary statement rather than a regenerated one.
