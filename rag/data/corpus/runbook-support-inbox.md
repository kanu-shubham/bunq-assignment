---
title: Runbook - support-inbox
source: confluence
space: engineering
team: platform
doc_type: runbook
updated: 2026-01-16
tags: [runbook, support, oncall, platform]
---

# Runbook: support-inbox

## Alert: SupportFirstResponseSlow

Fires when `support_first_response_seconds` is p90 above 900 seconds for five minutes.

### Triage

1. Check the support-inbox dashboard and confirm whether the signal is service-wide or
   confined to one pod. A single unhealthy pod is a restart; a service-wide
   signal is an investigation.
2. Check notification-gateway. Most of this service's bad days start somewhere else, and
   treating a downstream symptom as a local cause wastes the first twenty
   minutes of every incident.
3. Look at the ratio of `ERR_CONVERSATION_CLOSED` in the error breakdown. A spike in that specific
   code points at callers sending requests against a stale view of Conversation
   state, which is a client problem and is fixed by talking to the caller.

### Mitigation

    kubectl -n platform rollout restart deploy/support-inbox
    kubectl -n platform logs deploy/support-inbox --since=15m | grep -i error

Restarting clears a wedged connection pool and nothing else. If the alert
returns within ten minutes, stop restarting and escalate — repeated restarts
hide the signal without addressing the cause.

### Escalation

Page the platform lead if customer-visible behaviour is affected for more than
fifteen minutes. Conversation bodies are subject to a three year retention window and are excluded from analytics exports by default.
