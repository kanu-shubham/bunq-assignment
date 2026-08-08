---
doc_id: security-incident-response
title: Security Incident Response
uri: https://intranet.acme.example/security/incident-response
source: confluence
visibility: internal
updated_at: 2026-06-18
version: "3.1"
---

# Security Incident Response

## Severity levels

| Severity | Definition | Response target |
| --- | --- | --- |
| Sev-1 | Confirmed data exposure, or full loss of a customer-facing service | Acknowledge within 15 minutes, 24/7 |
| Sev-2 | Degraded service for a subset of customers, or a credible exposure risk | Acknowledge within 1 hour during business hours |
| Sev-3 | Internal-only impact, no customer data at risk | Next business day |

## Declaring an incident

Anyone can declare an incident. Post in `#sec-incident` with the severity you
believe applies and the on-call security engineer will confirm or re-grade it
within the response target. Over-declaring is explicitly encouraged; there is no
penalty for a Sev-1 that turns out to be a Sev-3.

## Error codes

Application error codes surfaced to customers map to internal causes as follows.

| Code | Meaning | First action |
| --- | --- | --- |
| ERR_ACCT_4032 | Account locked after repeated failed authentication | Verify identity through the support flow, then unlock in the admin console. Do not reset the password on the customer's behalf. |
| ERR_ACCT_4033 | Account frozen by compliance hold | Do not unlock. Route to the compliance queue. |
| ERR_PAY_5010 | Payment provider rejected the transaction | Retry once, then escalate to the payments on-call. |
| ERR_PAGE_5521 | Paging pipeline failed to deliver an alert | Treat as Sev-2 immediately: alerts may be silently dropping. |

## Evidence handling

Do not investigate on the affected production host. Snapshot it, work from the
snapshot, and record the snapshot id in the incident channel. Logs pulled during
an incident are retained for 400 days regardless of the standard retention
window.

## Customer notification

Under GDPR, a confirmed personal data breach must be reported to the supervisory
authority within 72 hours of becoming aware of it. The Data Protection Officer
owns that decision and that clock; do not communicate externally without them.
