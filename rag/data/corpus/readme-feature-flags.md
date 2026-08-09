---
title: flagd-service
source: readme
space: engineering
team: platform
doc_type: readme
updated: 2026-01-22
tags: [feature-flags, rollout, config, sdk]
---

# flagd-service

`flagd-service` is the feature flag control plane. Clients evaluate flags
locally against a snapshot pushed over a streaming connection, so an evaluation
never makes a network call on the hot path.

## Flag kinds

* **Release flags** — short lived, guard an unfinished feature. Must have an
  owner and an expiry date; the weekly flag report nags on anything older than
  60 days.
* **Operational flags** — long lived kill switches, e.g. `payments.sepa_instant.enabled`.
* **Experiment flags** — bucketed by a stable hash of `customer_id`, never by
  request, so a customer sees a consistent variant.

## Evaluation order

Targeting rules are evaluated top to bottom; the first match wins. If no rule
matches, the default variant is served. A flag that fails to evaluate for any
reason serves the default variant and increments `flag_eval_error_total` — flags
never throw into caller code.

## Safety rails

Changing an operational flag in production requires two-person approval in the
UI. The audit trail records who flipped what, when, and the previous value, and
is retained for seven years to satisfy the audit requirements in the data
retention policy.
