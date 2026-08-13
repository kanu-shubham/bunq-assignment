# 07 — Rapid-fire question bank

For the last 48 hours. Cover the right column and work down. If an answer doesn't come in
about fifteen seconds, go back to the section referenced.

---

## Distributed systems

| Question | Answer |
|---|---|
| What does a timeout tell you? | Nothing about whether the work happened. It's an ambiguity, not a failure. |
| Can you get exactly-once delivery? | No. At-least-once delivery + idempotent consumer = exactly-once *effect*. |
| Why can't you write to the DB and publish to Kafka atomically? | Dual-write problem — two systems, two failure modes. Fix: transactional outbox. |
| Why not use XA / 2PC? | Needs 2PC support everywhere, holds locks across the network, coordinator crash leaves rows in doubt. |
| What does an idempotency key identify? | The client's *intent*, so every retry of one logical payment shares it. |
| Why can't check-then-insert work? | Race: two requests both read "absent", both insert. Only a DB constraint can arbitrate. |
| Why hash the request body? | Catches a client reusing a key for a different payment. Otherwise you return the wrong response as a success. |
| How long do you keep idempotency keys? | ~24h (Stripe's choice). Covers sane retry windows without unbounded growth. |
| What if you crash between claim and complete? | Key stranded IN_PROGRESS. Reaper releases it, with a threshold longer than the work's worst case. |
| Do you need global ordering? | Almost never. Per-entity ordering via partitioning by entity id. |
| Global ordering costs what? | A single writer, so no horizontal scaling. |
| How do consumers handle out-of-order events? | Version numbers, and a state machine that rejects impossible transitions. |
| CAP, precisely? | On *partition*, choose availability or consistency. Says nothing about normal operation. |
| Better framing? | PACELC: on Partition A-or-C; Else Latency-or-Consistency. |
| Where does a ledger sit? | CP. Refusing is annoying; double-spending isn't recoverable. |
| Optimistic vs pessimistic locking? | Optimistic: version column, loser retries. No locks, no deadlocks. Right when conflicts are rare. |
| What's a saga? | Local transactions each with a compensating action. |
| Compensation vs rollback? | You can't un-send a payment. You send a return — a new, visible transaction. |
| Orchestration or choreography for payments? | Orchestration. When a regulator asks what happened, you need one place that knows. |
| Queue growing — options? | Bounded queue + shed with 429/503. Never silently accept work you can't do. |
| Little's Law? | L = λW. In-flight = arrival rate × time in system. |

## Fault tolerance

| Question | Answer |
|---|---|
| Why is slow worse than down? | Down fails fast. Slow holds threads until the pool exhausts and everything dies. |
| What must every network call have? | A timeout. Defaults are usually infinite. |
| How do you pick one? | Downstream p99 + headroom. Must shrink going down the stack. |
| What do you never retry? | Deterministic failures: 400, 401, 403, 422. |
| Why jitter? | Backoff alone synchronises the herd — everyone retries at the same instant and re-kills the service. |
| Why small retry budgets? | They multiply: 3 services × 3 attempts = 27 requests at the bottom. |
| Circuit breaker states? | CLOSED → OPEN → HALF_OPEN → CLOSED. |
| Why HALF_OPEN? | Probes recovery without handing a fragile service the whole backlog at once. |
| Why `minimumNumberOfCalls`? | Stops the breaker tripping on tiny samples during quiet periods. |
| Should slow calls count as failures? | Yes. An 8-second response is functionally down. |
| Should business rejections trip it? | No. The partner is healthy; one partner's bad data would cut off everyone. |
| What does a bulkhead protect? | Everything *else* — it stops one dependency draining a shared pool. |
| Decorator order? | Bulkhead(Retry(CircuitBreaker(TimeLimiter(call)))). |
| Why timeout innermost? | Per-attempt budget. Outside the retry, one slow call eats everything and retries never run. |
| Why retry outside the breaker? | Breaker sees every attempt, trips fast, and then short-circuits the remaining retries. |
| What bounds *total* request time? | Not per-attempt timeouts — deadline propagation from the caller. |
| When is a fallback wrong? | Payments. Never fabricate success; return 503 + Retry-After. |
| Liveness vs readiness? | "Restart me" vs "can I serve now". Conflating them turns a DB blip into a mass restart. |
| Alert on what? | Symptoms (payments failing), not causes (CPU 80%). |
| Why percentiles not averages? | Means hide everything. At scale, p99 is somebody's every request. |

## Java / JVM / Spring

| Question | Answer |
|---|---|
| Checked vs unchecked exceptions? | Checked must be declared and handled. Modern code prefers unchecked — checked ones don't compose with lambdas. |
| What's a record? | Immutable data class; constructor, accessors, equals/hashCode/toString generated. Accessors have no `get` prefix. |
| Sealed interface? | Closed set of implementations → exhaustive switches. Java's discriminated union. |
| `equals`/`hashCode` rule? | Override together. Unequal hashCodes for equal objects breaks every hash collection. |
| What are virtual threads? | JVM-scheduled threads (Java 21). Millions can exist; blocking is cheap again, so reactive is less necessary. |
| Virtual thread caveat? | `synchronized` can pin one to its carrier thread; prefer `ReentrantLock` in hot paths. |
| Thread-safe map? | `ConcurrentHashMap`. A shared plain `HashMap` can corrupt under concurrent resize. |
| Simplest concurrency strategy? | Immutability. An object that can't change is automatically thread-safe. |
| Why constructor injection? | Explicit dependencies, immutable object, testable with plain `new`. |
| Biggest `@Transactional` trap? | Self-invocation. A call within the same class bypasses the proxy and the annotation does nothing. |
| Which exceptions roll back by default? | Unchecked only. Checked ones commit unless you set `rollbackFor`. |
| What's `REQUIRES_NEW` for? | An independent transaction that commits immediately — how the idempotency claim becomes visible to rivals. |
| What is `open-in-view` and why disable it? | Holds a DB connection for the whole request including serialisation. Exhausts the pool under load. |
| JPA `save()` gotcha with assigned IDs? | Non-null id → treated as detached → `merge()` → duplicate INSERT becomes a silent UPDATE. Fix: implement `Persistable`. |
| What does `@Version` give you? | Optimistic locking — `WHERE version = ?`; the second writer gets `OptimisticLockException`. |
| N+1 query problem? | One query for the list, one per item. Fix with a join fetch or `@EntityGraph`. |

## Payments

| Question | Answer |
|---|---|
| Why never `double` for money? | 0.1 has no exact binary form; 0.1 + 0.2 ≠ 0.3. |
| How do you store £12.34? | `1234` minor units + currency. |
| Which currencies break "× 100"? | JPY (0 decimals), KWD/BHD/JOD (3). |
| Why not JSON numbers for amounts? | Parsers make them doubles; 10.10 arrives as 10.099999999999999. Send a digit string. |
| What is double-entry? | Every transaction ≥ 2 entries summing to zero. Money is conserved, and that's checkable. |
| Fixing a ledger mistake? | Reversing entry. Never update or delete — the audit trail is the product. |
| Where does balance live? | Derived from entries; cached for speed, but entries are truth. |
| Is SETTLED terminal? | No. Returns arrive days later. |
| Most dangerous state? | SUBMITTED — outcome genuinely unknown, must be reconciled not inferred. |
| Worst reconciliation break? | In theirs, not ours: a payment you recorded as failed that actually moved money. |
| Why reconcile at all? | It's the only control that catches what your code got wrong, by comparing against the counterparty. |
| Why is compliance architectural? | Sanctions screening is synchronous, so its p99 is inside your API's p99. |
| Clearing vs settlement? | Exchanging instructions vs money actually moving. |
| Nostro vs vostro? | Our account with them / their account with us. |
| ISO 20022? | The XML standard schemes are migrating to. `pain.001` initiates, `pacs.008` moves. |
| Wise's model? | Local float and local payouts on both sides, netted — instead of correspondent banking chains. |

---

## The five sentences

If you remember nothing else walking in:

1. *"A timeout is an ambiguity, not a failure — you don't know whether it happened."*
2. *"At-least-once delivery plus an idempotent consumer gives exactly-once effect. Exactly-once
   delivery isn't available over a network."*
3. *"You can't atomically write to a database and publish to a broker. That's the dual-write
   problem, and the answer is a transactional outbox."*
4. *"Retry only what's retryable, with exponential backoff and jitter, behind a per-dependency
   circuit breaker — because backoff without jitter just synchronises the herd."*
5. *"Money is counted, not measured: integer minor units, double-entry, append-only,
   corrections by reversal."*

---

## The evening before

- Re-read §05 and say the design out loud once, to a timer.
- Skim the five sentences above.
- Have your six stories in mind — one line each is enough to recall them.
- Have your questions for them written down.
- **Don't learn anything new.** Marginal value is negative; sleep is not.
