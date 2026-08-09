---
title: Postmortem - Load balancer health check flapping
source: confluence
space: engineering
team: platform
doc_type: postmortem
updated: 2025-11-13
tags: [postmortem, healthcheck-flap, sev2]
---

# Postmortem: Load balancer health check flapping (2025-11-13)

**Severity:** SEV2
**Duration:** 1h 10m
**Customer impact:** roughly 4 percent of requests failed during the window

## Summary

A health check endpoint began timing out under load, so instances were pulled from the pool, which increased load on the rest and pulled them out too.

## Root cause

The health check shared a thread pool with request handling and did a database round trip. Under load it queued behind real work.

## What we changed

1. Health checks now run on a dedicated pool and answer from cached state.
2. Liveness and readiness separated; liveness no longer touches dependencies.
3. Raised the unhealthy threshold from 2 to 4 consecutive failures.

## Lesson

A health check that depends on the thing it is protecting turns a degradation into an outage. It should answer one question: can this process serve traffic.
