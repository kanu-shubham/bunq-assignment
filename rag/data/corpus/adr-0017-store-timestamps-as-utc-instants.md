---
title: ADR-0017 Store timestamps as UTC instants
source: confluence
space: engineering
team: platform
doc_type: adr
updated: 2025-04-16
tags: [adr, store, platform]
---

# ADR-0017: Store timestamps as UTC instants

**Status:** Accepted

## Context

A mixture of naive local timestamps and offset-carrying timestamps meant a comparison between two rows was not reliably meaningful.

## Decision

Persist instants in UTC. Store the originating timezone as a separate column when the local wall-clock time is itself meaningful, such as a scheduled job.

## Consequences

**Good.** Comparisons are total and correct. Clock changes stop being a data problem.

**Bad.** Rendering a user-facing time needs the timezone at the edge, which is one more thing every client has to get right.

**Rejected alternative.** Storing local time with an offset. It is lossy across timezone rule changes, which happen more often than people expect.
