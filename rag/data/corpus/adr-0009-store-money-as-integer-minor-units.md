---
title: ADR-0009 Store money as integer minor units
source: confluence
space: engineering
team: ledger
doc_type: adr
updated: 2025-01-01
tags: [adr, store, ledger]
---

# ADR-0009: Store money as integer minor units

**Status:** Accepted

## Context

Amounts were being passed around as floating point in two services, and a rounding difference of one cent had already reached a customer statement.

## Decision

All monetary amounts are integers in the currency's minor unit, paired with an ISO-4217 currency code. Floating point never touches an amount.

## Consequences

**Good.** Arithmetic is exact. Serialisation is unambiguous. The type system stops an amount being added to a bare number.

**Bad.** Every currency's exponent must be known — not all currencies have two decimal places, and three-decimal currencies broke our first implementation.

**Rejected alternative.** A decimal type. It is correct, but it serialises inconsistently across our languages and invites a bare number to slip in.
