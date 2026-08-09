---
title: device-registry
source: readme
space: engineering
team: platform
doc_type: readme
updated: 2026-05-17
tags: [device, service, platform]
---

# device-registry

`device-registry` tracks the devices a customer has registered and their trust state. The primary aggregate is `Device` and state lives in PostgreSQL.

## Interface

Internal callers reach it over gRPC; the contract lives in the shared proto
repository and every RPC sets a deadline. The most common client-visible failure
is `ERR_DEVICE_NOT_TRUSTED`, which means the request was well formed but the Device was not in a
state that permits the operation. Retrying an `ERR_DEVICE_NOT_TRUSTED` without changing anything
will fail again — it is a state error, not a transient one.

## Operational shape

The service exports `device_trust_transitions_total`. The paging threshold is unusual spikes above 3x baseline; the alert is
`DeviceTrustSpike` and it links to this service's runbook. Its hard dependency is
notification-gateway, and a failure there surfaces here as elevated latency before it surfaces
as errors, so latency is the earlier signal.

## Notes

Registering a new device always notifies every other registered device. That notification is transactional and bypasses quiet hours.

## Local development

    make dev-up
    make migrate
    make run

The integration tests use testcontainers against the same database version we
run in production, so a migration that works locally works in staging.
