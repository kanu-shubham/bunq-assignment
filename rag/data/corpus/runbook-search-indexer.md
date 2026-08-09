---
title: Runbook - search-indexer
source: confluence
space: engineering
team: platform
doc_type: runbook
updated: 2026-05-05
tags: [runbook, search, oncall, platform]
---

# Runbook: search-indexer

## Alert: SearchIndexStale

Fires when `search_index_staleness_seconds` is above 300 seconds for five minutes.

### Triage

1. Check the search-indexer dashboard and confirm whether the signal is service-wide or
   confined to one pod. A single unhealthy pod is a restart; a service-wide
   signal is an investigation.
2. Check the Kafka content topic. Most of this service's bad days start somewhere else, and
   treating a downstream symptom as a local cause wastes the first twenty
   minutes of every incident.
3. Look at the ratio of `ERR_INDEX_VERSION_MISMATCH` in the error breakdown. A spike in that specific
   code points at callers sending requests against a stale view of IndexTask
   state, which is a client problem and is fixed by talking to the caller.

### Mitigation

    kubectl -n platform rollout restart deploy/search-indexer
    kubectl -n platform logs deploy/search-indexer --since=15m | grep -i error

Restarting clears a wedged connection pool and nothing else. If the alert
returns within ten minutes, stop restarting and escalate — repeated restarts
hide the signal without addressing the cause.

### Escalation

Page the platform lead if customer-visible behaviour is affected for more than
fifteen minutes. Index rebuilds write to a new index and flip an alias. A rebuild never mutates the index being served, so a failed rebuild is invisible to users.
