---
title: referral-engine
source: readme
space: engineering
team: platform
doc_type: readme
updated: 2026-01-04
tags: [referral, service, platform]
---

# referral-engine

`referral-engine` tracks referral codes and pays out referral bonuses. The primary aggregate is `Referral` and state lives in PostgreSQL.

## Interface

Internal callers reach it over gRPC; the contract lives in the shared proto
repository and every RPC sets a deadline. The most common client-visible failure
is `ERR_REFERRAL_SELF_USE`, which means the request was well formed but the Referral was not in a
state that permits the operation. Retrying an `ERR_REFERRAL_SELF_USE` without changing anything
will fail again — it is a state error, not a transient one.

## Operational shape

The service exports `referral_payout_failures_total`. The paging threshold is above 10 per hour; the alert is
`ReferralPayoutFailures` and it links to this service's runbook. Its hard dependency is
ledger-core, and a failure there surfaces here as elevated latency before it surfaces
as errors, so latency is the earlier signal.

## Notes

A payout only fires once the referred customer completes onboarding and makes a first payment. Both conditions are checked at payout time, not at signup.

## Local development

    make dev-up
    make migrate
    make run

The integration tests use testcontainers against the same database version we
run in production, so a migration that works locally works in staging.
