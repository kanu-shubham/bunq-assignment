---
title: ADR-0015 PostgreSQL over a document store
source: confluence
space: engineering
team: platform
doc_type: adr
updated: 2025-02-14
tags: [adr, postgres, database, transactions, mongodb]
---

# ADR-0015: PostgreSQL over a document store

**Status:** Accepted
**Date:** 2025-02-14

## Context

Several services were about to pick their own datastore. Left alone we would
have ended up operating four database technologies with one platform team.

## Decision

PostgreSQL is the default datastore. A service wanting something else must write
an ADR explaining why Postgres cannot do the job, and get platform sign-off.

## Rationale

* We need multi-row transactions in almost every service. Money is relational.
* `jsonb` covers the genuinely schemaless cases without a second technology.
* Logical replication, `pg_stat_statements`, and a decade of accumulated
  operational knowledge in the team are worth more than any single feature a
  document store offers.
* One technology means one backup strategy, one failover runbook, one set of
  upgrade rehearsals.

## Consequences

Some workloads are a poor fit and we accept the friction: very high-cardinality
time series live in the metrics stack instead, and full-text search over the
help centre uses OpenSearch. Both were approved through this process.

Horizontal write scaling is a known future problem. The plan is to reach for
partitioning first and sharding by `customer_id` only when a single primary is
genuinely the bottleneck, which it is not today at roughly 12% peak utilisation.
