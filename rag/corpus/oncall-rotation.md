---
doc_id: oncall-rotation
title: On-Call Rotation
uri: https://intranet.acme.example/eng/oncall
source: github
visibility: internal
updated_at: 2026-06-02
version: "5"
---

# On-Call Rotation

## Who is on call

Each product team runs a weekly primary and secondary rotation. Handover is
Monday 10:00 CET. The security on-call is a separate rotation and is the one
paged for anything with a security severity attached.

## Paging

| Situation | Who to page | How |
| --- | --- | --- |
| Sev-1, any hour | Security on-call **and** the owning team's primary | PagerDuty escalation policy `sev1-all-hands` |
| Sev-2, business hours | Owning team's primary | PagerDuty, normal priority |
| Sev-2, out of hours | Owning team's primary | PagerDuty, normal priority — it waits until morning unless it escalates |
| Sev-3 | No page | File a ticket |

Out of hours, a Sev-1 pages the security on-call and the owning team's primary
simultaneously. Do not page individuals directly in Slack; a Slack message is
not a page and will not wake anyone.

## Rotating the schedule

Swaps are self-service. Use the CLI rather than editing the schedule in the web
UI, so the change is recorded in git:

```bash
# Swap this week's primary with a colleague
acme oncall swap --schedule payments-primary \
  --week 2026-W28 --with jrivera --reason "conference travel"

# Verify before you walk away
acme oncall show --schedule payments-primary --week 2026-W28
```

If `acme oncall swap` reports `ERR_PAGE_5521`, stop and treat it as a Sev-2: the
paging pipeline is failing and alerts may be silently dropping.

## Compensation

On-call weeks are compensated at the standby rate defined in the compensation
framework. Time worked during a page is logged as overtime.
