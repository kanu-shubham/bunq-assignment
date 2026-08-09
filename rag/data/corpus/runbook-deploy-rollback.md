---
title: Runbook - Deploy and rollback
source: confluence
space: engineering
team: platform
doc_type: runbook
updated: 2026-06-04
tags: [deploy, rollback, canary, argo, release]
---

# Runbook: deploy and rollback

Deploys are continuous. Merging to `main` builds an image, promotes it to
staging, and — if the staging smoke suite passes — opens a production rollout in
Argo Rollouts.

## Canary stages

Production rollout is a four step canary: 5% for 10 minutes, 25% for 10 minutes,
50% for 5 minutes, then 100%. Each step is gated on an analysis run that checks
error rate and p99 latency against the baseline. A failed analysis aborts and
rolls back automatically without human involvement.

## Manual rollback

    kubectl argo rollouts undo <rollout> -n <namespace>
    kubectl argo rollouts status <rollout> -n <namespace>

Rollback restores the previous ReplicaSet and takes about 90 seconds. It does
**not** roll back database migrations. Every migration must be
backwards-compatible with the previous release — expand now, contract in a later
release — precisely so that rollback stays a one-command operation.

## Freeze windows

Deploys are frozen from 16:00 Friday to 09:00 Monday, and for the two business
days around each scheme settlement cycle change. Emergency fixes during a freeze
need approval from the on-call lead in the incident channel.
