---
title: ledger-core
source: readme
space: engineering
team: ledger
doc_type: readme
updated: 2026-05-02
tags: [ledger, accounting, postgres, event-sourcing]
---

# ledger-core

`ledger-core` is the double-entry bookkeeping engine behind every balance the
customer sees. Every movement of money is recorded as a `LedgerEntry` pair: one
debit and one credit that must sum to zero within a single `Transfer`
aggregate. The invariant is enforced in the database with a deferred constraint
named `chk_transfer_balances_to_zero`; if it ever fires, the service refuses the
write rather than persisting a torn transfer.

## Storage model

Entries live in PostgreSQL 16 in an append-only table `ledger_entry`. We never
issue `UPDATE` or `DELETE` against it. Corrections are made by posting a
reversing transfer that references the original via `reverses_transfer_id`.
Balances are materialised into `account_balance` by a projector that consumes
the entry stream; see ADR-0007 for why we settled on event sourcing here.

## Running locally

    make dev-up          # starts postgres + the projector
    make migrate
    go run ./cmd/ledgerd  # listens on :8081, gRPC on :9081

The projector lag is exposed as `ledger_projector_lag_seconds`. Anything above
30 seconds means customer balances are stale and the on-call engineer should
follow the ledger reconciliation runbook.

## Known sharp edges

Posting a transfer whose currency differs from the account currency is rejected
with `ERR_CURRENCY_MISMATCH`. There is no implicit FX conversion in the ledger;
conversion happens upstream in payments-api and arrives as two separate
transfers linked by an `fx_quote_id`.
