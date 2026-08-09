---
title: Runbook - device-registry
source: confluence
space: engineering
team: platform
doc_type: runbook
updated: 2026-05-17
tags: [runbook, device, oncall, platform]
---

# Runbook: device-registry

## Alert: DeviceTrustSpike

Fires when `device_trust_transitions_total` is unusual spikes above 3x baseline for five minutes.

### Triage

1. Check the device-registry dashboard and confirm whether the signal is service-wide or
   confined to one pod. A single unhealthy pod is a restart; a service-wide
   signal is an investigation.
2. Check notification-gateway. Most of this service's bad days start somewhere else, and
   treating a downstream symptom as a local cause wastes the first twenty
   minutes of every incident.
3. Look at the ratio of `ERR_DEVICE_NOT_TRUSTED` in the error breakdown. A spike in that specific
   code points at callers sending requests against a stale view of Device
   state, which is a client problem and is fixed by talking to the caller.

### Mitigation

    kubectl -n platform rollout restart deploy/device-registry
    kubectl -n platform logs deploy/device-registry --since=15m | grep -i error

Restarting clears a wedged connection pool and nothing else. If the alert
returns within ten minutes, stop restarting and escalate — repeated restarts
hide the signal without addressing the cause.

### Escalation

Page the platform lead if customer-visible behaviour is affected for more than
fifteen minutes. Registering a new device always notifies every other registered device. That notification is transactional and bypasses quiet hours.
