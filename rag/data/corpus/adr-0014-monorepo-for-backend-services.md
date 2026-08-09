---
title: ADR-0014 Monorepo for backend services
source: confluence
space: engineering
team: platform
doc_type: adr
updated: 2025-03-11
tags: [adr, monorepo, platform]
---

# ADR-0014: Monorepo for backend services

**Status:** Accepted

## Context

Cross-service changes required coordinated pull requests across six repositories and were regularly merged out of order.

## Decision

Backend services live in one repository with a shared build graph. Mobile clients stay separate.

## Consequences

**Good.** Atomic cross-service changes, one dependency version, contract tests that cannot drift.

**Bad.** Build tooling is a full-time concern and CI needs affected-target detection to stay under ten minutes.

**Rejected alternative.** Polyrepo with a published shared library. It moves the coordination cost from merge time to release time, where it is less visible and more expensive.
