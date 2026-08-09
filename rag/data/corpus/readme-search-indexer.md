---
title: search-indexer
source: readme
space: engineering
team: platform
doc_type: readme
updated: 2026-05-05
tags: [search, service, platform]
---

# search-indexer

`search-indexer` keeps the OpenSearch indexes for help centre content and merchant search up to date. The primary aggregate is `IndexTask` and state lives in OpenSearch with checkpoints in PostgreSQL.

## Interface

Internal callers reach it over HTTP; the contract lives in the shared proto
repository and every RPC sets a deadline. The most common client-visible failure
is `ERR_INDEX_VERSION_MISMATCH`, which means the request was well formed but the IndexTask was not in a
state that permits the operation. Retrying an `ERR_INDEX_VERSION_MISMATCH` without changing anything
will fail again — it is a state error, not a transient one.

## Operational shape

The service exports `search_index_staleness_seconds`. The paging threshold is above 300 seconds; the alert is
`SearchIndexStale` and it links to this service's runbook. Its hard dependency is
the Kafka content topic, and a failure there surfaces here as elevated latency before it surfaces
as errors, so latency is the earlier signal.

## Notes

Index rebuilds write to a new index and flip an alias. A rebuild never mutates the index being served, so a failed rebuild is invisible to users.

## Local development

    make dev-up
    make migrate
    make run

The integration tests use testcontainers against the same database version we
run in production, so a migration that works locally works in staging.
