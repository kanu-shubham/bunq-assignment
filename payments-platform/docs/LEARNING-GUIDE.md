# Learning guide

Every concept below appears in this codebase because it solves a real problem there — not as a
demo. Each entry points at the file where you can read it in context, and says what goes wrong
without it.

Suggested order: **Java core → Spring core → data → distributed systems → operations.**

---

## Part 1 — Java

### Records and value objects
📁 `common-lib/src/main/java/com/paykit/common/money/Money.java`

A `record` is a transparent carrier for immutable data. The compiler writes the constructor,
accessors, `equals`, `hashCode` and `toString`, which makes it exactly the right shape for a
value object — a thing where two instances with the same fields *are* the same thing.

The **compact constructor** runs before fields are assigned, so it is where invariants belong:

```java
public Money {
    Objects.requireNonNull(currency, "currency must not be null");
}
```

An object that fails validation is never constructed, so no downstream code has to defend
against a half-valid one.

> **The money rule.** Never use `double` for money. `0.1 + 0.2 != 0.3` in binary floating
> point. A ledger that drifts a fraction of a cent per transaction will not balance. Store
> integer minor units — cents, pence, yen. Test: `MoneyTest`.

### Enums with behaviour
📁 `common-lib/.../money/Currency.java`, `payment-service/.../domain/PaymentIntentStatus.java`

Enums are full classes. They carry state, expose methods, and — critically — make a closed set
of values something the compiler checks. `PaymentIntentStatus` goes further and owns its own
transition table:

```java
public boolean canTransitionTo(PaymentIntentStatus target) { ... }
```

The alternative is an `if` in whichever service happens to be doing the update, and those
`if`s drift. Here "can a succeeded payment be cancelled?" has one answer in one place.

> **Gotcha.** An enum constant cannot reference another constant in its own constructor —
> they are not initialised yet. That is why the successor sets are resolved lazily on first use.

### Sealed interfaces and exhaustive switches
📁 `common-lib/.../event/PaymentEvent.java` → used in `ledger-service/.../LedgerPostingService.java`

`sealed` fixes the set of implementations at compile time. The payoff is in the consumer:

```java
return switch (event) {
    case PaymentEvents.PaymentSucceeded s -> ...;
    case PaymentEvents.RefundSucceeded r  -> ...;
    case PaymentEvents.PaymentIntentCreated ignored -> List.of();
    case PaymentEvents.PaymentFailed ignored -> List.of();
    case PaymentEvents.PaymentCanceled ignored -> List.of();
};                                  // ← no default branch needed
```

Add a sixth event type and **the ledger stops compiling** until someone decides what it means
for the books. A `default -> ignore` branch would have silently dropped it, and you would find
out at a reconciliation months later.

### Generics and type erasure
📁 `common-lib/.../event/EventEnvelope.java` → consumed in `ledger-service/.../PaymentEventConsumer.java`

`EventEnvelope<T extends PaymentEvent>` is a bounded type parameter: any payment event, nothing
else, no casting at the call site.

At runtime the parameter is **erased** — `EventEnvelope<PaymentSucceeded>` and
`EventEnvelope<PaymentFailed>` are the same class. So this loses the payload type:

```java
mapper.readValue(json, EventEnvelope.class);                                  // ✗
mapper.readValue(json, new TypeReference<EventEnvelope<PaymentEvent>>() {});  // ✓
```

`TypeReference` is an anonymous subclass, and a *supertype's* generic information survives in
the class file. That trick is the standard erasure workaround across the whole ecosystem.
Test: `EventSerializationTest.genericEnvelopeNeedsATypeReferenceBecauseOfErasure`.

### Checked vs unchecked exceptions
📁 `common-lib/.../error/PlatformException.java`, `Exceptions.java`

Everything here extends `RuntimeException`, for two reasons:

1. A declined card is not something a controller can catch and repair, so forcing `throws`
   clauses through every layer adds noise without safety.
2. **Spring rolls back on unchecked exceptions by default.** A checked exception would
   *commit*, which surprises people regularly. `RefundService` relies on this: throwing aborts
   the refund and undoes the `recordRefund` that preceded it.

### ThreadLocal, and the leak it causes
📁 `common-lib/.../web/RequestContext.java`

Each thread sees its own value, so 200 concurrent requests keep 200 independent contexts. But
thread pools **reuse** threads: fail to clear it and the next request inherits the previous
one's data. `CorrelationIdFilter` clears it in a `finally` block, always.

### Virtual threads (Java 21)
📁 `payment-service/src/main/resources/application.yml` (`spring.threads.virtual.enabled`)

A request thread blocked on the acquirer for 200ms no longer pins an OS thread, so concurrency
is bounded by the downstream service rather than by the Tomcat pool size. `IdsTest` spins up
500 concurrent tasks with `Executors.newVirtualThreadPerTaskExecutor()` and it costs nothing.

> **Caveat.** `synchronized` blocks can still pin a carrier thread. Prefer `ReentrantLock` in
> hot paths.

### Utility classes and `MessageDigest.isEqual`
📁 `common-lib/.../util/Ids.java`, `webhook-service/.../service/WebhookSignature.java`

`String.equals` returns as soon as two bytes differ, so its runtime leaks how many leading
bytes were correct. Given enough attempts an attacker recovers a valid signature one byte at a
time. `MessageDigest.isEqual` always compares the full length. Use it for anything secret.

---

## Part 2 — Spring Boot

### Dependency injection, done properly
📁 every `@Service` in the project

Constructor injection, `final` fields, no `@Autowired` on fields. Two consequences: the object
cannot exist half-built, and a unit test can construct it by hand with fakes. `ApiKeyServiceTest`
does exactly that — no Spring context, milliseconds to run.

### Configuration properties
📁 `api-gateway/.../config/GatewayProperties.java`, `payment-service/.../config/PaymentProperties.java`

`@ConfigurationProperties` on a `record` beats `@Value` scattered around: settings are grouped,
immutable, IDE-completable, and `@Validated` makes a bad value fail at **startup** rather than
on the first request that happens to need it.

### Auto-configuration — how starters actually work
📁 `common-lib/.../config/CommonWebAutoConfiguration.java`
📁 `common-lib/src/main/resources/META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports`

Listing a class in that `.imports` file makes Boot evaluate it at startup with no service
importing anything. The `@ConditionalOn*` guards are what let one shared jar serve both the
servlet services and the reactive gateway — no `DispatcherServlet` on the classpath, no beans
contributed. This is the mechanism behind every `spring-boot-starter-*`.

### AOP, and the proxy trap that catches everyone
📁 `payment-service/.../idempotency/IdempotencyAspect.java`

At startup Spring replaces the controller bean with a **proxy**. Callers hold the proxy; it
runs the advice and then delegates. The controller contains not one line about idempotency.

> **The trap.** Because interception lives in the proxy, an *internal* call — one method of a
> bean calling another method of the same bean via `this` — bypasses it entirely. This applies
> to `@Transactional`, `@Cacheable` and `@Async` too. If an annotation "does not work", check
> for self-invocation first.
>
> 📁 `payment-service/.../service/PaymentTransactions.java` exists *specifically* to avoid
> this: the orchestrator in `PaymentIntentService` must call transactional methods on a
> different bean, or they would silently run with no transaction at all.

### `@Transactional` — propagation is not decoration
📁 `payment-service/.../idempotency/IdempotencyService.java`, `outbox/OutboxRecorder.java`

Three propagation modes, each chosen for a reason:

| Mode | Where | Why |
|---|---|---|
| `REQUIRED` (default) | most services | join the caller's transaction |
| `REQUIRES_NEW` | `IdempotencyService.reserve` | the reservation must be durable **before** the card is charged, so a crash mid-charge still leaves evidence. Joining the caller would roll it back with everything else. |
| `MANDATORY` | `OutboxRecorder.record` | **throws** if no transaction is active. Turns "someone recorded an event outside the business transaction" from a subtle correctness bug into an immediate failure. |

`readOnly = true` on queries lets the driver and Hibernate skip dirty checking and flushing.

### Global error handling
📁 `common-lib/.../web/GlobalExceptionHandler.java`

One `@RestControllerAdvice` handles the entire `PlatformException` family through polymorphism,
so no controller has a try/catch and a client never sees two error shapes. Unexpected exceptions
are logged with their stack trace and returned as a bare "internal error" plus the correlation
id — leaking a stack trace, SQL statement or class name to the internet is how attackers map
your system.

### Bean validation
📁 `auth-service/.../web/AuthDtos.java`, `payment-service/.../web/PaymentDtos.java`

`@NotBlank`, `@Email`, `@Min`, `@Pattern` run **before** the controller body, so no business
method ever sees a blank email. Failures become a field-by-field 400.
Test: `AuthServiceIntegrationTest.validationFailuresAreReportedPerField`.

### DTOs, and why entities must never be returned
📁 `auth-service/.../web/AuthDtos.java`

Three reasons, all of which bite eventually:

1. **Leakage** — serialising `ApiKey` directly would publish `secretHash` to the internet.
2. **Coupling** — renaming a column would silently break every client.
3. **Lazy loading** — Jackson touching a lazy association outside a transaction throws
   `LazyInitializationException`, a 500 that only appears in production.

### Scheduling
📁 `payment-service/.../outbox/OutboxPublisher.java`

`fixedDelay`, not `fixedRate`. `fixedDelay` measures the gap *after* the previous run finishes;
with `fixedRate`, a run slower than the interval starts the next one immediately and they stack
up under exactly the load where that hurts most.

### Spring Security on an internal service
📁 `auth-service/.../config/SecurityConfig.java`

CSRF disabled, sessions `STATELESS`. Both are correct **here** and would be bugs elsewhere:
CSRF needs an ambient credential the browser attaches automatically (a cookie); this API uses
an `Authorization` header no browser sends on its own. Disabling CSRF on a cookie-authenticated
app is a serious vulnerability.

---

## Part 3 — Data

### JPA entity vs record
📁 `auth-service/.../domain/Merchant.java`

Entities are **mutable and identity-based** — the opposite of the value objects elsewhere. Two
`Merchant` instances are the same merchant if their ids match. That is why entities cannot be
records: Hibernate needs a no-arg constructor and mutable fields to hydrate them and track changes.

Note there is no `setStatus`. Every change goes through a named method (`activate`, `suspend`)
that validates first. If any caller can write any status, the state machine is decoration.

### Optimistic vs pessimistic locking
📁 `payment-service/.../domain/PaymentIntent.java` (`@Version`)
📁 `payment-service/.../repository/PaymentIntentRepository.java` (`findForUpdate`)

| | How | When |
|---|---|---|
| **Optimistic** (`@Version`) | `WHERE version = ?` on every UPDATE; zero rows matched → conflict | conflicts are rare; scales better; the default here |
| **Pessimistic** (`SELECT … FOR UPDATE`) | row locked until the transaction ends | losing the race means calling the card network twice |

Confirming a payment and refunding a charge are pessimistic. Everything else is optimistic.
A lost optimistic race surfaces as a retryable 409, not a silent lost update.

### The N+1 problem
📁 `auth-service/.../repository/ApiKeyRepository.java`

`FetchType.LAZY` on the association, plus an explicit `JOIN FETCH` at the query that needs the
related entity. Making the mapping `EAGER` instead "fixes" it by paying the cost everywhere,
including the queries that never touch the association.

### `open-in-view: false`
📁 every service's `application.yml`

On by default, and it should almost always be off. It keeps a database connection open for the
whole request including JSON serialisation, which hides N+1 queries and holds pool connections
while rendering.

### Flyway, not `ddl-auto: update`
📁 `*/src/main/resources/db/migration/V1__*.sql`

`ddl-auto` guesses. It adds columns, never drops them, never backfills data, and never writes
an index you did not model. Migrations are versioned, reviewed in the same pull request as the
code that needs them, and run identically in CI, Testcontainers and production. Every service
runs `ddl-auto: validate` — Hibernate refuses to start if the mapping and the schema disagree.

### Constraints as the last line of defence
📁 `payment-service/src/main/resources/db/migration/V1__create_payment_tables.sql`

```sql
CONSTRAINT charges_one_per_intent UNIQUE (payment_intent_id),
CONSTRAINT charges_refund_within_bounds CHECK (refunded_minor BETWEEN 0 AND amount_minor),
CONSTRAINT pi_succeeded_has_charge CHECK (status <> 'SUCCEEDED' OR charge_id IS NOT NULL)
```

Application validation can be bypassed by a migration, a script or a bug. The database cannot
be talked out of it. `charges_one_per_intent` is what makes a double charge *structurally*
impossible rather than merely unlikely.

### Index design
Same file. Three kinds, each matched to a query:

- **Composite** `(merchant_id, created_at DESC)` — column order matches the list endpoint, so
  it is an index scan rather than a table scan.
- **Partial** `WHERE status = 'PROCESSING'` — the reconciliation job touches a tiny fraction of
  rows, so the index stays small enough to live in memory.
- **GIN** on `jsonb` — lets a merchant find a payment by their own `order_id`.

### Connection pool sizing
📁 `application.yml` → `spring.datasource.hikari.maximum-pool-size`

Bigger is not better. Each connection is a Postgres backend process; a pool larger than the
database can serve just moves the queue from your app to the database, where it is harder to
see. `connection-timeout: 3000` means failing fast rather than piling threads on a dead DB.

---

## Part 4 — Distributed systems

### Idempotency
📁 `payment-service/.../idempotency/` (whole package)

**The problem.** A client sends `POST /v1/payment_intents`. The payment is created, then the
connection drops before the response arrives. The client retries — correctly. Without
protection, the customer is charged twice.

**The protocol.** Client sends `Idempotency-Key: <uuid>` and reuses it on every retry.

| Situation | Response |
|---|---|
| Same key, same body | replay the stored original response |
| Same key, **different** body | `409` — the key was reused for a different operation |
| Same key, still running | `409` — a duplicate is in flight |

**Two layers, and knowing which one is real:**

- Redis `SET key NX EX 60` rejects a concurrent duplicate in under a millisecond. *Advisory.*
- The unique constraint on `scoped_key` is what actually guarantees it. *Authoritative.*

> Whenever you see a distributed lock, ask what happens when the lock service fails. If the
> answer is "we double-charge", the lock is not a correctness mechanism and something else
> must be.

The key is scoped `merchantId + ":" + key` — a global key space would let one merchant's retry
replay another's response.

### The transactional outbox
📁 `payment-service/.../outbox/OutboxEvent.java`, `OutboxPublisher.java`

**The dual-write problem.** A payment succeeds; the row must be updated *and* an event
published. There is no good ordering:

```
save() ; kafka.send()   → crash between: money moved, ledger never hears. Books wrong.
kafka.send() ; save()   → crash between: ledger records a payment that never happened.
```

**The fix.** Write the event into the *same database, same transaction* as the business change.
One commit, atomic by construction. A poller then drains it to Kafka.

The guarantee is **at-least-once**: a crash between "sent" and "marked published" re-sends.
That is why every consumer deduplicates.

### `FOR UPDATE SKIP LOCKED`
📁 `payment-service/.../outbox/OutboxEventRepository.java`

The most interesting SQL in the codebase. Two instances poll the outbox every 500ms. Plain
`FOR UPDATE` would make instance B *wait* for A's batch — correct, but the poller is now
single-threaded and B burns a connection doing nothing. `SKIP LOCKED` says "give me unlocked
rows, ignore the rest", so both instances take disjoint batches with **no leader election, no
coordinator, no distributed lock**. The webhook sender uses the same trick.

### Exactly-once processing (which is not exactly-once delivery)
📁 `ledger-service/.../domain/ProcessedEvent.java`

Kafka delivers at least once. The same `payment_intent.succeeded` *will* arrive twice
eventually, and without protection the merchant is credited twice.

The event id is the primary key and the row is inserted **in the same transaction** as the
ledger postings. A duplicate hits the constraint, the transaction rolls back, and the consumer
acknowledges anyway because the work was already done.

> Exactly-once *effect* = at-least-once delivery + an idempotent consumer. There is no broker
> setting that replaces this.

### Kafka producer durability
📁 `payment-service/.../config/KafkaProducerConfig.java`

Every one of these is a default that must be changed to be safe:

- `acks=all` — with `acks=1`, a broker crash just after acknowledging loses the event silently.
- `enable.idempotence=true` — without it, "retries > 0" and "no duplicates" are mutually exclusive.
- `max.in.flight.requests.per.connection=5` — above 5, ordering guarantees silently disappear.

### Kafka consumer safety
📁 `ledger-service/.../config/KafkaConsumerConfig.java`

- `enable.auto.commit=false` — auto-commit advances offsets on a timer regardless of whether
  processing succeeded, so a crash silently skips records.
- `auto-offset-reset=earliest` — a new ledger deployment must read the whole history, not
  skip everything published before it started.
- **Dead-letter topic** — a consumer that throws does not advance its offset, so it re-reads
  the same record forever and blocks *every* message behind it on that partition. One bad row
  becomes an outage. Retry a few times, then park it in the DLQ and carry on.
- Concurrency is capped by **partition count**, not thread count.

### Circuit breaker, retry, timeout, bulkhead
📁 `payment-service/.../acquirer/AcquirerClient.java` + its `application.yml`

```
CircuitBreaker( Retry( TimeLimiter( call ) ) )
```

- **Timeout** — a call that never returns is worse than one that fails; it holds a thread and
  a connection forever.
- **Retry with jittered exponential backoff** — without jitter, every client that failed at the
  same moment retries at the same moment, and the thundering herd finishes off a service that
  was recovering.
- **Circuit breaker** — when failures stop being transient, retrying makes it worse. Opening
  fails fast *and* takes load off the struggling service.
- **Bulkhead** — caps concurrent acquirer calls, so a slow acquirer cannot consume every
  thread and take down endpoints that never touch it.

> **The distinction that matters most.** A decline is a *business answer*, not an acquirer
> failure. `ignore-exceptions` keeps `CardDeclinedException` out of the breaker's statistics.
> Get this wrong and a wave of expired cards trips the breaker and stops payments for everyone.

### The two kinds of load balancing
📁 `infra/nginx/nginx.conf` vs `api-gateway/.../config/RouteConfig.java`

| | Server-side (nginx) | Client-side (Spring Cloud LoadBalancer) |
|---|---|---|
| Who chooses | a dedicated proxy | the caller itself |
| Client needs | nothing | access to the registry |
| Extra hop | yes | no |
| Use when | the client is a browser or third party | the caller is your own service |

Rule of thumb: server-side at the edge, client-side internally. `lb://payment-service` is not a
hostname — it is a service id resolved through Eureka.

### Saga / eventual consistency
📁 the flow across `payment-service` → Kafka → `ledger-service`

Separate databases mean no cross-service transaction. The ledger is *eventually* consistent
with payments — typically under a second. The compensating action for a payment is a refund,
not a rollback, because the money really did move.

---

## Part 5 — Security

| Concern | File | The failure it prevents |
|---|---|---|
| Header spoofing | `api-gateway/.../AuthenticationFilter.java` | client sends `X-Merchant-Id: acct_victim` and reads another merchant's payments. The filter **strips** it before setting its own. |
| Secret storage | `auth-service/.../domain/ApiKey.java` | bcrypt, never plaintext. Two-part keys (`sk_test_PREFIX_secret`) solve the "you cannot look up a salted hash" problem. |
| Timing attacks | `ApiKeyService.verify`, `WebhookSignature.verify` | constant-time comparison; bcrypt is run even for unknown prefixes so response time does not reveal which prefixes exist. |
| Enumeration | `ApiKeyService.revoke`, `PaymentIntentRepository` | 404, never 403 — a 403 confirms the resource exists. |
| Replay attacks | `WebhookSignature` | the timestamp is *inside* the signed value, so it cannot be refreshed without the secret. |
| SSRF | `webhook-service/.../UrlValidator.java` | merchant-supplied URL pointed at `169.254.169.254` turns the sender into a proxy for cloud credentials. Redirects are not followed, for the same reason. |
| Tenant isolation | every repository | queries filter on `merchant_id` in the query itself, so forgetting the check is not possible. |
| Container hardening | `Dockerfile` | non-root user; JRE-only runtime image with no compiler or source. |

---

## Part 6 — Operations

### Metrics: counters, timers, gauges
📁 `payment-service/.../metrics/PaymentMetrics.java`

- **Counter** — only increases. Rates are derived at query time.
- **Timer** — count plus distribution. Percentiles are the point: a p99 of 8s matters even when
  the mean is 200ms, and the mean will never show it to you.
- **Gauge** — goes up and down. `paykit.payments.stuck` should be 0; alert on anything else.

> **The cardinality trap.** Every distinct tag combination is a separate time series. Tagging
> by currency (4 values) is fine. Tagging by merchant id or payment id creates millions and
> takes the monitoring system down. High-cardinality identifiers belong in logs and traces.

### Correlation ids
📁 `api-gateway/.../filter/CorrelationIdFilter.java` → `common-lib/.../web/CorrelationIdFilter.java`

One `POST /v1/payment_intents` touches nginx, the gateway, payment-service, Kafka, the ledger
and the webhook sender. Without a shared id you are grepping six services by timestamp and
guessing. The gateway mints it; everyone else propagates it into their logging MDC — including
the Kafka consumers, via the event envelope.

### Health checks and graceful shutdown
Liveness vs readiness probes are enabled on every service. `server.shutdown: graceful` plus
`spring.lifecycle.timeout-per-shutdown-phase` lets in-flight payments finish — a pod killed
mid-confirm is exactly how a payment gets stranded in `PROCESSING`.

### Docker layer caching
📁 `Dockerfile`

POMs are copied and dependencies downloaded *before* the source, so a code change does not
re-download the internet. Spring Boot's `layertools` then splits the fat jar so a code change
ships a few hundred KB instead of 60MB.

`-XX:MaxRAMPercentage=75`, not `-Xmx`: the JVM reads the *container's* limit. Without it a JVM
in a 512MB container sizes its heap for the host and gets OOM-killed with no stack trace.

---

## Part 7 — Testing

| Kind | Example | Runs in |
|---|---|---|
| Pure unit | `MoneyTest`, `ChargeRefundTest` | microseconds, no Spring |
| Mocked collaborators | `PaymentIntentServiceTest`, `LedgerPostingServiceTest` | milliseconds |
| Reactive | `AuthenticationFilterTest` (`StepVerifier`) | milliseconds |
| Integration | `PaymentFlowIntegrationTest` (Testcontainers) | seconds, needs Docker |

**Why Testcontainers and not H2.** H2 in "Postgres compatibility mode" is not Postgres: no
`TIMESTAMPTZ`, no partial indexes, different upsert syntax, different locking. A suite that
passes on H2 and fails on Postgres has tested the wrong database — and you find out in
production.

**Why the integration tests are gated.** `@Testcontainers(disabledWithoutDocker = true)` skips
them cleanly where no Docker daemon exists, so the build stays green rather than red for a
reason unrelated to the code.

Two tests worth reading for their own sake:

- `AuthenticationFilterTest.stripsSpoofedMerchantHeader` — the single most important security
  guarantee in the platform, pinned by a test.
- `LedgerPostingServiceTest` — every case ultimately asserts debits equal credits.

---

## Where to go next

Things this codebase deliberately leaves as exercises:

1. **Resolve stranded payments** — have `PaymentReconciliationJob` query the acquirer for the
   true status instead of only alerting.
2. **Distributed tracing** — add Micrometer Tracing + OpenTelemetry so the correlation id
   becomes a real span tree.
3. **Payouts** — move money from `MERCHANT_PAYABLE` to `PLATFORM_CASH` on a schedule. The
   account type already exists.
4. **Multi-currency settlement** — FX conversion is a genuinely hard ledger problem.
5. **Kubernetes** — swap Eureka for the Kubernetes service registry and nginx for an Ingress.
6. **CDC instead of polling** — replace the outbox poller with Debezium reading the WAL.
