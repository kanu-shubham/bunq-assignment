---
title: session-auth
source: readme
space: engineering
team: platform
doc_type: readme
updated: 2026-04-10
tags: [session, service, platform]
---

# session-auth

`session-auth` issues and validates customer session tokens and handles step-up challenges. The primary aggregate is `Session` and state lives in Redis with PostgreSQL for long-lived refresh records.

## Interface

Internal callers reach it over gRPC; the contract lives in the shared proto
repository and every RPC sets a deadline. The most common client-visible failure
is `ERR_SESSION_BINDING_MISMATCH`, which means the request was well formed but the Session was not in a
state that permits the operation. Retrying an `ERR_SESSION_BINDING_MISMATCH` without changing anything
will fail again — it is a state error, not a transient one.

## Operational shape

The service exports `session_validation_failures_total`. The paging threshold is above 200 per minute; the alert is
`SessionValidationFailures` and it links to this service's runbook. Its hard dependency is
device-registry, and a failure there surfaces here as elevated latency before it surfaces
as errors, so latency is the earlier signal.

## Notes

Sessions are bound to a device fingerprint. A token presented from an unrecognised device is rejected rather than challenged, because a silent rebinding is indistinguishable from theft.

## Local development

    make dev-up
    make migrate
    make run

The integration tests use testcontainers against the same database version we
run in production, so a migration that works locally works in staging.
