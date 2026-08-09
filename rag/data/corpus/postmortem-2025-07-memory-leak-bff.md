---
title: Postmortem - Memory leak in mobile-bff
source: confluence
space: engineering
team: platform
doc_type: postmortem
updated: 2025-07-20
tags: [postmortem, memory-leak-bff, sev3]
---

# Postmortem: Memory leak in mobile-bff (2025-07-20)

**Severity:** SEV3
**Duration:** rolling over four days
**Customer impact:** periodic latency spikes during pod restarts

## Summary

Per-request dataloader instances were being retained by a module-level registry, so heap grew until the pod was killed and restarted.

## Root cause

A caching decorator keyed on the request object and held a strong reference to it. Requests were never collected.

## What we changed

1. Switched the registry to weak references and added a heap growth alert.
2. Added a soak test that runs 200k requests and asserts flat heap.
3. Documented the dataloader lifetime in the service README.

## Lesson

It was detected by customers noticing slow screens, not by us. A crash loop that restarts fast enough looks healthy on a dashboard built around availability.
