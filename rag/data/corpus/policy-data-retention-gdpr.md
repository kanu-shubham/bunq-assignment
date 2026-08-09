---
title: Data retention and erasure
source: notion
space: security
team: platform
doc_type: policy
updated: 2026-01-30
tags: [gdpr, retention, erasure, privacy, compliance]
---

# Data retention and erasure

## Retention periods

| Data | Period | Driver |
| --- | --- | --- |
| Transaction records | 7 years | Financial regulation |
| KYC documents and screening results | 5 years after relationship ends | AML |
| Application logs | 30 days | Operational |
| Access and change audit logs | 7 years | Audit |
| Marketing consent records | Life of consent + 3 years | GDPR accountability |
| Support conversations | 3 years | Operational |

## Right to erasure

A customer erasure request does **not** delete data we are legally required to
retain. Transaction and KYC records survive erasure; what is erased is
everything not covered by a retention obligation — marketing profiles, support
conversation bodies, device analytics, and derived behavioural attributes.

Requests are handled by the privacy team through the erasure tool, never by
engineers running deletes by hand. The tool produces a signed completion record
listing every system touched, which is what we show a regulator.

## Deadlines

Erasure and access requests must be fulfilled within 30 calendar days, extendable
once by 60 days for complex requests with written notice to the customer. The
clock starts at receipt, not at triage.

## Backups

Backups are not selectively edited. Erased data may persist in backups until the
backup ages out, currently a maximum of 35 days. This is documented in the
privacy notice and is an accepted, disclosed position rather than a gap.
