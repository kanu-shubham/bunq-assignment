---
title: audit-trail
source: readme
space: engineering
team: platform
doc_type: readme
updated: 2026-03-03
tags: [audit, service, platform]
---

# audit-trail

`audit-trail` collects immutable audit events from every service and serves them to compliance tooling. The primary aggregate is `AuditEvent` and state lives in append-only PostgreSQL partitioned by month.

## Interface

Internal callers reach it over gRPC; the contract lives in the shared proto
repository and every RPC sets a deadline. The most common client-visible failure
is `ERR_AUDIT_WINDOW_CLOSED`, which means the request was well formed but the AuditEvent was not in a
state that permits the operation. Retrying an `ERR_AUDIT_WINDOW_CLOSED` without changing anything
will fail again — it is a state error, not a transient one.

## Operational shape

The service exports `audit_ingest_lag_seconds`. The paging threshold is above 120 seconds; the alert is
`AuditIngestLag` and it links to this service's runbook. Its hard dependency is
the Kafka audit topic, and a failure there surfaces here as elevated latency before it surfaces
as errors, so latency is the earlier signal.

## Notes

Audit events are never deleted or edited. Partitions older than seven years are detached and archived, not dropped.

## Local development

    make dev-up
    make migrate
    make run

The integration tests use testcontainers against the same database version we
run in production, so a migration that works locally works in staging.
