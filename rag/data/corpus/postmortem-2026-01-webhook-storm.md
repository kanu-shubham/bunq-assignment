---
title: Postmortem - Webhook retry storm
source: confluence
space: engineering
team: platform
doc_type: postmortem
updated: 2026-01-16
tags: [postmortem, webhooks, retry, thundering-herd, sev2]
---

# Postmortem: webhook retry storm (2026-01-12)

**Severity:** SEV2
**Duration:** 1h 25m
**Customer impact:** partner webhook delivery delayed by up to 50 minutes

## Summary

A large partner's endpoint went down for eleven minutes. When it recovered, our
dispatcher delivered a backlog of 1.4 million retries in a burst that took the
partner down again, and the second failure produced an even larger backlog.

## Root cause

Retry backoff was exponential but **not jittered**. Every delivery that failed
in the same window retried at the same instants, so the backlog was
self-synchronising: a classic thundering herd. The circuit breaker existed but
its threshold was per-delivery, not per-endpoint, so it never opened.

## What we changed

1. Full jitter on every retry interval. The published schedule (1s, 2s, 4s, …)
   is now the upper bound of a uniform random draw, not the exact delay.
2. A per-endpoint concurrency cap of 50 in-flight deliveries and a token-bucket
   rate limit derived from the endpoint's observed success throughput.
3. The circuit breaker now tracks failures per `endpoint_id` and opens for 15
   minutes after 20 consecutive failures.
4. A backlog drain is now explicitly rate-shaped: recovering from a large
   backlog is slower on purpose.

## Detection gap

We found out from the partner, not from monitoring. We now alert on
`webhook_pending_deliveries` exceeding 100k for five minutes.
