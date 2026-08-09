---
title: metrics-collector
source: readme
space: engineering
team: platform
doc_type: readme
updated: 2026-01-19
tags: [metrics, service, platform]
---

# metrics-collector

`metrics-collector` receives custom application metrics and forwards them to the metrics backend. The primary aggregate is `MetricBatch` and state lives in in-memory buffers with disk spill.

## Interface

Internal callers reach it over HTTP; the contract lives in the shared proto
repository and every RPC sets a deadline. The most common client-visible failure
is `ERR_METRIC_CARDINALITY_EXCEEDED`, which means the request was well formed but the MetricBatch was not in a
state that permits the operation. Retrying an `ERR_METRIC_CARDINALITY_EXCEEDED` without changing anything
will fail again — it is a state error, not a transient one.

## Operational shape

The service exports `metric_series_dropped_total`. The paging threshold is any sustained non-zero value; the alert is
`MetricSeriesDropped` and it links to this service's runbook. Its hard dependency is
the metrics backend, and a failure there surfaces here as elevated latency before it surfaces
as errors, so latency is the earlier signal.

## Notes

Per-tenant cardinality is capped. A service that starts emitting a customer id as a label gets its new series dropped rather than taking the backend down for everyone.

## Local development

    make dev-up
    make migrate
    make run

The integration tests use testcontainers against the same database version we
run in production, so a migration that works locally works in staging.
