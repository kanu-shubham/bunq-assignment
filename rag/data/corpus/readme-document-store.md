---
title: document-store
source: readme
space: engineering
team: platform
doc_type: readme
updated: 2026-02-23
tags: [document, service, platform]
---

# document-store

`document-store` stores customer-uploaded documents with server-side encryption and access auditing. The primary aggregate is `StoredDocument` and state lives in object storage with metadata in PostgreSQL.

## Interface

Internal callers reach it over gRPC; the contract lives in the shared proto
repository and every RPC sets a deadline. The most common client-visible failure
is `ERR_DOCUMENT_SCAN_PENDING`, which means the request was well formed but the StoredDocument was not in a
state that permits the operation. Retrying an `ERR_DOCUMENT_SCAN_PENDING` without changing anything
will fail again — it is a state error, not a transient one.

## Operational shape

The service exports `document_scan_queue_depth`. The paging threshold is above 500; the alert is
`DocumentScanBacklog` and it links to this service's runbook. Its hard dependency is
the malware scanner, and a failure there surfaces here as elevated latency before it surfaces
as errors, so latency is the earlier signal.

## Notes

Every upload is scanned before it becomes readable. A document in the pending state returns a 409, never a partial or unscanned body.

## Local development

    make dev-up
    make migrate
    make run

The integration tests use testcontainers against the same database version we
run in production, so a migration that works locally works in staging.
