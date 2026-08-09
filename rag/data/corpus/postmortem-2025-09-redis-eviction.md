---
title: Postmortem - Redis eviction caused duplicate notifications
source: confluence
space: engineering
team: platform
doc_type: postmortem
updated: 2025-09-30
tags: [postmortem, redis, eviction, notifications, duplicates, memory]
---

# Postmortem: Redis eviction caused duplicate notifications (2025-09-27)

**Severity:** SEV2
**Duration:** 2h 05m
**Customer impact:** roughly 46,000 duplicate push notifications

## Summary

The shared Redis cluster hit its `maxmemory` limit. The eviction policy was
`allkeys-lru`, so it evicted whatever was coldest — which included the
notification-gateway dedupe set. With dedupe keys gone, retried deliveries were
no longer recognised as duplicates and customers received the same push several
times.

## Root cause

A batch job in an unrelated service wrote 12 GB of cached report data into the
same Redis instance with no TTL. That pushed the instance to its memory limit
and triggered eviction of unrelated keys. The failure crossed a service boundary
because the boundary did not exist: one Redis, many tenants, no quotas.

## What we changed

1. Notification dedupe moved to its own Redis instance with `noeviction` and
   alerting on `used_memory_ratio > 0.8`. Failing writes loudly beats evicting
   silently.
2. Every key written to the shared cluster must now have a TTL; a lint rule in
   the shared Redis client rejects `SET` without an expiry.
3. The report job writes to object storage, which is where 12 GB of report data
   belonged in the first place.

## Note on the design trade-off

notification-gateway deliberately fails open when the dedupe store is
unavailable — duplicates are preferable to dropped payment notifications. That
decision stands. The bug was that Redis failed *silently and partially* rather
than being unavailable, so nothing detected the degraded state.
