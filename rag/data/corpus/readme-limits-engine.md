---
title: limits-engine
source: readme
space: engineering
team: risk
doc_type: readme
updated: 2026-06-24
tags: [limits, service, risk]
---

# limits-engine

`limits-engine` evaluates spending and transfer limits before a payment is accepted. The primary aggregate is `LimitCheck` and state lives in Redis counters with PostgreSQL definitions.

## Interface

Internal callers reach it over gRPC; the contract lives in the shared proto
repository and every RPC sets a deadline. The most common client-visible failure
is `ERR_LIMIT_EXCEEDED`, which means the request was well formed but the LimitCheck was not in a
state that permits the operation. Retrying an `ERR_LIMIT_EXCEEDED` without changing anything
will fail again — it is a state error, not a transient one.

## Operational shape

The service exports `limit_evaluation_duration_seconds`. The paging threshold is p99 above 40 milliseconds; the alert is
`LimitEngineSlow` and it links to this service's runbook. Its hard dependency is
ledger-core, and a failure there surfaces here as elevated latency before it surfaces
as errors, so latency is the earlier signal.

## Notes

Limits are evaluated against a rolling window, not a calendar day. A customer who spent at 23:59 does not get a fresh allowance at 00:00.

## Local development

    make dev-up
    make migrate
    make run

The integration tests use testcontainers against the same database version we
run in production, so a migration that works locally works in staging.
