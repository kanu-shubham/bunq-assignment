---
title: notification-gateway
source: readme
space: engineering
team: platform
doc_type: readme
updated: 2026-04-19
tags: [notifications, push, email, templating]
---

# notification-gateway

`notification-gateway` fans a single internal `NotificationRequested` event out
to push (APNs, FCM), email (via Postmark), and in-app inbox. It owns template
rendering and per-user channel preferences.

## Templates

Templates are Go `text/template` files stored in `templates/<locale>/<key>.tmpl`
and are compiled at boot. A missing locale falls back to `en-NL`, then `en`. A
missing template key is a hard boot failure — we deliberately do not degrade
silently, because a silently missing payment-received notification is worse than
a failed deploy.

## Delivery guarantees

Delivery is at-least-once. Each request carries a `dedupe_key`; the gateway
keeps a 6 hour Redis set of delivered keys under `notif:dedupe:`. If Redis is
unavailable the gateway fails open and may deliver duplicates. This trade-off
was made deliberately after the 2025-09 Redis eviction incident.

## Throttling

Per-user throttle is 5 push notifications per minute. Marketing sends are
subject to an additional quiet-hours window of 22:00-08:00 in the recipient's
timezone; transactional sends bypass quiet hours entirely and are tagged
`priority: transactional` on the request.
