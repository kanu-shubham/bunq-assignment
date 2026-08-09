---
title: fx-pricing
source: readme
space: engineering
team: payments
doc_type: readme
updated: 2026-04-22
tags: [fx, service, payments]
---

# fx-pricing

`fx-pricing` quotes foreign exchange rates and holds a quote for a fixed window. The primary aggregate is `FxQuote` and state lives in Redis for live quotes, PostgreSQL for the audit trail.

## Interface

Internal callers reach it over gRPC; the contract lives in the shared proto
repository and every RPC sets a deadline. The most common client-visible failure
is `ERR_QUOTE_EXPIRED`, which means the request was well formed but the FxQuote was not in a
state that permits the operation. Retrying an `ERR_QUOTE_EXPIRED` without changing anything
will fail again — it is a state error, not a transient one.

## Operational shape

The service exports `fx_quote_age_seconds`. The paging threshold is above 90 seconds; the alert is
`FxRateFeedStale` and it links to this service's runbook. Its hard dependency is
the rate provider feed, and a failure there surfaces here as elevated latency before it surfaces
as errors, so latency is the earlier signal.

## Notes

Quotes are valid for 60 seconds. An expired quote is never silently refreshed — the caller must request a new one and show the customer the new rate.

## Local development

    make dev-up
    make migrate
    make run

The integration tests use testcontainers against the same database version we
run in production, so a migration that works locally works in staging.
