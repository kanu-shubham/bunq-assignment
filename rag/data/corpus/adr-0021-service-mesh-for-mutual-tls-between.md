---
title: ADR-0021 Service mesh for mutual TLS between services
source: confluence
space: engineering
team: platform
doc_type: adr
updated: 2025-05-21
tags: [adr, service, platform]
---

# ADR-0021: Service mesh for mutual TLS between services

**Status:** Accepted

## Context

Internal traffic was unencrypted inside the cluster and each service implemented its own retry and timeout behaviour differently.

## Decision

Adopt a service mesh for mutual TLS, retries, and traffic shifting. Application code keeps deadlines; the mesh owns transport concerns.

## Consequences

**Good.** Encryption in transit everywhere with no application change. Consistent retry semantics. Traffic shifting for canaries.

**Bad.** A sidecar per pod costs memory and adds a hop to every request, and a mesh outage is now a cluster-wide event.

**Rejected alternative.** Library-level mutual TLS. It works, and it means every language runtime needs its own correct implementation.
