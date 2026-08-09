---
title: savings-goals
source: readme
space: engineering
team: ledger
doc_type: readme
updated: 2026-05-02
tags: [savings, service, ledger]
---

# savings-goals

`savings-goals` lets customers create named savings pots and automatic contributions. The primary aggregate is `Goal` and state lives in PostgreSQL.

## Interface

Internal callers reach it over gRPC; the contract lives in the shared proto
repository and every RPC sets a deadline. The most common client-visible failure
is `ERR_GOAL_LIMIT_REACHED`, which means the request was well formed but the Goal was not in a
state that permits the operation. Retrying an `ERR_GOAL_LIMIT_REACHED` without changing anything
will fail again — it is a state error, not a transient one.

## Operational shape

The service exports `goal_contribution_failures_total`. The paging threshold is above 20 per hour; the alert is
`GoalContributionFailures` and it links to this service's runbook. Its hard dependency is
ledger-core, and a failure there surfaces here as elevated latency before it surfaces
as errors, so latency is the earlier signal.

## Notes

A pot is a labelled sub-balance, not a separate account. It has no IBAN and cannot be paid into from outside.

## Local development

    make dev-up
    make migrate
    make run

The integration tests use testcontainers against the same database version we
run in production, so a migration that works locally works in staging.
