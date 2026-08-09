---
title: Runbook - metrics-collector
source: confluence
space: engineering
team: platform
doc_type: runbook
updated: 2026-01-19
tags: [runbook, metrics, oncall, platform]
---

# Runbook: metrics-collector

## Alert: MetricSeriesDropped

Fires when `metric_series_dropped_total` is any sustained non-zero value for five minutes.

### Triage

1. Check the metrics-collector dashboard and confirm whether the signal is service-wide or
   confined to one pod. A single unhealthy pod is a restart; a service-wide
   signal is an investigation.
2. Check the metrics backend. Most of this service's bad days start somewhere else, and
   treating a downstream symptom as a local cause wastes the first twenty
   minutes of every incident.
3. Look at the ratio of `ERR_METRIC_CARDINALITY_EXCEEDED` in the error breakdown. A spike in that specific
   code points at callers sending requests against a stale view of MetricBatch
   state, which is a client problem and is fixed by talking to the caller.

### Mitigation

    kubectl -n platform rollout restart deploy/metrics-collector
    kubectl -n platform logs deploy/metrics-collector --since=15m | grep -i error

Restarting clears a wedged connection pool and nothing else. If the alert
returns within ten minutes, stop restarting and escalate — repeated restarts
hide the signal without addressing the cause.

### Escalation

Page the platform lead if customer-visible behaviour is affected for more than
fifteen minutes. Per-tenant cardinality is capped. A service that starts emitting a customer id as a label gets its new series dropped rather than taking the backend down for everyone.
