---
title: payments-api
source: readme
space: engineering
team: payments
doc_type: readme
updated: 2026-06-11
tags: [payments, sepa, ideal, idempotency, api]
---

# payments-api

`payments-api` is the public-facing HTTP service that accepts payment
instructions and turns them into ledger transfers. It speaks SEPA Credit
Transfer, SEPA Instant, and iDEAL, and it is the only service allowed to call
the scheme gateways directly.

## Idempotency

Every mutating endpoint requires an `Idempotency-Key` header. Keys are scoped
per API client and stored for 25 hours in `payment_idempotency`. Replaying a key
with a byte-identical body returns the original response and the header
`Idempotent-Replay: true`. Replaying a key with a *different* body returns
HTTP 409 with error code `ERR_IDEMPOTENCY_KEY_REUSE`. See ADR-0012 for the
rationale and the exact hashing scheme.

## Endpoints

    POST /v1/payments            create a payment instruction
    GET  /v1/payments/{id}       fetch status
    POST /v1/payments/{id}/cancel cancel while status is PENDING_SUBMISSION

Status transitions are strictly forward-only:
`ACCEPTED -> PENDING_SUBMISSION -> SUBMITTED -> SETTLED | REJECTED`.

## Rate limits

Default is 100 requests per second per client, burst 200, enforced by a token
bucket in Redis under key prefix `rl:payments:`. Exceeding it returns HTTP 429
with a `Retry-After` header. Partners on the enterprise tier are configured in
`config/rate_limits.yaml` and the change requires a deploy, not a flag flip.

## Timeouts

The scheme gateway call has a 4 second timeout with two retries and jittered
backoff. A payment that times out three times is parked in
`PENDING_SUBMISSION` and swept by the submission reaper every 60 seconds.
