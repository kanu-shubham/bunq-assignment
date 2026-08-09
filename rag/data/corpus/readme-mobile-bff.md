---
title: mobile-bff
source: readme
space: engineering
team: platform
doc_type: readme
updated: 2026-06-01
tags: [bff, mobile, graphql, caching, api]
---

# mobile-bff

`mobile-bff` is the backend-for-frontend that the iOS and Android apps talk to.
It aggregates calls to ledger-core, payments-api, and identity-kyc into a single
screen-shaped response so the app makes one request per screen, not seven.

## Protocol

The app talks GraphQL over HTTP/2. Internally the BFF speaks gRPC to every
upstream, per ADR-0019. Persisted queries are mandatory in production: the app
sends a query hash, not a query document, which keeps the request small and
stops arbitrary queries from reaching the graph.

## Caching

Per-request dataloaders collapse duplicate upstream calls within a single
response. On top of that, reference data (currencies, merchant categories,
country lists) is cached in-process for 10 minutes. Customer data is never
cached in the BFF — staleness on a balance is a support ticket we do not want.

## Version skew

The BFF supports the two most recent app minor versions plus the current one.
Older clients receive HTTP 426 Upgrade Required with a deep link to the store
listing. The supported window is enforced in `SupportedVersions` and reviewed at
every release; see the API versioning policy for the deprecation timeline.
