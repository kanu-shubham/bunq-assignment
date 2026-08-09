---
title: Postmortem - KYC vendor outage blocked onboarding
source: confluence
space: engineering
team: risk
doc_type: postmortem
updated: 2025-06-26
tags: [postmortem, kyc, vendor, outage, sev2, circuit-breaker]
---

# Postmortem: KYC vendor outage blocked onboarding (2025-06-23)

**Severity:** SEV2
**Duration:** 5h 10m
**Customer impact:** ~2,300 signups could not complete document verification

## Summary

Veriflow, our document and liveness vendor, had a regional outage. Our
integration retried aggressively, exhausted the HTTP connection pool in
identity-kyc, and turned a vendor-side failure into a full onboarding outage —
including for customers who were past the document step and only needed
screening, which uses a different vendor entirely.

## Root cause

Two compounding mistakes. First, the retry policy was three immediate retries
with no backoff and no jitter, which multiplied load against an already
struggling vendor. Second, the vendor client shared one connection pool with the
screening client, so saturation in one path starved the other.

## What we changed

1. Separate connection pools per vendor. Blast radius is now one vendor.
2. Exponential backoff with full jitter, and a circuit breaker that opens after
   20 consecutive failures and half-opens after 30 seconds.
3. A degraded mode: when document verification is unavailable, onboarding
   continues to `DOCUMENT_SUBMITTED` and the customer is told verification is in
   progress rather than being shown a generic error.
4. Vendor status is now a first-class dashboard panel instead of something we
   discover from customer complaints.

## Lesson

A synchronous dependency on a third party is a synchronous dependency on their
worst day. Every vendor call now needs an explicit answer to "what does the
product do when this returns nothing?" before it ships.
