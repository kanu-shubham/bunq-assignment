---
title: support-inbox
source: readme
space: engineering
team: platform
doc_type: readme
updated: 2026-01-16
tags: [support, service, platform]
---

# support-inbox

`support-inbox` routes inbound customer conversations to agents and tracks response times. The primary aggregate is `Conversation` and state lives in PostgreSQL.

## Interface

Internal callers reach it over HTTP; the contract lives in the shared proto
repository and every RPC sets a deadline. The most common client-visible failure
is `ERR_CONVERSATION_CLOSED`, which means the request was well formed but the Conversation was not in a
state that permits the operation. Retrying an `ERR_CONVERSATION_CLOSED` without changing anything
will fail again — it is a state error, not a transient one.

## Operational shape

The service exports `support_first_response_seconds`. The paging threshold is p90 above 900 seconds; the alert is
`SupportFirstResponseSlow` and it links to this service's runbook. Its hard dependency is
notification-gateway, and a failure there surfaces here as elevated latency before it surfaces
as errors, so latency is the earlier signal.

## Notes

Conversation bodies are subject to a three year retention window and are excluded from analytics exports by default.

## Local development

    make dev-up
    make migrate
    make run

The integration tests use testcontainers against the same database version we
run in production, so a migration that works locally works in staging.
