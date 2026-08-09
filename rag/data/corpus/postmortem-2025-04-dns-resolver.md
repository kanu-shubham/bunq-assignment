---
title: Postmortem - Internal DNS resolver saturation
source: confluence
space: engineering
team: platform
doc_type: postmortem
updated: 2025-04-02
tags: [postmortem, dns-resolver, sev2]
---

# Postmortem: Internal DNS resolver saturation (2025-04-02)

**Severity:** SEV2
**Duration:** 1h 55m
**Customer impact:** intermittent 5xx across six services

## Summary

The cluster DNS resolver hit its query rate limit after a deployment increased per-request lookups tenfold.

## Root cause

A client library was rebuilt without connection reuse, so every outbound call performed a fresh lookup instead of reusing a pooled connection.

## What we changed

1. Restored connection pooling in the shared HTTP client and pinned the setting in a lint rule.
2. Raised the node-local DNS cache TTL from 5 to 30 seconds.
3. Added a per-node DNS query rate panel, which nobody had before.

## Lesson

A library upgrade changed a default that nothing was watching. Defaults are configuration, and unwatched configuration is a future incident.
