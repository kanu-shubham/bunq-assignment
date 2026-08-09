---
title: card-issuing
source: readme
space: engineering
team: payments
doc_type: readme
updated: 2026-02-08
tags: [card, service, payments]
---

# card-issuing

`card-issuing` orders physical and virtual debit cards and tracks their lifecycle. The primary aggregate is `CardOrder` and state lives in PostgreSQL.

## Interface

Internal callers reach it over gRPC; the contract lives in the shared proto
repository and every RPC sets a deadline. The most common client-visible failure
is `ERR_CARD_ALREADY_ACTIVATED`, which means the request was well formed but the CardOrder was not in a
state that permits the operation. Retrying an `ERR_CARD_ALREADY_ACTIVATED` without changing anything
will fail again — it is a state error, not a transient one.

## Operational shape

The service exports `card_activation_latency_seconds`. The paging threshold is p95 above 3 seconds; the alert is
`CardIssuingVendorErrors` and it links to this service's runbook. Its hard dependency is
identity-kyc, and a failure there surfaces here as elevated latency before it surfaces
as errors, so latency is the earlier signal.

## Notes

Card PANs never enter our systems; the issuer processor holds them and we store a token plus the last four digits.

## Local development

    make dev-up
    make migrate
    make run

The integration tests use testcontainers against the same database version we
run in production, so a migration that works locally works in staging.
