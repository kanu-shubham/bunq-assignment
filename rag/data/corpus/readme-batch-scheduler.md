---
title: batch-scheduler
source: readme
space: engineering
team: platform
doc_type: readme
updated: 2026-04-25
tags: [batch, service, platform]
---

# batch-scheduler

`batch-scheduler` runs scheduled jobs with leader election and per-job concurrency limits. The primary aggregate is `JobRun` and state lives in PostgreSQL with advisory locks.

## Interface

Internal callers reach it over gRPC; the contract lives in the shared proto
repository and every RPC sets a deadline. The most common client-visible failure
is `ERR_JOB_ALREADY_RUNNING`, which means the request was well formed but the JobRun was not in a
state that permits the operation. Retrying an `ERR_JOB_ALREADY_RUNNING` without changing anything
will fail again — it is a state error, not a transient one.

## Operational shape

The service exports `job_run_overdue_seconds`. The paging threshold is above 600 seconds; the alert is
`ScheduledJobOverdue` and it links to this service's runbook. Its hard dependency is
PostgreSQL, and a failure there surfaces here as elevated latency before it surfaces
as errors, so latency is the earlier signal.

## Notes

A job that overruns its next scheduled start is skipped, not queued. Queueing an already-late job is how a slow job becomes a stampede.

## Local development

    make dev-up
    make migrate
    make run

The integration tests use testcontainers against the same database version we
run in production, so a migration that works locally works in staging.
