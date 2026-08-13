# 09 — Event processing, Kafka and Redis

Concepts only. Enough to hold a design conversation; nothing about running a cluster.

Everything here is implemented in `partner-send` and tested against a **real broker running
in-process** — `mvn test -Dtest=KafkaIntegrationTest` needs no Docker.

---

## Part 1 — Kafka

### The mental model

Kafka is **not** a queue. That single misconception causes most wrong answers.

A queue hands each message to one consumer and deletes it. Kafka is a **distributed, append-only
log**: messages are written to the end, kept for a retention period, and consumers track their
own position (an *offset*) in it. Nothing is deleted on read.

Three consequences worth stating:

- **Multiple independent consumers** can read the same events for different purposes. Our
  read-model builder, a fraud engine and an analytics pipeline all consume `transfer-events`
  without knowing about each other.
- **Replay is free.** Reset the offset and reprocess history — how you rebuild a corrupted read
  model, or backfill a new one you just wrote.
- **Order is a property of the log**, not of delivery attempts.

### The five terms

| Term | What it is | The bit that matters |
|---|---|---|
| **Topic** | A named stream | `transfer-events` |
| **Partition** | An ordered shard of a topic | **Ordering is guaranteed only within a partition** |
| **Key** | Determines the partition, by hash | Same key → same partition → ordered |
| **Consumer group** | Consumers sharing the work | Each partition goes to exactly one member |
| **Offset** | A consumer's position | Committed after processing → at-least-once |

### The three questions you'll actually be asked

**"How do you guarantee ordering?"**

> *"Partition by the entity the ordering matters for. We key on the transfer id, so every event
> for one transfer lands on one partition and stays ordered, while different transfers spread
> across all partitions and scale out. Global ordering would mean a single partition and a
> single consumer — no scaling, to buy a guarantee the domain doesn't need."*

`KafkaIntegrationTest.keyingByAggregateIdPreservesPerTransferOrdering` sends six records with
one key and asserts they all land on the same partition.

**"How do you scale consumers?"**

> *"Add members to the consumer group — Kafka assigns partitions across them. The ceiling is
> the partition count: with three partitions, a fourth consumer sits idle. So partition count
> is a capacity decision made up front, and it's awkward to increase later because it changes
> which partition a key hashes to, which breaks the ordering guarantee for keys in flight."*

That last clause is the senior detail. Most people know you can't have more consumers than
partitions; fewer know *why* repartitioning is disruptive.

**"Doesn't Kafka give exactly-once?"**

> *"Only within Kafka — transactional reads and writes where the source and sink are both
> Kafka topics. The moment your side effect is an external payment or a database write, the
> guarantee doesn't reach it. In practice you take at-least-once delivery and make the consumer
> idempotent, which gives exactly-once effect."*

### Consumer idempotency — the part that matters

At-least-once means duplicates are **guaranteed**, not hypothetical. A consumer can apply an
event and crash before committing its offset; Kafka redelivers.

So consumers keep a record of processed event ids and skip repeats. See
`TransferEventProcessor`. The critical detail, which is where most implementations go wrong:

> **The dedup marker and the side effect must commit in the same transaction.**

If you record "processed" first and crash before applying, the update is lost forever — the
redelivery is skipped because it's already marked. Apply first and crash before recording, and
you apply twice. It's the dual-write problem again, one level down. Here it has an easy answer,
because both writes go to the same database: one transaction.

`ConsumerIdempotencyTest` makes this visible by maintaining a **counter**. Process the same
event twice and the total would be £500 instead of £250 — a read model that is quietly,
permanently wrong in a way no amount of staring at the transfers table reveals.

### Poison messages and dead-letter topics

A message that can never be processed — malformed payload, a schema you don't understand —
must not be retried forever, because it **blocks every message behind it on that partition**.

Route it to a dead-letter topic and alert. Spring does this with
`DefaultErrorHandler` + `DeadLetterPublishingRecoverer`.

The judgement call is classification, and it's the same distinction as retryable vs
non-retryable HTTP failures in §03: transient (broker unavailable, database deadlock) → retry;
deterministic (unparseable, unknown schema) → dead-letter immediately. Retrying a deterministic
failure is waste that also stalls the partition.

### Schema evolution

Producers and consumers deploy independently, so the schema is a contract between versions of
software that are never in sync.

- **Backward compatible changes only**: add optional fields, never remove or rename, never
  change a type.
- **A schema registry** (Confluent's, or equivalent) enforces this at publish time rather than
  at 3am.
- **Consumers must tolerate unknown fields** — they will receive events from a newer producer.

Worth one sentence in a design round: *"Events are a public API with a longer lifetime than
your REST endpoints, because the log keeps old ones around. I'd treat schema changes with the
same discipline as a partner-facing API change."*

### What we built

```
TransferService ──▶ outbox table ──▶ OutboxPublisher ──▶ Kafka(transfer-events)
   (one tx)                            (at-least-once)     key = transferId
                                                                  │
                                                                  ▼
                                                    TransferEventListener
                                                          (transport)
                                                                  │
                                                                  ▼
                                                    TransferEventProcessor
                                              (dedup + effect, one transaction)
                                                                  │
                                                                  ▼
                                                       partner_volume read model
```

| File | Shows |
|---|---|
| `outbox/KafkaEventPublisher` | Keying for ordering; blocking on ack so a failed send retries |
| `consumer/TransferEventListener` | Transport at the edge; exceptions propagate so the error handler can act |
| `consumer/TransferEventProcessor` | Idempotent apply; dedup marker and effect in one transaction |
| `consumer/ProcessedEvent` | The dedup table |
| `consumer/PartnerVolume` | A read model — CQRS in its un-grand, useful form |

---

## Part 2 — Redis

Smaller topic than people expect. Four uses, and the interesting question in each.

### 1. Caching

The obvious one. The interesting parts are the failure modes:

- **Cache stampede** — a hot key expires and a thousand requests all miss and hit the database
  simultaneously. Fix with a short lock so one request refills, or probabilistic early
  expiry. This is §03's thundering herd wearing a different hat.
- **Invalidation** — TTL is simple and eventually right; explicit invalidation is precise and
  easy to get wrong. Prefer TTL unless correctness demands otherwise.
- **What not to cache**: anything where stale data is a correctness problem. A balance, for
  instance.

### 2. Rate limiting

Very relevant here — you're the API provider, and §04 makes the point that partners retry
badly. You need a per-partner limit that holds across all your instances, which means shared
state, which means Redis.

**Token bucket** is the usual choice: tokens refill at a steady rate, each request takes one,
empty bucket means 429. It permits short bursts, which real traffic has, unlike a fixed window.

The detail to know: **do it in a Lua script.** Check-then-decrement across two round trips is a
race — the same check-then-act failure as §02's idempotency. Redis runs Lua atomically, which
closes it.

### 3. Distributed locks — and the honest answer

Asked often, usually as a trap.

> *"You can build one with `SET key value NX PX ttl`, and it's fine for things where a rare
> double-execution is merely wasteful — say, stopping two instances running the same cron job.
> But it isn't safe for correctness. The lock can expire while you still think you hold it —
> a GC pause is enough — and now two processes are in the critical section. The Redlock
> algorithm exists to address this and is genuinely contested among distributed-systems people.*
>
> *So for anything involving money I wouldn't use a distributed lock at all. I'd use the
> database: a unique constraint, or optimistic locking with a version column. Correctness
> belongs where the transaction is."*

That answer is much stronger than describing Redlock confidently, because the real signal is
knowing when *not* to reach for a distributed lock.

### 4. Idempotency key storage

Tempting — Redis is fast and has TTLs built in, which fits the 24-hour retention from §02.

The catch: your idempotency claim needs to be transactional with the work it protects. If the
key lives in Redis and the transfer lives in Postgres, you've reintroduced the dual-write
problem on the exact mechanism that exists to prevent duplicate payments.

> *"I'd keep idempotency keys in the same database as the transfers, so the claim and the work
> are one transaction. Redis would be a reasonable fast-path cache in front of it, but not the
> system of record. For payments I'd rather pay the latency than lose the atomicity."*

### Redis in one line

*Great for things you can afford to lose (cache, counters, rate-limit buckets); wrong for
things you can't (money, the record of whether a payment happened).*

---

## Quick self-test

1. Is Kafka a queue? → No. An append-only log; consumers track their own offset.
2. Where is ordering guaranteed? → Within a partition. Key by the entity that needs it.
3. What caps consumer parallelism? → The partition count.
4. Why is repartitioning disruptive? → Keys re-hash to different partitions, breaking ordering.
5. Does Kafka give exactly-once? → Only Kafka-to-Kafka. Not to an external side effect.
6. What must be atomic in a consumer? → The dedup marker and the effect.
7. What do you do with a poison message? → Dead-letter it; retrying blocks the partition.
8. Is a Redis lock safe for money? → No. Use the database's constraints.
9. Where do idempotency keys belong? → The same database as the work they protect.
