---
title: ADR-0025 Cache invalidation by write-through, not TTL alone
source: confluence
space: engineering
team: platform
doc_type: adr
updated: 2025-06-26
tags: [adr, cache, platform]
---

# ADR-0025: Cache invalidation by write-through, not TTL alone

**Status:** Accepted

## Context

Reference data was cached with a five minute TTL, so a correction to a merchant name took five minutes to appear and support could not tell whether a fix had worked.

## Decision

Caches for reference data are written through on update and carry a long TTL as a backstop rather than as the primary mechanism.

## Consequences

**Good.** Corrections are visible immediately. The TTL still bounds the damage from a missed invalidation.

**Bad.** Every writer must know about the cache, which is coupling we would rather not have. It is contained behind a repository interface.

**Rejected alternative.** Shortening the TTL. It multiplies read load on the origin and still leaves a stale window.
