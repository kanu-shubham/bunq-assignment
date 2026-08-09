---
title: Testing strategy
source: notion
space: engineering
team: platform
doc_type: standard
updated: 2026-05-11
tags: [testing, unit-tests, integration, contract, flaky]
---

# Testing strategy

## Shape

Mostly unit tests, a solid layer of integration tests against real dependencies
in containers, and a thin end-to-end suite covering the handful of journeys that
must never break: signup, first payment, balance display.

We do not chase a coverage number. Coverage is a smoke detector, not a fire
alarm: a drop is worth looking at, a specific percentage is worth nothing. CI
fails on a drop of more than two points against the base branch.

## Integration tests

Use testcontainers against the real PostgreSQL, Kafka, and Redis versions we run
in production. Mocking a database means testing your understanding of the
database rather than the database.

## Contract tests

Every gRPC service publishes a contract test suite that its consumers run in
their own CI. A provider change that breaks a consumer fails the provider's
pipeline, not the consumer's, which is the whole point.

## Flaky tests

A flaky test is quarantined within one business day of being identified, with a
ticket assigned to the owning team and a two week deadline. After two weeks it
is deleted. A test nobody trusts is worse than no test, because it trains people
to re-run the pipeline without reading the failure.

## What we do not test

Generated code, third-party libraries, and framework behaviour. Test your logic,
not theirs.
