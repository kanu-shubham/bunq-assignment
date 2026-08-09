---
title: direct-debit-mandates
source: readme
space: engineering
team: payments
doc_type: readme
updated: 2026-03-15
tags: [direct, service, payments]
---

# direct-debit-mandates

`direct-debit-mandates` stores and validates SEPA direct debit mandates. The primary aggregate is `Mandate` and state lives in PostgreSQL.

## Interface

Internal callers reach it over gRPC; the contract lives in the shared proto
repository and every RPC sets a deadline. The most common client-visible failure
is `ERR_MANDATE_EXPIRED`, which means the request was well formed but the Mandate was not in a
state that permits the operation. Retrying an `ERR_MANDATE_EXPIRED` without changing anything
will fail again — it is a state error, not a transient one.

## Operational shape

The service exports `mandate_validation_failures_total`. The paging threshold is above 50 per minute; the alert is
`MandateValidationFailures` and it links to this service's runbook. Its hard dependency is
payments-api, and a failure there surfaces here as elevated latency before it surfaces
as errors, so latency is the earlier signal.

## Notes

A mandate that has not been used for 36 months lapses automatically and cannot be revived; the customer must sign a new one.

## Local development

    make dev-up
    make migrate
    make run

The integration tests use testcontainers against the same database version we
run in production, so a migration that works locally works in staging.
