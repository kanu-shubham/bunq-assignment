---
title: ADR-0007 Event sourcing for the ledger
source: confluence
space: engineering
team: ledger
doc_type: adr
updated: 2024-11-08
tags: [adr, event-sourcing, ledger, cqrs, projection]
---

# ADR-0007: Event sourcing for the ledger

**Status:** Accepted
**Date:** 2024-11-08

## Context

We need an auditable record of every balance change, the ability to answer
"what was this balance at 14:03 last Tuesday", and a regulator-facing story for
how corrections are made. A mutable `balance` column answers none of these.

## Decision

The ledger is an append-only stream of immutable entries. Balances are a
projection derived from that stream and can be rebuilt from scratch at any time.
Corrections are new reversing entries, never edits.

## Consequences

**Good.** Full audit trail for free. Point-in-time balances are a fold over the
stream up to a timestamp. Bugs in projection logic are recoverable by fixing the
code and replaying, which has already saved us twice.

**Bad.** Reads are not trivially "select the balance". We carry the operational
cost of a projector, its lag, and the reconciliation job that detects drift
between stream and projection. Replaying the full stream now takes about 40
minutes and grows linearly; we will need snapshotting before it reaches two
hours.

**Rejected alternative.** A mutable balance with an audit-log side table. It is
simpler to read but the audit log is then a second source of truth that can
silently disagree with the balance, which is the exact failure we are trying to
make impossible.
