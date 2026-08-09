---
title: Secrets management
source: notion
space: security
team: platform
doc_type: policy
updated: 2026-02-19
tags: [secrets, vault, rotation, security, credentials]
---

# Secrets management

## Where secrets live

HashiCorp Vault, always. Not in environment files committed to a repository, not
in CI variables, not in a password manager note pasted into a channel. Services
authenticate to Vault with their Kubernetes service account and receive a
short-lived token.

## Rotation

| Secret type | Rotation |
| --- | --- |
| Database credentials | Dynamic, 24 hour lease |
| Vendor API keys | 90 days |
| Webhook signing secrets | 180 days, dual-active during rotation |
| Scheme mTLS certificates | Per scheme validity, start 60 days early |

Rotation is automated where the vendor supports it and calendared where it is
not. An overdue rotation is a ticket with a due date, not a nagging reminder.

## If a secret leaks

Assume compromise from the moment of exposure, not from the moment of detection.

1. Rotate immediately. Do not wait for a change window.
2. Declare a SEV2 minimum, SEV1 if the secret grants production data access.
3. Preserve the evidence — do not force-push away the commit; the audit trail
   matters more than the tidiness of the history.
4. Review access logs for use of the leaked credential during the exposure window.

## Detection

`gitleaks` runs pre-commit and in CI, and a repository-wide scan runs nightly.
Pre-commit hooks are advisory; the CI check is blocking and cannot be skipped.
