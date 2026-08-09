---
title: Postmortem - Scheduled jobs ran twice at the DST boundary
source: confluence
space: engineering
team: platform
doc_type: postmortem
updated: 2026-02-24
tags: [postmortem, scheduler-timezone, sev3]
---

# Postmortem: Scheduled jobs ran twice at the DST boundary (2026-02-24)

**Severity:** SEV3
**Duration:** one hour, once
**Customer impact:** duplicate statement generation for 900 accounts

## Summary

Jobs scheduled by local wall-clock time ran twice during the autumn clock change, when 02:30 occurred twice.

## Root cause

The scheduler matched wall-clock time in a local timezone with no idempotency on the job run itself.

## What we changed

1. Job runs are keyed by (job, logical execution time) and a duplicate is a no-op.
2. Schedules in the ambiguous 01:00-03:00 local window are rejected at definition time.
3. The generated statements were deduplicated and no customer was contacted twice.

## Lesson

Wall-clock scheduling has two edge cases a year and both are known in advance. Making the run idempotent is cheaper than reasoning about them.
