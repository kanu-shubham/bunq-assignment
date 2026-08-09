---
title: Incident severity levels and comms
source: confluence
space: engineering
team: platform
doc_type: runbook
updated: 2026-02-27
tags: [incident, severity, sev1, comms, process]
---

# Incident severity levels

| Level | Definition | Response |
| --- | --- | --- |
| SEV1 | Customer money is wrong, or a core flow is fully down | Page immediately, incident channel, exec update every 30 min |
| SEV2 | Significant degradation, workaround exists | Page during business hours, channel, update hourly |
| SEV3 | Minor or internal-only impact | Ticket, next business day |

## Declaring

Anyone can declare an incident. Over-declaring is explicitly encouraged; you
will never be criticised for calling a SEV2 that turned out to be a SEV3.
Declare in `#incidents` with `/incident declare <sev> <one-line summary>`, which
creates the channel, the doc, and the timeline automatically.

## Roles

* **Incident commander** — owns the response, does not debug.
* **Communications lead** — owns status page and internal updates.
* **Operations lead** — owns hands on keyboard.

For a SEV1 these must be three different people. For a SEV2 the commander may
also run comms.

## After

A written postmortem is required for every SEV1 and SEV2 within five business
days. Postmortems are blameless; we name systems and decisions, never people.
Action items must have an owner and a due date or they do not count.
