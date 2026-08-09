---
title: Glossary of internal terms
source: notion
space: engineering
team: platform
doc_type: glossary
updated: 2026-06-02
tags: [glossary, terminology, acronyms, definitions]
---

# Glossary

**Aurora** — the internal name for the whole platform. Never customer-facing.

**Break** — a discrepancy found by reconciliation between two sources that
should agree. See the ledger reconciliation runbook.

**Brownout** — a short, scheduled, announced outage of a deprecated API version,
used to make integrators discover a dependency before it is fatal.

**Burn rate** — how fast an SLO's error budget is being consumed, expressed as a
multiple of the sustainable rate. Alerts page on burn rate, not raw error rate.

**Entry** — a single debit or credit line in the ledger. Entries always come in
balancing pairs within a transfer.

**Expand and contract** — the migration pattern where a schema change is split
into an additive step, a backfill, a code change, and only later a removal, so
that any single release can be rolled back.

**Fail open / fail closed** — what a component does when a dependency is
unavailable. notification-gateway fails open on dedupe (may duplicate);
identity-kyc fails closed on screening (blocks rather than approves).

**Poison message** — a message that always fails processing and therefore blocks
a partition forever if not skipped.

**Projection** — derived, rebuildable state computed from an event stream. Never
edit a projection by hand.

**SEV** — incident severity, 1 through 3. See the incident severity page.

**Step-up** — an additional authentication challenge triggered by a fraud score
in the 300 to 699 band.

**Thundering herd** — many clients retrying in lockstep because their backoff is
unjittered, amplifying a recovery into a second outage.
