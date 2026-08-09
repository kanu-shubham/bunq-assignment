---
title: Postmortem - Log volume filled and evicted pods
source: confluence
space: engineering
team: platform
doc_type: postmortem
updated: 2025-05-05
tags: [postmortem, log-volume-full, sev2]
---

# Postmortem: Log volume filled and evicted pods (2025-05-05)

**Severity:** SEV2
**Duration:** 3h 20m
**Customer impact:** two services unavailable for 40 minutes

## Summary

A debug log statement left enabled after a release wrote 400 GB in six hours and filled the node's disk, which evicted unrelated pods.

## Root cause

Debug logging was enabled behind a flag for an investigation and the flag was never turned back off. Nothing expired it and nothing alerted on log volume.

## What we changed

1. Investigation flags now carry a mandatory expiry and auto-disable.
2. Per-service log volume budget with an alert at 80 percent.
3. Ephemeral storage requests and limits set on every workload so a noisy pod cannot evict a quiet one.

## Lesson

The blast radius was set by a missing resource limit, not by the log statement. Isolation failures turn small mistakes into incidents.
