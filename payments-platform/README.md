# Paykit — a Stripe-shaped payments platform in Java 21 + Spring Boot

A complete, running microservice payments platform, built to be **read**. Every non-obvious
decision in this codebase has a comment next to it explaining *why* it is that way — not what
the line does, but which failure it prevents and what it costs.

If you are learning Java and Spring Boot, start with [`docs/LEARNING-GUIDE.md`](docs/LEARNING-GUIDE.md).
It maps every concept — records, sealed interfaces, generics, `@Transactional`, AOP proxies,
JPA locking, Kafka semantics — to the exact file where it is used for a real reason.

---

## What it does

Take a payment, charge a card, keep double-entry books, and tell the merchant about it.

```
   client
     │  POST /v1/payment_intents          (Idempotency-Key: uuid)
     ▼
 ┌─────────┐   server-side load balancing
 │  nginx  │   :80  — the only public port
 └────┬────┘
      ▼
 ┌──────────────┐  auth · rate limit · circuit breaker · correlation id
 │ api-gateway  │  (WebFlux, reactive)
 └──────┬───────┘
        │  lb://payment-service   ← client-side load balancing via Eureka
        ▼
 ┌─────────────────┐   ┌──────────────────┐
 │ payment-service │──▶│ acquirer (card   │   circuit breaker + retry + timeout
 │                 │   │ network sim)     │
 └────┬────────┬───┘   └──────────────────┘
      │        │
      │        └─▶ Postgres  (payment + outbox row, ONE transaction)
      ▼
   Kafka  payments.events.v1
      │
      ├─▶ ledger-service    double-entry postings, debits == credits
      └─▶ webhook-service   HMAC-signed HTTP delivery, retried with jitter
```

| Service | Port | Responsibility |
|---|---|---|
| `nginx` | 80 | Edge load balancer, the only exposed port |
| `api-gateway` | 8080 | Routing, auth, rate limiting, circuit breaking |
| `discovery-server` | 8761 | Eureka registry — where every instance lives |
| `auth-service` | 8081 | Merchants, hashed API keys, JWT issuance |
| `payment-service` | 8082 | Payment intents, charges, refunds, idempotency, outbox |
| `ledger-service` | 8083 | Double-entry bookkeeping from Kafka events |
| `webhook-service` | 8084 | Signed, retrying delivery to merchant servers |
| `acquirer-simulator` | 8085 | Fake card network you can make fail on demand |
| Prometheus / Grafana | 9090 / 3000 | Metrics |
| Kafka UI | 8090 | Inspect topics and consumer lag |

---

## Run it

Requires Docker and about 4GB of free memory.

```bash
cd payments-platform
docker compose up --build          # first build takes a few minutes
```

Wait until Eureka at <http://localhost:8761> lists all six services as `UP`.

To run more than one instance of anything:

```bash
docker compose up -d --scale payment-service=3 --scale api-gateway=2
```

Nothing else changes — nginx re-resolves the gateway replicas every 10 seconds, and the
gateway discovers the new payment-service instances through Eureka.

### Build and test without Docker

```bash
mvn clean install          # 127 tests
```

The integration tests use Testcontainers and are annotated
`@Testcontainers(disabledWithoutDocker = true)`, so without a Docker daemon they skip cleanly
(11 of them) instead of failing the build.

---

## A complete payment, end to end

Every command below is copy-pasteable. Everything goes through nginx on port 80.

### 1. Register a merchant

```bash
curl -s -X POST http://localhost/v1/auth/merchants \
  -H 'Content-Type: application/json' \
  -d '{"name":"Acme Coffee","email":"acme@example.com","country_code":"NL"}' | jq
```

```json
{
  "merchant": { "id": "acct_QmT4x...", "status": "active" },
  "api_key": {
    "secret": "sk_test_aB3dEf9x_QmT4xR7pLnV2sK8wYz1",
    "warning": "Store this secret now — it is hashed on our side and cannot be shown again."
  }
}
```

```bash
export API_KEY='sk_test_...'          # paste the secret
export MERCHANT='acct_...'            # paste the merchant id
```

### 2. Exchange the key for a token (optional but faster)

```bash
export TOKEN=$(curl -s -X POST http://localhost/v1/auth/token \
  -H 'Content-Type: application/json' \
  -d "{\"api_key\":\"$API_KEY\"}" | jq -r .access_token)
```

The API key works directly too — the gateway verifies it against auth-service and caches the
result in Redis. The JWT skips that round trip entirely. See `TokenService` for the trade-off.

### 3. Create a payment intent

```bash
curl -s -X POST http://localhost/v1/payment_intents \
  -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H 'Content-Type: application/json' \
  -d '{"amount":10000,"currency":"EUR","description":"1kg Ethiopia Guji",
       "metadata":{"order_id":"ord_1001"}}' | jq
```

> `amount` is in **minor units**. `10000` is €100.00. This is how every real payments API
> works, and why `Money` in this codebase is a `long` and never a `double`.

```bash
export PI=pi_...
```

### 4. Confirm it — this is where money moves

```bash
curl -s -X POST http://localhost/v1/payment_intents/$PI/confirm \
  -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H 'Content-Type: application/json' \
  -d '{"payment_method_id":"pm_card_visa"}' | jq
```

```json
{
  "payment_intent": { "status": "succeeded", "amount": 10000 },
  "charge": { "id": "ch_...", "amount": 10000, "fee": 320, "net": 9680 }
}
```

### 5. Watch the books update

The payment event went to Kafka; the ledger consumed it and wrote balanced postings.

```bash
curl -s http://localhost/v1/balance -H "Authorization: Bearer $TOKEN" | jq
```

```json
{ "accounts": [ { "account_type": "merchant_payable", "amount": 9680, "currency": "EUR" } ] }
```

And prove the ledger balances itself — this recomputes every balance from the raw postings
and compares it against the cached value:

```bash
curl -s http://localhost/v1/ledger/verify -H "Authorization: Bearer $TOKEN" | jq
```

### 6. Refund half of it

```bash
curl -s -X POST http://localhost/v1/refunds \
  -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H 'Content-Type: application/json' \
  -d "{\"charge\":\"ch_...\",\"amount\":5000,\"reason\":\"REQUESTED_BY_CUSTOMER\"}" | jq
```

The fee comes back proportionally (160 of the 320), and the ledger postings reverse.

---

## Break it on purpose

This is the part worth spending time on. The acquirer simulator lets you trigger every
failure mode the resilience code was written for.

```bash
curl -s http://localhost:8085/acquirer/v1/test_cards | jq
```

| `payment_method_id` | What happens | What it teaches |
|---|---|---|
| `pm_card_visa` | approved | the happy path |
| `pm_card_declined` | declined | a 402 that does **not** trip the circuit breaker |
| `pm_card_insufficient_funds` | declined | decline codes reaching the client |
| `pm_card_slow` | approved after 3.5s | the slow-call threshold counts slow as failed |
| `pm_card_timeout` | hangs 30s | read timeout → retry → fallback → 503 |
| `pm_card_error` | HTTP 500 | retry, then the circuit opens |
| `pm_card_flaky` | fails twice, then works | retry actually recovering |

**Try this.** Confirm six payments with `pm_card_error`, then one with `pm_card_visa`:

```bash
curl -s http://localhost:8082/actuator/health | jq '.components.circuitBreakers'
```

The breaker is `OPEN`, and the good card now fails instantly with a 503 instead of waiting.
Fifteen seconds later it goes `HALF_OPEN`, lets a probe through, and closes. That is a
cascading failure being prevented, live.

**Then try declines.** Confirm ten payments with `pm_card_declined`. The breaker stays
`CLOSED` — declines are business outcomes, not acquirer failures. Getting this distinction
wrong means a wave of expired cards takes down payments for every merchant. See the
`ignore-exceptions` block in `payment-service/src/main/resources/application.yml`.

### Idempotency

Send the same request twice with the same key:

```bash
KEY=$(uuidgen)
for i in 1 2; do
  curl -s -X POST http://localhost/v1/payment_intents \
    -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: $KEY" \
    -H 'Content-Type: application/json' \
    -d '{"amount":2500,"currency":"EUR"}' | jq -r .id
done
```

The same `pi_...` comes back twice. One payment exists, not two.

Now reuse that key with a *different* body and you get `409 idempotency_conflict` — because
silently replaying the old response would ignore what you actually asked for.

### Rate limiting

```bash
for i in $(seq 1 200); do
  curl -s -o /dev/null -w '%{http_code} ' http://localhost/v1/payment_intents \
    -H "Authorization: Bearer $TOKEN"
done; echo
```

`200`s turn into `429`s once the Redis token bucket empties. Because the bucket lives in
Redis rather than in each gateway's memory, the limit holds no matter how many gateway
replicas you scale to.

### Webhooks

```bash
curl -s -X POST http://localhost/v1/webhook_endpoints \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"url":"http://webhook-receiver:8080/hook","enabled_events":["*"],
       "description":"local test"}' | jq
```

Then confirm a payment and watch it arrive, signed:

```bash
docker compose logs -f webhook-receiver
```

The `Paykit-Signature` header is `t=<timestamp>,v1=<hmac>`. The timestamp is *inside* the
signed value, which is what stops a captured request being replayed forever.
[`docs/WEBHOOKS.md`](docs/WEBHOOKS.md) has verification code.

---

## Where the interesting code is

| If you want to understand… | Read |
|---|---|
| Why money is never a `double` | `common-lib/.../money/Money.java` |
| Compile-time-exhaustive event handling | `common-lib/.../event/PaymentEvent.java` |
| Why a payment can't be charged twice | `payment-service/.../domain/PaymentIntentStatus.java` |
| Retries that don't double-charge | `payment-service/.../idempotency/IdempotencyAspect.java` |
| Never losing an event on a crash | `payment-service/.../outbox/OutboxEvent.java` |
| Two pods sharing work with no coordinator | `payment-service/.../outbox/OutboxEventRepository.java` |
| Not holding a DB lock across a network call | `payment-service/.../service/PaymentTransactions.java` |
| Circuit breaker, retry, timeout, bulkhead | `payment-service/.../acquirer/AcquirerClient.java` |
| Double-entry bookkeeping | `ledger-service/.../service/LedgerPostingService.java` |
| Exactly-once *effect* from at-least-once delivery | `ledger-service/.../domain/ProcessedEvent.java` |
| Stopping header-spoofed tenant impersonation | `api-gateway/.../filter/AuthenticationFilter.java` |
| HMAC signing and replay protection | `webhook-service/.../service/WebhookSignature.java` |
| SSRF in merchant-supplied URLs | `webhook-service/.../service/UrlValidator.java` |

---

## Documentation

- [`docs/LEARNING-GUIDE.md`](docs/LEARNING-GUIDE.md) — every Java and Spring concept, mapped to where it is used and why
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — the system design and the trade-offs behind it
- [`docs/API.md`](docs/API.md) — endpoint reference
- [`docs/WEBHOOKS.md`](docs/WEBHOOKS.md) — signature verification, for merchants

Interactive API docs are served per service: <http://localhost:8082/swagger-ui.html>.

---

## Honest limitations

This is a teaching codebase. It is architecturally real, but these are deliberately out of scope:

- **No PCI compliance.** Card numbers never appear; the acquirer is simulated. Handling real
  PANs pulls you into a certification process this project does not model.
- **Secrets in config files.** The JWT signing key and webhook secrets sit in `application.yml`
  and environment variables. Production needs a secret manager and key rotation.
- **Auth-service registration is open.** Anyone can create a merchant. Real onboarding is
  gated behind KYC/AML checks.
- **Refunds settle instantly.** Card-network refunds take days; a real implementation leaves
  them `PENDING` and updates on a network callback.
- **Stranded payments are detected, not resolved.** `PaymentReconciliationJob` finds payments
  stuck in `PROCESSING` and alerts. A production version would query the acquirer for the
  true status and settle them. Guessing would be worse than reporting.
- **DNS rebinding.** `UrlValidator` blocks private address ranges, but a determined attacker
  controlling DNS can still race the check. The real fix is an egress proxy.
- **One Postgres instance, four databases.** Convenient locally; in production each service's
  database would be its own cluster.
