---
title: Release process and change management
source: notion
space: engineering
team: platform
doc_type: policy
updated: 2026-05-29
tags: [release, change-management, approval, audit, deploy]
---

# Release process and change management

## Standard changes

A standard change is a deploy of application code through the normal pipeline
with an automated canary. It needs no separate approval — the pull request
review *is* the change approval, and the pipeline records the audit trail.

## Normal changes

Anything touching infrastructure, database schema in a non-backwards-compatible
way, or a third-party integration contract is a normal change. It requires a
change ticket with a rollback plan and a named approver from the owning team.

## Emergency changes

During an incident, do what is needed to stop customer harm. Record it
afterwards: a retroactive change ticket within one business day, referencing the
incident. We would rather have an accurate record written late than an approval
process that slows down a SEV1.

## What must be in a rollback plan

The exact command, the expected duration, what it does *not* undo (migrations,
published events, sent emails), and how you will confirm it worked. "Roll back
the deploy" is not a rollback plan.

## Audit

Every production change is traceable to a pull request, an approver, and a
timestamp. This is retained for seven years. It is the single most common thing
auditors ask for and the reason no one gets a manual production shell.
