---
title: webhook-dispatcher
source: readme
space: engineering
team: platform
doc_type: readme
updated: 2026-02-08
tags: [webhooks, retries, signing, backoff]
---

# webhook-dispatcher

`webhook-dispatcher` delivers outbound events to partner HTTP endpoints. It is
the only egress path for customer-visible events and it is deliberately boring.

## Signing

Every request carries `X-Aurora-Signature: t=<unix>,v1=<hex>` where `v1` is an
HMAC-SHA256 over `"{t}.{raw_body}"` using the partner's active signing secret.
Partners should reject timestamps older than five minutes to blunt replay
attacks. Two secrets can be active at once during rotation; the dispatcher signs
with the newest and partners should accept either.

## Retry policy

Failed deliveries are retried with exponential backoff and full jitter:
1s, 2s, 4s, 8s, 30s, 2m, 10m, 1h, 6h. After nine attempts the delivery is
marked `EXHAUSTED` and the endpoint is put into a circuit-broken state for 15
minutes. Any 2xx response is a success; 410 Gone permanently disables the
endpoint and notifies the partner's technical contact.

## Ordering

There is no global ordering guarantee. Events for a single `subscription_id` are
delivered in order only while deliveries succeed; a retry will overtake nothing,
but a later event may be delivered before an earlier retried one. Partners that
need strict ordering should sort by the `sequence` field in the payload.
