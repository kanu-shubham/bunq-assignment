---
title: config-service
source: readme
space: engineering
team: platform
doc_type: readme
updated: 2026-06-12
tags: [config, service, platform]
---

# config-service

`config-service` serves versioned application configuration with schema validation. The primary aggregate is `ConfigVersion` and state lives in PostgreSQL.

## Interface

Internal callers reach it over gRPC; the contract lives in the shared proto
repository and every RPC sets a deadline. The most common client-visible failure
is `ERR_CONFIG_SCHEMA_INVALID`, which means the request was well formed but the ConfigVersion was not in a
state that permits the operation. Retrying an `ERR_CONFIG_SCHEMA_INVALID` without changing anything
will fail again — it is a state error, not a transient one.

## Operational shape

The service exports `config_fetch_failures_total`. The paging threshold is above 5 per minute; the alert is
`ConfigFetchFailures` and it links to this service's runbook. Its hard dependency is
PostgreSQL, and a failure there surfaces here as elevated latency before it surfaces
as errors, so latency is the earlier signal.

## Notes

Clients cache the last good configuration on disk and start from it if the service is unreachable. A config service outage must never prevent a pod from starting.

## Local development

    make dev-up
    make migrate
    make run

The integration tests use testcontainers against the same database version we
run in production, so a migration that works locally works in staging.
