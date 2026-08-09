---
title: Runbook - chat-assistant
source: confluence
space: engineering
team: platform
doc_type: runbook
updated: 2026-02-26
tags: [runbook, chat, oncall, platform]
---

# Runbook: chat-assistant

## Alert: AssistantHandoffRatioHigh

Fires when `assistant_handoff_ratio` is above 0.4 for five minutes.

### Triage

1. Check the chat-assistant dashboard and confirm whether the signal is service-wide or
   confined to one pod. A single unhealthy pod is a restart; a service-wide
   signal is an investigation.
2. Check search-indexer. Most of this service's bad days start somewhere else, and
   treating a downstream symptom as a local cause wastes the first twenty
   minutes of every incident.
3. Look at the ratio of `ERR_ASSISTANT_NO_GROUNDING` in the error breakdown. A spike in that specific
   code points at callers sending requests against a stale view of AssistantTurn
   state, which is a client problem and is fixed by talking to the caller.

### Mitigation

    kubectl -n platform rollout restart deploy/chat-assistant
    kubectl -n platform logs deploy/chat-assistant --since=15m | grep -i error

Restarting clears a wedged connection pool and nothing else. If the alert
returns within ten minutes, stop restarting and escalate — repeated restarts
hide the signal without addressing the cause.

### Escalation

Page the platform lead if customer-visible behaviour is affected for more than
fifteen minutes. The assistant answers only from retrieved help centre content and hands off when nothing relevant is found. It never answers from the base model's own knowledge.
