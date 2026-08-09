---
title: ADR-0023 Transactional outbox for event publishing
source: confluence
space: engineering
team: platform
doc_type: adr
updated: 2025-08-19
tags: [adr, outbox, kafka, consistency, dual-write]
---

# ADR-0023: Transactional outbox for event publishing

**Status:** Accepted
**Date:** 2025-08-19

## Context

Services were writing to their database and then publishing to Kafka. That is a
dual write: a crash between the two leaves the database and the event stream
disagreeing, and no amount of retrying fixes the case where the database commit
succeeded and the process died before publishing.

## Decision

Events are written to an `outbox` table in the same transaction as the state
change. A relay process reads the outbox and publishes to Kafka, marking rows
published as it goes.

    BEGIN;
      UPDATE payment SET status = 'SUBMITTED' WHERE id = $1;
      INSERT INTO outbox (aggregate_id, topic, payload) VALUES ($1, 'payments.v1', $2);
    COMMIT;

## Consequences

Delivery becomes at-least-once, so every consumer must be idempotent — this is
now a review requirement, not a suggestion. Publish latency gains the relay's
poll interval, currently 200ms, which is acceptable everywhere we have applied
it.

The relay is a new component to operate. It is deliberately dumb: read, publish,
mark, repeat. Its only interesting behaviour is that it publishes strictly in
`id` order per aggregate so that per-aggregate ordering survives.

**Rejected alternative.** Change data capture with Debezium. It removes the
relay but couples event schemas to table schemas, and a routine column rename
becomes a breaking change for every consumer.
