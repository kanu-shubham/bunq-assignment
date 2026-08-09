---
title: tax-reporting
source: readme
space: engineering
team: ledger
doc_type: readme
updated: 2026-02-11
tags: [tax, service, ledger]
---

# tax-reporting

`tax-reporting` produces annual interest and capital statements for tax authorities. The primary aggregate is `TaxReport` and state lives in PostgreSQL with signed exports in object storage.

## Interface

Internal callers reach it over gRPC; the contract lives in the shared proto
repository and every RPC sets a deadline. The most common client-visible failure
is `ERR_REPORTING_YEAR_LOCKED`, which means the request was well formed but the TaxReport was not in a
state that permits the operation. Retrying an `ERR_REPORTING_YEAR_LOCKED` without changing anything
will fail again — it is a state error, not a transient one.

## Operational shape

The service exports `tax_report_generation_failures_total`. The paging threshold is any non-zero value during a reporting window; the alert is
`TaxReportFailures` and it links to this service's runbook. Its hard dependency is
ledger-core, and a failure there surfaces here as elevated latency before it surfaces
as errors, so latency is the earlier signal.

## Notes

A reporting year locks on 31 January. Corrections after the lock go through the amendment process and are filed separately.

## Local development

    make dev-up
    make migrate
    make run

The integration tests use testcontainers against the same database version we
run in production, so a migration that works locally works in staging.
