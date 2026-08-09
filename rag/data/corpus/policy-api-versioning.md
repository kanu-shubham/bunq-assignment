---
title: Public API versioning and deprecation
source: notion
space: engineering
team: payments
doc_type: policy
updated: 2026-04-09
tags: [api, versioning, deprecation, breaking-change, partners]
---

# Public API versioning and deprecation

## Versioning scheme

The public API is versioned in the path: `/v1/`, `/v2/`. We add a major version
only for a breaking change we cannot avoid. Additive changes — new endpoints, new
optional request fields, new response fields — ship inside the current version,
and clients must tolerate unknown response fields.

## What counts as breaking

Removing a field, renaming a field, narrowing a type, changing an enum's meaning,
making an optional request field required, or changing a default. Adding a new
enum *value* is breaking in practice for strictly-typed clients, so new values
ship behind an opt-in header for one release cycle.

## Deprecation timeline

1. Announce, with a migration guide and a dated sunset.
2. `Deprecation` and `Sunset` response headers on every affected endpoint.
3. Minimum 12 months of parallel operation for a major version.
4. Brownouts: short, scheduled, announced outages of the old version at 60, 30,
   and 7 days before sunset, so integrators discover the dependency before it is
   fatal rather than after.

## Communication

Partners hear it from the changelog, an email to the registered technical
contact, and the deprecation headers. Three channels, because at least one will
be filtered into a mailbox nobody reads.
