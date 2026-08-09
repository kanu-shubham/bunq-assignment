---
title: Runbook - TLS and scheme certificate rotation
source: confluence
space: security
team: platform
doc_type: runbook
updated: 2026-03-11
tags: [runbook, tls, certificates, rotation, security]
---

# Runbook: certificate rotation

## Public TLS certificates

Public edge certificates are issued by Let's Encrypt through cert-manager and
renew automatically at 30 days remaining. The alert `CertExpiringSoon` fires at
21 days, which means automation has already failed twice. Check the
`Certificate` resource status and the cert-manager logs for ACME challenge
failures; the usual cause is a stale DNS record for the challenge domain.

## Scheme client certificates

SEPA scheme access uses mutual TLS with certificates issued by the scheme's own
CA. These do **not** renew automatically. The process takes up to ten business
days:

1. Generate a CSR from the HSM-backed key. The private key never leaves the HSM.
2. Submit the CSR through the scheme portal with a signed request form.
3. Once issued, load the certificate into Vault at `secret/pki/scheme/<env>`.
4. Roll payments-api pods. The client supports two certificates simultaneously,
   so there is no cutover window.

Start this **60 days** before expiry. The calendar reminder is owned by the
payments lead and duplicated in the compliance calendar, because an expired
scheme certificate means a total SEPA outage, not a degraded one.
