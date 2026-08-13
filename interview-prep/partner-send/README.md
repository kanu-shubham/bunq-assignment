# partner-send

A miniature payment-initiation service — the domain of the *Send for Partners* role, shrunk to
something you can read in an evening and run in ten seconds.

It exists to make the interview topics concrete. Every pattern in the guide has working,
tested code here, and the class comments carry the reasoning you'd give out loud.

```bash
mvn test              # 47 tests, ~60s
mvn spring-boot:run   # http://localhost:8080, no broker needed
```

Java 21, Spring Boot 3.5, H2 in-memory, Resilience4j, Spring Kafka.

The Kafka path is behind the `kafka` profile, so the default run needs no broker. The
integration tests start one **in-process** via `@EmbeddedKafka` — no Docker required.

---

## Try it

```bash
# First request — 202 Accepted
curl -i -X POST http://localhost:8080/v1/partners/acme-bank/transfers \
  -H "Idempotency-Key: demo-1" -H "Content-Type: application/json" \
  -d '{"partnerReference":"ACME-1","amountMinorUnits":"25000",
       "currency":"GBP","beneficiaryIban":"GB33BUKB20201555555555"}'

# Same key again — identical body, plus `Idempotent-Replay: true`. No new transfer.
# (repeat the exact command above)

# Same key, different amount — 422 idempotency_key_reuse
curl -i -X POST http://localhost:8080/v1/partners/acme-bank/transfers \
  -H "Idempotency-Key: demo-1" -H "Content-Type: application/json" \
  -d '{"partnerReference":"ACME-1","amountMinorUnits":"9900000",
       "currency":"GBP","beneficiaryIban":"GB33BUKB20201555555555"}'
```

Watch the log ~500ms after the first request and you'll see the outbox poller publish
`transfer.received`.

**Expected noise:** you will see `SQL Error: 23505 — Unique index or primary key violation` in
the log on the second request. That is Hibernate reporting the duplicate-key INSERT that the
idempotency logic deliberately provokes and catches. It's the mechanism working, not a bug.

To watch the Kafka path instead of the logging stand-in, start a broker and run with
`--spring.profiles.active=kafka`. The tests do this for you with an in-process broker:
`mvn test -Dtest=KafkaIntegrationTest`.

The H2 console is at `/h2-console` (JDBC URL `jdbc:h2:mem:partnersend`, user `sa`, no password)
if you want to look at `idempotency_record` and `outbox_event` directly.

---

## What's here, and what it demonstrates

| Area | Code | Demonstrates |
|---|---|---|
| **Money** | `domain/Money.java` | Integer minor units, per-currency scale, no floats |
| **Lifecycle** | `domain/TransferStatus.java`, `Transfer.java` | Explicit state machine; `@Version` optimistic locking |
| **Idempotency** | `idempotency/` | Claim–work–complete over a PK constraint; the JPA `Persistable` trap |
| **Dual writes** | `service/TransferService.java`, `outbox/` | Transactional outbox; at-least-once delivery |
| **Resilience** | `partner/ResilientPartnerBankClient.java`, `config/ResilienceConfig.java` | Timeout, retry+jitter, circuit breaker, bulkhead, and their nesting order |
| **API contract** | `api/` | Idempotency outcomes as HTTP status codes; retryable vs not |
| **Event processing** | `outbox/KafkaEventPublisher.java`, `consumer/` | Partitioning for ordering; consumer idempotency; read models; dead-lettering |

### Reading order

1. `domain/Money.java` — gentle, and the reasoning is all in the comments.
2. `domain/TransferStatus.java` — the state machine.
3. `api/TransferController.java` — the protocol as HTTP.
4. `idempotency/IdempotencyService.java` — the claim protocol; sealed interfaces.
5. `outbox/OutboxPublisher.java` — at-least-once, and why duplicates are expected.
6. `partner/ResilientPartnerBankClient.java` — the hard one. Read the class comment twice.
7. `consumer/TransferEventProcessor.java` — consumer idempotency, and why the dedup marker and the effect must share a transaction.

---

## The tests are the point

47 tests, and each is written to answer an interview question rather than to chase coverage.

| Test | Question it answers |
|---|---|
| `MoneyTest.floatingPointCannotRepresentMoney` | Why never `double`? |
| `MoneyTest.bigDecimalFromDoubleIsStillWrong` | Why `new BigDecimal(0.1)` doesn't save you |
| `TransferStateMachineTest.settledCanBeReturned` | Why isn't `SETTLED` terminal? |
| `IdempotencyApiTest.replayIsByteIdentical` | What does a client get on retry? |
| `IdempotencyApiTest.keyReuseWithDifferentBodyIsRejected` | Why hash the body? |
| `IdempotencyApiTest.inFlightDuplicateIsToldToRetry` | What if a duplicate arrives mid-flight? |
| **`ConcurrentIdempotencyTest.onlyOneWinnerUnderConcurrency`** | **16 threads, one key — what stops a double payment?** |
| `OutboxTest.rollbackLosesBoth` | Why is the outbox atomic and a `kafka.send()` isn't? |
| `OutboxTest.failedPublishIsRetried` | What happens when the broker is down? |
| `ResiliencePatternsTest.doesNotRetryRejections` | Which failures must never be retried? |
| `ResiliencePatternsTest.breakerOpensAndShedsLoad` | What does an open circuit actually do? |
| `ResiliencePatternsTest.rejectionsAreIgnoredByTheBreaker` | Why exclude business errors from the breaker? |
| `ResiliencePatternsTest.timeoutAppliesPerAttemptNotPerRequest` | Why is the timeout inside the retry? |
| `ResiliencePatternsTest.idempotencyKeyDoesNotChangeBetweenAttempts` | Why must the downstream key be stable? |
| `ResiliencePatternsTest.bulkheadLimitsConcurrency` | What does a bulkhead protect? |
| `ConsumerIdempotencyTest.duplicateDeliveryIsIgnored` | Why must consumers be idempotent? (the counter makes it visible) |
| `ConsumerIdempotencyTest.poisonMessageIsRejectedNotRetried` | What do you do with a message that can never succeed? |
| `KafkaIntegrationTest.keyingByAggregateIdPreservesPerTransferOrdering` | How do you get ordering without killing scalability? |
| `KafkaIntegrationTest.redeliveryIsDeduplicated` | What happens when the broker delivers twice? |

### A bug this codebase actually had

`ConcurrentIdempotencyTest` failed on the first run: **15 of 16 threads acquired the same key.**

The cause is a genuine JPA trap. Spring Data's `save()` picks `persist()` vs `merge()` by
checking whether the `@Id` is null. `IdempotencyRecord` assigns its own id, so `save()` always
concluded "detached" and called `merge()` — and `merge()` on an existing key is an **UPDATE**,
not a failed INSERT. No constraint violation, no exception, and each thread cheerfully
overwrote the previous claim.

The fix is `implements Persistable<String>` with an `isNew()` backed by a transient flag, which
forces `persist()`. The class comment on `IdempotencyRecord` explains it in full.

Worth knowing as a story, not just a fact: it's silent, it only appears under concurrency, and
a test that merely called the endpoint twice in sequence would have passed. It's a good
concrete answer to "tell me about a bug you found" or "how do you know your tests are any good".

---

## Deliberate simplifications

Called out so you don't defend something the code doesn't do:

- **H2 in-memory, `ddl-auto: create-drop`.** Production would be Postgres with Flyway owning the
  schema and `validate` here.
- **The outbox poller has no `SKIP LOCKED`.** Single-instance demo. `OutboxRepository` documents
  the production query for running multiple pollers.
- **No real partner submission in the request flow.** `TransferService` deliberately stops at
  `RECEIVED` + event. The orchestration from `FUNDED` to `SUBMITTED` is described in
  §05 of the guide but not built.
- **No ledger.** Double-entry is covered in §04; implementing it would double the size of this
  for less marginal learning than the idempotency and resilience layers give you.
- **Resilience4j wired by hand** rather than via `@CircuitBreaker` annotations. Annotations are
  fine in production; here the point is that the composition order stays visible.
- **No auth.** Real partner APIs use mTLS plus request signing.
- **The Kafka consumer has no dead-letter topic wired up.** `TransferEventProcessor` classifies
  a poison message correctly and the test asserts it, but routing it to a DLQ (Spring's
  `DefaultErrorHandler` + `DeadLetterPublishingRecoverer`) is described in §09, not built.
- **The read model is a single counter table.** Enough to make duplicate processing visible,
  which is its only job here.
