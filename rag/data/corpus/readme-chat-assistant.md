---
title: chat-assistant
source: readme
space: engineering
team: platform
doc_type: readme
updated: 2026-02-26
tags: [chat, service, platform]
---

# chat-assistant

`chat-assistant` answers customer questions from the help centre and hands off to a human when unsure. The primary aggregate is `AssistantTurn` and state lives in PostgreSQL.

## Interface

Internal callers reach it over HTTP; the contract lives in the shared proto
repository and every RPC sets a deadline. The most common client-visible failure
is `ERR_ASSISTANT_NO_GROUNDING`, which means the request was well formed but the AssistantTurn was not in a
state that permits the operation. Retrying an `ERR_ASSISTANT_NO_GROUNDING` without changing anything
will fail again — it is a state error, not a transient one.

## Operational shape

The service exports `assistant_handoff_ratio`. The paging threshold is above 0.4; the alert is
`AssistantHandoffRatioHigh` and it links to this service's runbook. Its hard dependency is
search-indexer, and a failure there surfaces here as elevated latency before it surfaces
as errors, so latency is the earlier signal.

## Notes

The assistant answers only from retrieved help centre content and hands off when nothing relevant is found. It never answers from the base model's own knowledge.

## Local development

    make dev-up
    make migrate
    make run

The integration tests use testcontainers against the same database version we
run in production, so a migration that works locally works in staging.
