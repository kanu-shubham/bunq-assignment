---
title: Runbook - tax-reporting
source: confluence
space: engineering
team: ledger
doc_type: runbook
updated: 2026-02-11
tags: [runbook, tax, oncall, ledger]
---

# Runbook: tax-reporting

## Alert: TaxReportFailures

Fires when `tax_report_generation_failures_total` is any non-zero value during a reporting window for five minutes.

### Triage

1. Check the tax-reporting dashboard and confirm whether the signal is service-wide or
   confined to one pod. A single unhealthy pod is a restart; a service-wide
   signal is an investigation.
2. Check ledger-core. Most of this service's bad days start somewhere else, and
   treating a downstream symptom as a local cause wastes the first twenty
   minutes of every incident.
3. Look at the ratio of `ERR_REPORTING_YEAR_LOCKED` in the error breakdown. A spike in that specific
   code points at callers sending requests against a stale view of TaxReport
   state, which is a client problem and is fixed by talking to the caller.

### Mitigation

    kubectl -n ledger rollout restart deploy/tax-reporting
    kubectl -n ledger logs deploy/tax-reporting --since=15m | grep -i error

Restarting clears a wedged connection pool and nothing else. If the alert
returns within ten minutes, stop restarting and escalate — repeated restarts
hide the signal without addressing the cause.

### Escalation

Page the ledger lead if customer-visible behaviour is affected for more than
fifteen minutes. A reporting year locks on 31 January. Corrections after the lock go through the amendment process and are filed separately.
