---
title: On-call rotation policy
source: notion
space: engineering
team: platform
doc_type: policy
updated: 2026-03-07
tags: [oncall, rotation, compensation, handover, pager]
---

# On-call rotation policy

## Shape of the rotation

Rotations are weekly, Tuesday 10:00 to Tuesday 10:00, with a primary and a
secondary. A rotation needs at least six people; below that we merge with a
neighbouring team rather than burn out four.

## Expectations

The primary acknowledges a page within 5 minutes and is expected to be able to
reach a laptop and a stable connection within 15 minutes. The secondary is the
escalation path and is paged automatically if the primary does not acknowledge
within 10 minutes.

You are not expected to fix everything yourself. Escalating early is a good
outcome, not an admission of anything.

## Compensation

On-call is compensated at a flat weekly rate plus an hourly rate for time worked
outside business hours, with a minimum of one hour per incident. Time worked
overnight entitles you to a paid rest day, and taking it is expected rather than
optional; your manager will ask.

## Handover

Handover happens live, not in writing alone. Cover: what is still open, what you
silenced and why, anything you would page yourself about tonight, and any
alert that fired more than twice and should be tuned.

## Alert hygiene

An alert that fires and requires no action is a bug. The on-call engineer owns
either fixing it or deleting it during their shift, and the handover explicitly
asks about it.
