---
title: Postmortem - Autoscaler oscillation under bursty load
source: confluence
space: engineering
team: platform
doc_type: postmortem
updated: 2026-03-14
tags: [postmortem, autoscaler-oscillation, sev3]
---

# Postmortem: Autoscaler oscillation under bursty load (2026-03-14)

**Severity:** SEV3
**Duration:** 6h intermittent
**Customer impact:** elevated p99 latency during scale transitions

## Summary

The autoscaler scaled up and back down every few minutes, and each scale-down dropped warm connections and cold-started the replacements.

## Root cause

The scale-down stabilisation window was shorter than the pod's warm-up time, so capacity was removed before the new capacity was useful.

## What we changed

1. Scale-down stabilisation window raised to ten minutes.
2. Readiness gated on a warmed connection pool rather than process start.
3. Scaling events added to the deploy timeline so they show up next to latency.

## Lesson

Two independently sensible timeouts interacted badly. Timeouts are a system property, not a per-component setting.
