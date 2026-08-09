---
title: identity-kyc
source: readme
space: engineering
team: risk
doc_type: readme
updated: 2026-03-30
tags: [kyc, identity, onboarding, compliance, vendor]
---

# identity-kyc

`identity-kyc` orchestrates customer due diligence: document capture, liveness
check, sanctions and PEP screening, and the periodic review cycle.

## Vendors

Document and liveness verification is delegated to Veriflow. Sanctions and PEP
screening uses ScreenPoint. Both are wrapped behind the `Verifier` and
`Screener` interfaces so a vendor can be swapped without touching the state
machine. Vendor credentials live in Vault under `secret/kyc/*` and are rotated
every 90 days.

## Onboarding state machine

    STARTED -> DOCUMENT_SUBMITTED -> LIVENESS_PASSED -> SCREENED -> APPROVED
                                                              \-> MANUAL_REVIEW

A customer who lands in `MANUAL_REVIEW` is picked up by the compliance queue
with an SLA of one business day. Screening hits are never auto-rejected; a human
always makes the final call, which is a regulatory requirement and not a product
preference.

## Periodic review

Standard risk customers are re-screened every 36 months, elevated risk every 12
months, and high risk every 6 months. The scheduler emits `ReviewDue` events;
missing a review window is a reportable compliance breach, so the
`kyc_review_overdue_total` metric pages the risk on-call at any non-zero value.
