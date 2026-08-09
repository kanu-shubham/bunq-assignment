---
title: Runbook - session-auth
source: confluence
space: engineering
team: platform
doc_type: runbook
updated: 2026-04-10
tags: [runbook, session, oncall, platform]
---

# Runbook: session-auth

## Alert: SessionValidationFailures

Fires when `session_validation_failures_total` is above 200 per minute for five minutes.

### Triage

1. Check the session-auth dashboard and confirm whether the signal is service-wide or
   confined to one pod. A single unhealthy pod is a restart; a service-wide
   signal is an investigation.
2. Check device-registry. Most of this service's bad days start somewhere else, and
   treating a downstream symptom as a local cause wastes the first twenty
   minutes of every incident.
3. Look at the ratio of `ERR_SESSION_BINDING_MISMATCH` in the error breakdown. A spike in that specific
   code points at callers sending requests against a stale view of Session
   state, which is a client problem and is fixed by talking to the caller.

### Mitigation

    kubectl -n platform rollout restart deploy/session-auth
    kubectl -n platform logs deploy/session-auth --since=15m | grep -i error

Restarting clears a wedged connection pool and nothing else. If the alert
returns within ten minutes, stop restarting and escalate — repeated restarts
hide the signal without addressing the cause.

### Escalation

Page the platform lead if customer-visible behaviour is affected for more than
fifteen minutes. Sessions are bound to a device fingerprint. A token presented from an unrecognised device is rejected rather than challenged, because a silent rebinding is indistinguishable from theft.
