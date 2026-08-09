---
title: Runbook - document-store
source: confluence
space: engineering
team: platform
doc_type: runbook
updated: 2026-02-23
tags: [runbook, document, oncall, platform]
---

# Runbook: document-store

## Alert: DocumentScanBacklog

Fires when `document_scan_queue_depth` is above 500 for five minutes.

### Triage

1. Check the document-store dashboard and confirm whether the signal is service-wide or
   confined to one pod. A single unhealthy pod is a restart; a service-wide
   signal is an investigation.
2. Check the malware scanner. Most of this service's bad days start somewhere else, and
   treating a downstream symptom as a local cause wastes the first twenty
   minutes of every incident.
3. Look at the ratio of `ERR_DOCUMENT_SCAN_PENDING` in the error breakdown. A spike in that specific
   code points at callers sending requests against a stale view of StoredDocument
   state, which is a client problem and is fixed by talking to the caller.

### Mitigation

    kubectl -n platform rollout restart deploy/document-store
    kubectl -n platform logs deploy/document-store --since=15m | grep -i error

Restarting clears a wedged connection pool and nothing else. If the alert
returns within ten minutes, stop restarting and escalate — repeated restarts
hide the signal without addressing the cause.

### Escalation

Page the platform lead if customer-visible behaviour is affected for more than
fifteen minutes. Every upload is scanned before it becomes readable. A document in the pending state returns a 409, never a partial or unscanned body.
