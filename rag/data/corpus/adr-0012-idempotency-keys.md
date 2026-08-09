---
title: ADR-0012 Client-supplied idempotency keys
source: confluence
space: engineering
team: payments
doc_type: adr
updated: 2025-01-21
tags: [adr, idempotency, api, retries, safety]
---

# ADR-0012: Client-supplied idempotency keys

**Status:** Accepted
**Date:** 2025-01-21

## Context

Payment creation is not naturally idempotent, and clients retry — on timeouts,
on connection resets, on user double-taps. Without a deduplication mechanism a
retry means a second payment, which means real money moved twice.

## Decision

Every mutating endpoint requires an `Idempotency-Key` header supplied by the
client. We store `(client_id, idempotency_key) -> (request_body_sha256, response_status, response_body)`
for 25 hours.

* Same key, same body hash → return the stored response, plus `Idempotent-Replay: true`.
* Same key, different body hash → HTTP 409 `ERR_IDEMPOTENCY_KEY_REUSE`.
* Key present, request still in flight → HTTP 409 `ERR_REQUEST_IN_PROGRESS`; the
  client should retry after a short delay.

The 25 hour window is deliberately longer than the longest client retry budget
we have seen (24 hours) plus an hour of slack.

## Consequences

Clients must generate keys, and bad clients will reuse them. We chose to fail
loudly on body mismatch rather than silently serve the old response, because a
silent wrong answer in payments is worse than an error the integrator has to fix.

Server-derived idempotency (hashing the request body alone) was rejected: two
genuinely distinct payments of the same amount to the same beneficiary in the
same second are legitimate, and we must not collapse them.
