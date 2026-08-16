# Architecture

## The request path

```
                    ┌────────────────────────────────────────────────┐
   client ─────────▶│ nginx :80        server-side load balancing     │
                    │ · per-IP rate limit (cheap, first)             │
                    │ · X-Forwarded-For, X-Request-Id                │
                    └───────────────────┬────────────────────────────┘
                                        │ least_conn
                    ┌───────────────────▼────────────────────────────┐
                    │ api-gateway  (WebFlux, N replicas)             │
                    │ · correlation id                               │
                    │ · authenticate → stamp X-Merchant-Id           │
                    │ · per-merchant rate limit (Redis token bucket) │
                    │ · circuit breaker per downstream               │
                    └───┬───────────┬───────────┬───────────┬────────┘
                        │           │           │           │
             lb://auth  │  lb://payment  lb://ledger  lb://webhook
                        │           │           │           │
                   ┌────▼───┐  ┌────▼─────┐ ┌───▼────┐ ┌────▼─────┐
                   │  auth  │  │ payment  │ │ ledger │ │ webhook  │
                   └────┬───┘  └──┬────┬──┘ └───▲────┘ └────▲─────┘
                        │         │    │        │           │
                        │         │    └───▶ acquirer       │
                        │         │       (card network)    │
                   ┌────▼─────────▼────┐        │           │
                   │    PostgreSQL     │        │           │
                   │ 4 databases       │        │           │
                   └───────────────────┘        │           │
                                 │              │           │
                        outbox poller           │           │
                                 │              │           │
                            ┌────▼──────────────┴───────────┴───┐
                            │ Kafka  payments.events.v1          │
                            │ partitioned by payment-intent id   │
                            └────────────────────────────────────┘
```

Everything is discovered through **Eureka** (`:8761`). Redis backs rate limiting and
idempotency locks. Prometheus scrapes `/actuator/prometheus` on every service.

---

## Why these service boundaries

Services are split by **what changes together and what must fail independently**, not by
technical layer. A "controller service" and a "repository service" would be a distributed
monolith with extra latency.

| Service | Owns | Why it is separate |
|---|---|---|
| `auth-service` | merchants, API keys | It is the only service storing a credential. A bug in refund logic should not have the key store in its blast radius. |
| `payment-service` | intents, charges, refunds | The money path. It must stay available even when the ledger or webhooks are broken. |
| `ledger-service` | double-entry books | Accounting must never block a payment. It also has a completely different scaling profile — write-heavy, read-rare. |
| `webhook-service` | delivery to merchants | It talks to servers we do not control. Its slowest dependency is the public internet; that must not be in the payment path. |
| `api-gateway` | edge concerns | Auth, rate limiting and routing implemented once instead of six times. |
| `acquirer-simulator` | the card network | Stands in for a third party, and lets us trigger failures on demand. |

**The test for a good boundary:** can this service be deployed, scaled and broken independently
of the others? If the ledger is down, payments still succeed — the events queue in Kafka and
the books catch up. That is the boundary earning its cost.

---

## The critical path, step by step

`POST /v1/payment_intents/{id}/confirm` is where money moves. Its design is the most
consequential in the system.

```
① IdempotencyAspect        reserve the key            [tx, REQUIRES_NEW, committed]
② beginConfirmation        lock row, → PROCESSING     [tx, committed]
③ acquirer.authorize       the slow, failure-prone bit  ← NO transaction open
④ applyApproval/Decline    → SUCCEEDED | FAILED
                           + charge row
                           + outbox event             [tx, committed]
⑤ IdempotencyAspect        store the response         [tx, REQUIRES_NEW]
⑥ OutboxPublisher (async)  drain outbox → Kafka
```

### Why the transaction is split around step ③

Doing all of it in one transaction would hold a database row lock for the entire duration of a
remote call — hundreds of milliseconds on a good day, the full timeout on a bad one. Under load
that exhausts the connection pool and makes the database's health depend on a third party's.

The cost of splitting is honest: **if the process dies between ③ and ④, the payment is stranded
in `PROCESSING`.** That state is visible, not lost, and `PaymentReconciliationJob` looks for
exactly it. A design that cannot fail this way usually just fails invisibly instead.

### Why the reservation (①) commits before the charge (③)

If the idempotency record were written only on success, the window between "charge the card"
and "remember that we did" would be wide open. Reserving first means a crash mid-charge still
leaves evidence that the request was seen.

---

## Consistency model

| Boundary | Guarantee | Mechanism |
|---|---|---|
| Within payment-service | **strong** — ACID | one Postgres transaction |
| payment-service → Kafka | **at-least-once** | transactional outbox |
| Kafka → ledger / webhooks | **eventual**, sub-second | consumer + dedup table |
| Across services | **eventual** | events, never distributed transactions |

There is no two-phase commit anywhere. It is unsupported across Postgres and Kafka, and where
it does exist it is a performance disaster and an availability liability.

**What this means in practice.** After a successful confirm, `GET /v1/payment_intents/{id}`
shows `succeeded` immediately, but `GET /v1/balance` may lag by a few hundred milliseconds.
That is a deliberate trade: the alternative is making the payment path fail when the ledger is
slow.

---

## Failure modes, and what happens

| Failure | Effect | Recovery |
|---|---|---|
| One payment-service instance dies | Eureka evicts it; gateway routes elsewhere | automatic |
| **All** payment-service instances die | gateway circuit breaker opens → fast 503 with `Retry-After` | automatic when they return |
| Acquirer slow | slow-call threshold trips the breaker; requests fail fast | half-open probe after 15s |
| Acquirer down mid-authorization | payment stranded in `PROCESSING` | detected by reconciliation job; **needs a human today** |
| Postgres down | writes fail; `connection-timeout: 3s` prevents thread pile-up | automatic |
| Redis down | rate limiting and idempotency locks degrade; **the DB unique constraint still prevents double charges** | automatic |
| Kafka down | outbox rows accumulate unpublished; payments unaffected | poller drains on recovery |
| Ledger down | events queue in Kafka; payments unaffected | consumer catches up from its offset |
| Merchant's webhook URL down | delivery retried with jittered backoff, then parked | auto-disabled after 50 consecutive failures |
| Duplicate Kafka delivery | dedup table rejects it | automatic |
| Two concurrent confirms | pessimistic row lock serialises; state machine rejects the second | automatic |
| Two concurrent partial refunds | row lock + `CHECK` constraint | automatic |

The interesting column is the last one. Almost everything recovers without intervention; the
one case that does not is called out honestly rather than papered over.

---

## Scaling

**Stateless services** — `api-gateway`, `payment-service`, `auth-service` hold no session state,
so they scale horizontally without sticky sessions or session replication.

```bash
docker compose up -d --scale payment-service=5 --scale api-gateway=3
```

**Background workers scale too.** The outbox publisher and webhook sender run on *every*
instance and use `FOR UPDATE SKIP LOCKED` to take disjoint batches. No leader election, no
distributed lock, no coordinator — adding a replica adds throughput.

**Kafka consumers** are capped by partition count, not replica count. Three partitions means at
most three useful `ledger-service` instances; a fourth sits idle.

**The database is the real ceiling.** Read replicas for the list endpoints and partitioning
`payment_intents` by month are the next steps, in that order.

---

## Data ownership

One database per service, and no service reads another's tables.

Sharing a schema is the most common way a microservice architecture quietly becomes a
distributed monolith: two services join each other's tables, and now neither can change its
schema, deploy independently, or scale separately.

The cost is real and paid deliberately: no cross-service joins, no cross-service transactions.
That is *why* the outbox pattern and eventual consistency exist here.

> One Postgres instance hosting four databases is a local convenience. In production each would
> be its own instance or cluster, so one service's load cannot affect the others.

---

## Security model

**Authenticate once at the edge; trust the network inside.**

The gateway verifies the credential and stamps `X-Merchant-Id`. Internal services do not
re-validate. That is only safe because of two rules:

1. Services are not routable from outside the cluster — the only way in is the gateway.
2. The gateway **strips** any client-supplied identity header before setting its own.

Skip rule 2 and anyone can send `X-Merchant-Id: acct_victim` and read another merchant's
payments. It is a one-line mistake with a catastrophic blast radius, which is why it has a
dedicated test (`AuthenticationFilterTest.stripsSpoofedMerchantHeader`).

**Where this model stops.** It is perimeter security. A compromised pod inside the network can
impersonate any merchant. Zero-trust would give each hop a signed, short-lived service token
(mTLS or a service JWT) — the natural next step, and out of scope here.

**Defence in depth elsewhere:** bcrypt for API keys, constant-time comparison for signatures,
404-not-403 to prevent enumeration, tenant filtering in the query itself rather than in a check
after it, and SSRF protection on merchant-supplied URLs.

---

## Technology choices

| Choice | Alternative considered | Why |
|---|---|---|
| Eureka | Consul, Kubernetes DNS | Client-side LB is the concept worth learning; in K8s you would drop it. |
| Spring Cloud Gateway | Kong, Envoy | Same language and ecosystem, so filters are readable Java. |
| Kafka | RabbitMQ | Retained, replayable log. A ledger must be rebuildable from the event history — a queue that deletes on ack cannot do that. |
| Postgres | MySQL | `jsonb`, partial indexes, `SKIP LOCKED`, real `TIMESTAMPTZ`. |
| Redis | in-memory cache | Rate limits and locks must be shared across replicas. |
| Outbox polling | Debezium CDC | 500ms latency is irrelevant here and needs no extra infrastructure. Choose CDC when the latency budget or write volume actually demands it. |
| WebFlux (gateway only) | servlet stack | A gateway is almost entirely I/O wait; a few event-loop threads multiplex thousands of in-flight requests. |
| Servlet + virtual threads (everywhere else) | WebFlux everywhere | Blocking code is easier to read and debug, and Java 21 virtual threads remove most of the reason to go reactive. |

---

## What would change for production

1. **Secrets** — a secret manager with rotation, not `application.yml`.
2. **TLS everywhere** — mTLS between services, real certificates at the edge.
3. **Eureka HA** — 2–3 peer-aware nodes; the registry is currently a single point of failure.
4. **Kafka replication** — `replication.factor=3`, `min.insync.replicas=2`.
5. **Database HA** — primary/replica with automated failover, PITR backups.
6. **Distributed tracing** — OpenTelemetry, so the correlation id becomes a real span tree.
7. **Acquirer reconciliation** — resolve stranded payments automatically instead of alerting.
8. **PCI scope** — tokenisation at the edge; card data must never reach these services.
9. **Kubernetes** — replace Eureka with the K8s registry and nginx with an Ingress; keep
   everything else.
