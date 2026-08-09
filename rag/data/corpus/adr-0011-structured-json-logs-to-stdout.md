---
title: ADR-0011 Structured JSON logs to stdout
source: confluence
space: engineering
team: platform
doc_type: adr
updated: 2025-02-06
tags: [adr, structured, platform]
---

# ADR-0011: Structured JSON logs to stdout

**Status:** Accepted

## Context

Every service logged in its own format and correlating a request across three services meant three different grep incantations.

## Decision

All services log structured JSON to stdout with a fixed set of required fields. The platform owns collection; services own nothing but the write.

## Consequences

**Good.** One parser, one query language, trace correlation for free.

**Bad.** Local development output is less readable; a pretty-printer wrapper is provided and nobody uses it consistently.

**Rejected alternative.** A logging sidecar per service. More moving parts and one more thing to fail at 3am.
