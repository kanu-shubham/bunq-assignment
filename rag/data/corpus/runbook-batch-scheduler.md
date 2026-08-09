---
title: Runbook - batch-scheduler
source: confluence
space: engineering
team: platform
doc_type: runbook
updated: 2026-04-25
tags: [runbook, batch, oncall, platform]
---

# Runbook: batch-scheduler

## Alert: ScheduledJobOverdue

Fires when `job_run_overdue_seconds` is above 600 seconds for five minutes.

### Triage

1. Check the batch-scheduler dashboard and confirm whether the signal is service-wide or
   confined to one pod. A single unhealthy pod is a restart; a service-wide
   signal is an investigation.
2. Check PostgreSQL. Most of this service's bad days start somewhere else, and
   treating a downstream symptom as a local cause wastes the first twenty
   minutes of every incident.
3. Look at the ratio of `ERR_JOB_ALREADY_RUNNING` in the error breakdown. A spike in that specific
   code points at callers sending requests against a stale view of JobRun
   state, which is a client problem and is fixed by talking to the caller.

### Mitigation

    kubectl -n platform rollout restart deploy/batch-scheduler
    kubectl -n platform logs deploy/batch-scheduler --since=15m | grep -i error

Restarting clears a wedged connection pool and nothing else. If the alert
returns within ten minutes, stop restarting and escalate — repeated restarts
hide the signal without addressing the cause.

### Escalation

Page the platform lead if customer-visible behaviour is affected for more than
fifteen minutes. A job that overruns its next scheduled start is skipped, not queued. Queueing an already-late job is how a slow job becomes a stampede.
