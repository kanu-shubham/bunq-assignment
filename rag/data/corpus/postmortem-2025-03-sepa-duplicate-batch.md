---
title: Postmortem - Duplicate SEPA batch submission
source: confluence
space: engineering
team: payments
doc_type: postmortem
updated: 2025-03-19
tags: [postmortem, sepa, duplicate, idempotency, sev1]
---

# Postmortem: duplicate SEPA batch submission (2025-03-14)

**Severity:** SEV1
**Duration:** 3h 40m from first duplicate to full reversal
**Customer impact:** 8,412 customers were debited twice, total 1.9M EUR

## Summary

The nightly SEPA batch was submitted twice to the scheme. The second submission
carried a fresh batch identifier, so the scheme accepted it as new work rather
than rejecting it as a replay.

## Timeline

* 02:14 — batch submitter pod is OOM-killed after the file write but before the
  submission was recorded in `sepa_batch_submission`.
* 02:15 — the replacement pod starts, finds no submission record, and resubmits.
* 06:02 — a customer contacts support about a double debit.
* 06:40 — payments on-call correlates support reports with two batch IDs.
* 09:54 — reversing batch confirmed accepted; all duplicate debits reversed.

## Root cause

Batch submission was not idempotent. The batch identifier was generated at
submission time from a UUID rather than derived from the batch contents, so a
retry could not be recognised as a retry by either side. The write-then-record
ordering meant a crash in the window between the two steps was indistinguishable
from "never submitted".

## What we changed

1. Batch identifiers are now a deterministic hash of `(settlement_date, account_set, entry_digest)`.
   A retry produces the same identifier and the scheme rejects it as a duplicate.
2. Submission is recorded in the same transaction as the file write, using the
   outbox pattern adopted in ADR-0023.
3. The submitter's memory limit was raised and a heap profile alert was added,
   but we treat this as incidental — the crash was the trigger, not the cause.

## What did not work

Our first instinct was to add a distributed lock around submission. We rejected
it: a lock makes the duplicate less likely but still leaves a window, whereas a
content-derived identifier removes the failure mode entirely.
