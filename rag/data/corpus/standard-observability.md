---
title: Observability standards
source: notion
space: engineering
team: platform
doc_type: standard
updated: 2026-06-08
tags: [observability, metrics, logging, tracing, slo, alerts]
---

# Observability standards

## The three signals

**Metrics** are Prometheus, scraped every 15 seconds. Use the RED method for
services (rate, errors, duration) and USE for resources (utilisation,
saturation, errors). Histogram buckets must be chosen for your actual latency
distribution — the default buckets are wrong for a service with a 120ms budget.

**Logs** are structured JSON to stdout. Required fields: `ts`, `level`, `msg`,
`service`, `trace_id`. Never log a full request body, a token, a PAN, or an IBAN.
The log pipeline redacts patterns that look like card numbers, but redaction is
a safety net, not a design.

**Traces** use OpenTelemetry with a 1% head sample plus tail sampling that keeps
every trace containing an error or exceeding 1 second.

## SLOs

Every customer-facing service defines an availability SLO and a latency SLO with
a 30 day rolling window. Alerts page on burn rate, not on instantaneous
threshold breaches: a fast burn (2% of budget in an hour) pages, a slow burn (5%
in six hours) opens a ticket.

## Alert quality

Every alert must link to a runbook and answer "what does a human do about this
right now". An alert with no action is deleted, not silenced. We track the ratio
of actionable pages and review it monthly; anything below 70% is treated as an
engineering problem, not an on-call problem.
