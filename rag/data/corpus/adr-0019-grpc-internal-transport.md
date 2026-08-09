---
title: ADR-0019 gRPC for internal service transport
source: confluence
space: engineering
team: platform
doc_type: adr
updated: 2025-05-06
tags: [adr, grpc, protobuf, transport, contracts]
---

# ADR-0019: gRPC for internal service transport

**Status:** Accepted
**Date:** 2025-05-06

## Context

Internal calls were a mix of JSON over HTTP with hand-written clients. Contract
drift between services was a recurring source of incidents, and every team wrote
its own retry and deadline handling, badly.

## Decision

All internal service-to-service traffic uses gRPC with protobuf contracts stored
in a central `proto/` repository. Public and partner-facing APIs remain JSON
over HTTP — protobuf is an internal implementation detail, not something we push
onto integrators.

## Rules

* Backwards-compatible changes only: never reuse a field number, never change a
  field type, never remove a required behaviour without a deprecation cycle.
* Every RPC must set a deadline. A call without a deadline is rejected by the
  interceptor in CI's contract test.
* Generated code is committed, not generated at build time, so a broken codegen
  toolchain cannot break every build at once.

## Consequences

Contract breakage is caught by buf's breaking-change detector in CI rather than
in production. Debugging is harder than curling a JSON endpoint, which we
mitigate with `grpcurl` reflection enabled in non-production environments only.
