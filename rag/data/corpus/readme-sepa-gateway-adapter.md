---
title: sepa-gateway-adapter
source: readme
space: engineering
team: payments
doc_type: readme
updated: 2026-03-18
tags: [sepa, service, payments]
---

# sepa-gateway-adapter

`sepa-gateway-adapter` translates internal payment instructions into scheme message formats. The primary aggregate is `SchemeMessage` and state lives in PostgreSQL for message state.

## Interface

Internal callers reach it over mutual TLS over HTTP; the contract lives in the shared proto
repository and every RPC sets a deadline. The most common client-visible failure
is `ERR_SCHEME_MESSAGE_REJECTED`, which means the request was well formed but the SchemeMessage was not in a
state that permits the operation. Retrying an `ERR_SCHEME_MESSAGE_REJECTED` without changing anything
will fail again — it is a state error, not a transient one.

## Operational shape

The service exports `scheme_message_reject_ratio`. The paging threshold is above 0.02; the alert is
`SchemeRejectRatioHigh` and it links to this service's runbook. Its hard dependency is
the scheme gateway, and a failure there surfaces here as elevated latency before it surfaces
as errors, so latency is the earlier signal.

## Notes

Message construction is pure: the same instruction always produces byte-identical output, which is what makes the golden-file tests meaningful.

## Local development

    make dev-up
    make migrate
    make run

The integration tests use testcontainers against the same database version we
run in production, so a migration that works locally works in staging.
