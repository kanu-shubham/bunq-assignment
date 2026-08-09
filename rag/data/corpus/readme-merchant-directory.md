---
title: merchant-directory
source: readme
space: engineering
team: platform
doc_type: readme
updated: 2026-06-09
tags: [merchant, service, platform]
---

# merchant-directory

`merchant-directory` enriches raw transaction counterparties with merchant names, logos, and categories. The primary aggregate is `MerchantRecord` and state lives in PostgreSQL with an OpenSearch index.

## Interface

Internal callers reach it over gRPC; the contract lives in the shared proto
repository and every RPC sets a deadline. The most common client-visible failure
is `ERR_MERCHANT_NOT_FOUND`, which means the request was well formed but the MerchantRecord was not in a
state that permits the operation. Retrying an `ERR_MERCHANT_NOT_FOUND` without changing anything
will fail again — it is a state error, not a transient one.

## Operational shape

The service exports `merchant_enrichment_hit_ratio`. The paging threshold is below 0.85; the alert is
`MerchantEnrichmentHitRatioLow` and it links to this service's runbook. Its hard dependency is
the enrichment vendor feed, and a failure there surfaces here as elevated latency before it surfaces
as errors, so latency is the earlier signal.

## Notes

Unmatched counterparties fall back to the raw scheme descriptor. Showing a raw descriptor is ugly; showing the wrong merchant is a support ticket.

## Local development

    make dev-up
    make migrate
    make run

The integration tests use testcontainers against the same database version we
run in production, so a migration that works locally works in staging.
