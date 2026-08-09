---
title: statement-service
source: readme
space: engineering
team: ledger
doc_type: readme
updated: 2026-01-01
tags: [statement, service, ledger]
---

# statement-service

`statement-service` renders monthly and on-demand account statements as PDF and CSV. The primary aggregate is `StatementRun` and state lives in PostgreSQL with generated files in object storage.

## Interface

Internal callers reach it over gRPC; the contract lives in the shared proto
repository and every RPC sets a deadline. The most common client-visible failure
is `ERR_STATEMENT_PERIOD_OPEN`, which means the request was well formed but the StatementRun was not in a
state that permits the operation. Retrying an `ERR_STATEMENT_PERIOD_OPEN` without changing anything
will fail again — it is a state error, not a transient one.

## Operational shape

The service exports `statement_render_duration_seconds`. The paging threshold is p99 above 12 seconds; the alert is
`StatementRenderBacklog` and it links to this service's runbook. Its hard dependency is
ledger-core, and a failure there surfaces here as elevated latency before it surfaces
as errors, so latency is the earlier signal.

## Notes

Statements for a period are immutable once the period closes; a correction is issued as a supplementary statement rather than a regenerated one.

## Local development

    make dev-up
    make migrate
    make run

The integration tests use testcontainers against the same database version we
run in production, so a migration that works locally works in staging.
