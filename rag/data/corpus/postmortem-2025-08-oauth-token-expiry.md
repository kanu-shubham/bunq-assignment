---
title: Postmortem - Expired vendor OAuth token
source: confluence
space: engineering
team: platform
doc_type: postmortem
updated: 2025-08-16
tags: [postmortem, oauth-token-expiry, sev2]
---

# Postmortem: Expired vendor OAuth token (2025-08-16)

**Severity:** SEV2
**Duration:** 2h 40m
**Customer impact:** merchant enrichment stopped; raw descriptors shown to customers

## Summary

The refresh token for the enrichment vendor expired after 180 days of use and nothing renewed it.

## Root cause

The vendor's refresh tokens are single-use and rotate on every refresh. Our client stored the original and replayed it, which worked until the vendor tightened enforcement.

## What we changed

1. Store the rotated refresh token on every refresh, in the vault, atomically.
2. Alert on credential age and on consecutive refresh failures.
3. A synthetic check that exercises the vendor path every ten minutes.

## Lesson

Credential handling that works by accident works until the other side changes. Read the vendor's rotation semantics rather than inferring them from behaviour.
