# API reference

Base URL through the load balancer: `http://localhost`
Interactive docs per service: `http://localhost:8082/swagger-ui.html` (and 8081, 8083, 8084).

## Conventions

| | |
|---|---|
| **Auth** | `Authorization: Bearer <api key or JWT>` on everything except `/v1/auth/**` |
| **Amounts** | always **minor units** — `10000` is €100.00. Never a decimal. |
| **Ids** | prefixed and self-describing: `acct_`, `pi_`, `ch_`, `re_`, `we_` |
| **JSON** | `snake_case` on the wire, `camelCase` in Java. Jackson bridges the two. |
| **Idempotency** | `Idempotency-Key: <uuid>` — optional on create, **required** on confirm and refund |
| **Tracing** | `X-Request-Id` is echoed on every response; quote it in support tickets |

### Error envelope

Every error, from every service, has one shape:

```json
{
  "error": {
    "code": "card_declined",
    "message": "Your card has insufficient funds.",
    "param": "payment_method",
    "request_id": "req_a1b2c3d4e5f6g7h8i9j0",
    "timestamp": "2026-03-01T12:00:00Z"
  }
}
```

Validation failures add a `violations` array with one entry per field.

| HTTP | `code` | Meaning |
|---|---|---|
| 400 | `invalid_request` | malformed body or a business rule violated |
| 401 | `authentication_required` | missing, invalid, expired or revoked credential |
| 402 | `card_declined` | the issuer said no — a business answer, not an outage |
| 403 | `permission_denied` | authenticated, but not allowed |
| 404 | `resource_not_found` | does not exist **or** belongs to another merchant |
| 409 | `idempotency_conflict` | same key, different request body |
| 409 | `invalid_state_transition` | e.g. cancelling a succeeded payment |
| 409 | `resource_conflict` | concurrent modification — safe to retry |
| 429 | `rate_limited` | token bucket empty; back off |
| 503 | `service_unavailable` | circuit breaker open |
| 503 | `acquirer_unavailable` | card network unreachable; retry with the same key |

---

## Authentication — `auth-service`

### `POST /v1/auth/merchants`
Register a merchant and receive its first test API key. *(Open in this demo; real onboarding is
gated behind KYC.)*

```json
{ "name": "Acme Coffee", "email": "acme@example.com", "country_code": "NL" }
```

→ `201` with `merchant` and `api_key`. **The secret is shown exactly once.**

### `POST /v1/auth/token`
Exchange an API key for a short-lived JWT (30 minutes).

```json
{ "api_key": "sk_test_..." }
```

→ `200` `{ "access_token": "eyJ...", "token_type": "Bearer", "expires_in": 1800 }`

A POST, not a GET: a secret in a query string ends up in access logs and browser history.

### `POST /v1/auth/api_keys` · `GET /v1/auth/api_keys` · `DELETE /v1/auth/api_keys/{id}`
Issue, list and revoke keys. Revocation takes effect immediately for API keys; already-issued
JWTs remain valid until they expire — the reason TTLs are short.

### `GET /v1/auth/merchants/{id}`
Only your own. Anything else is a 404.

---

## Payments — `payment-service`

### `POST /v1/payment_intents`

```json
{
  "amount": 10000,
  "currency": "EUR",
  "customer_id": "cus_123",
  "description": "1kg Ethiopia Guji",
  "metadata": { "order_id": "ord_1001" }
}
```

`currency` — `USD`, `EUR`, `GBP`, `JPY`. `metadata` is stored as `jsonb` and indexed, so you
can find a payment by your own order id. Minimum 50 minor units; maximum 99,999,999.

→ `201`, status `requires_confirmation`.

### `POST /v1/payment_intents/{id}/confirm`
**`Idempotency-Key` required.** This is the call that moves money.

```json
{ "payment_method_id": "pm_card_visa" }
```

→ `200` `{ "payment_intent": {...}, "charge": { "amount": 10000, "fee": 320, "net": 9680 } }`

Fee is 2.9% + 30. Test tokens are listed in the README; `GET :8085/acquirer/v1/test_cards`
returns them live.

- `402 card_declined` — the issuer refused. The intent is `failed` and **can be retried** with
  another payment method.
- `503 acquirer_unavailable` — the network did not answer. The payment is **unresolved**;
  retry with the same key. We deliberately do not guess.

### `POST /v1/payment_intents/{id}/cancel`
Only before capture. `{ "reason": "customer changed their mind" }` — optional.

### `GET /v1/payment_intents/{id}` · `GET /v1/payment_intents?status=&page=&limit=`
`limit` is clamped to 100 — an unbounded limit is a denial-of-service vector dressed up as a
feature.

---

## Refunds and charges

### `POST /v1/refunds`
**`Idempotency-Key` required.**

```json
{ "charge": "ch_...", "amount": 5000, "reason": "REQUESTED_BY_CUSTOMER" }
```

Omit `amount` to refund the full remaining balance — which also removes a race between reading
the balance and asking for it.

`reason` — `REQUESTED_BY_CUSTOMER`, `DUPLICATE`, `FRAUDULENT`, `PRODUCT_UNSATISFACTORY`.

The platform fee is returned proportionally. Over-refunding is a `400`, enforced by the entity,
a row lock, and a database `CHECK` constraint.

### `GET /v1/refunds/{id}` · `GET /v1/charges/{id}` · `GET /v1/charges/{id}/refunds`

---

## Balance and ledger — `ledger-service`

### `GET /v1/balance`
Balances per account and currency. `merchant_payable` is what you are owed.

### `GET /v1/ledger/entries?page=&limit=`
The individual double-entry postings behind that balance. Entries sharing a `transaction_id`
are the two-or-more sides of one movement.

### `GET /v1/ledger/verify`
Recomputes every balance from the raw postings and reports drift. This is the audit that makes
the cached balance trustworthy — a ledger you cannot verify independently is just a number in
a column.

---

## Webhooks — `webhook-service`

### `POST /v1/webhook_endpoints`

```json
{
  "url": "https://example.com/paykit-hook",
  "enabled_events": ["payment_intent.succeeded", "refund.succeeded"],
  "description": "order fulfilment"
}
```

Use `["*"]` for everything. → `201`, including the signing `secret` — shown once.

HTTPS is required except for `localhost`. Private address ranges are rejected (SSRF).

### `GET /v1/webhook_endpoints` · `DELETE /v1/webhook_endpoints/{id}`
### `POST /v1/webhook_endpoints/{id}/enable`
Re-enable an endpoint auto-disabled after 50 consecutive failures.

### `GET /v1/events?page=&limit=`
Delivery attempts with status, attempt count, response code and last error.

### `POST /v1/events/{id}/retry`
Re-queue a delivery that gave up.

---

## Event types

Published to `payments.events.v1`, partitioned by payment-intent id so all events for one
payment stay ordered.

| Type | When | Ledger effect |
|---|---|---|
| `payment_intent.created` | intent created | none — no money has moved |
| `payment_intent.succeeded` | card captured | debit clearing; credit payable + fee revenue |
| `payment_intent.payment_failed` | issuer declined | none |
| `payment_intent.canceled` | abandoned pre-capture | none |
| `refund.succeeded` | money returned | the exact reverse of the payment |

---

## Operations endpoints

| Endpoint | Purpose |
|---|---|
| `/actuator/health` | liveness and readiness, including circuit-breaker state |
| `/actuator/prometheus` | metrics scrape target |
| `/actuator/circuitbreakers` | live breaker state (payment-service, gateway) |
| `/actuator/gateway/routes` | the gateway's effective routing table |

Useful business metrics: `paykit_payments_succeeded_total`, `paykit_payments_declined_total`
(tagged by `decline_code`), `paykit_acquirer_authorization_seconds`, `paykit_outbox_published_total`,
`paykit_webhooks_delivered_total`, and `paykit_payments_stuck` — the gauge that should always
be zero.
