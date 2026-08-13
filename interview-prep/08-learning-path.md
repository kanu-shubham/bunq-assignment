# 08 — What to learn, in what order

> *"Should we start with microservices, distributed systems, fault tolerance, event
> processing, Kafka, Redis and all?"*

**No.** That list is roughly the right set of topics in roughly the wrong order, and two of
the items on it are traps for someone in your position. Here is the order, and the reasoning.

---

## The principle behind the ordering

**Kafka and Redis are implementations. The interview tests the concepts underneath them.**

If you understand at-least-once delivery and idempotent consumers, you can discuss Kafka
credibly for twenty minutes. If you have memorised Kafka's rebalance protocol but can't
explain why duplicates happen, the first follow-up question exposes it.

It also works in your favour: concepts transfer. "How do you guarantee ordering?" has the same
answer whether the broker is Kafka, Pulsar or SQS FIFO. Tool knowledge doesn't transfer, ages
badly, and is the thing they least expect from a lead who is candid about being new to the JVM.

So the rule is: **learn the concept, then learn the tool as the concept's concrete answer.**
Never the reverse.

---

## The order

### 1. Java fluency — but only to *read* (2–3 days)

Not to write from scratch under time pressure. You need to follow a Spring Boot service in a
design discussion and have opinions about it.

Stop when you can read `TransferController` and `IdempotencyService` without looking anything
up. That's the bar. Chasing more is a poor use of the time you have.

**Do:** §01, then read the service in the order that file suggests.
**Skip:** streams/collectors depth, generics variance, the `Optional` API surface, build tooling.

### 2. Distributed systems concepts (3–4 days) ← **the highest-value block**

Idempotency, the dual-write problem, delivery semantics, ordering, consistency, sagas.

This is where the interview signal actually is. Every hour here is worth about three spent on
Kafka configuration. It is also the block that makes everything after it easy, because Kafka,
Redis and microservices are all just answers to questions posed here.

**Do:** §02, reading the code alongside it. Then §04 (payments) — it's short and it's your
differentiator.

### 3. Fault tolerance (2 days)

Timeouts, retries, backoff and jitter, circuit breakers, bulkheads. Directly named in the job
posting ("scalable and robust systems"), and it's the block with the most concrete code to
point at.

**Do:** §03, then break things in `ResiliencePatternsTest` — change `maxAttempts` to 1 and see
which tests fail, and why.

### 4. Event-driven and Kafka — **concepts only** (1–2 days)

Now it makes sense, because you know what problem it solves. Partitions, keys, consumer
groups, offsets, at-least-once, dead-letter topics, and why "exactly-once" is narrower than
the marketing implies.

**Do:** §09, and read `KafkaEventPublisher` / `TransferEventProcessor`. Run
`mvn test -Dtest=KafkaIntegrationTest` — it starts a real broker in-process.
**Skip:** broker operations. Replication factors, ISR tuning, partition rebalancing internals,
running a cluster. Nobody hires an Engineering Lead for that, and if it does come up, "I'd
lean on whoever runs our Kafka" is a fine answer.

### 5. Redis — narrowly (half a day)

Four uses, and the interesting question in each. §09 covers it. Half a day, genuinely.
**Skip:** the command surface, cluster mode, persistence tuning.

### 6. Microservices (half a day, and mostly unlearning)

Deliberately last, and deliberately small, because it is the most over-indexed topic in
interview prep and the one where confident wrongness is most common.

At lead level, "microservices" is an *organisational* question wearing a technical costume.
The useful content is: service boundaries follow team boundaries (Conway's Law), each service
owns its data, and every network hop you add is a new failure mode you now have to handle —
which is why the rest of this guide exists. Nobody senior wants to hear that microservices are
inherently better. A candidate who says *"I'd start with a well-structured monolith and extract
services where the team boundary or the scaling profile actually demands it"* sounds more
experienced than one who reaches for microservices by default.

---

## What that looks like as a schedule

Ten working days, if you have them:

| Days | Block | Done when you can… |
|---|---|---|
| 1–2 | Java fluency | Read `IdempotencyService` without looking things up |
| 3–5 | Distributed systems + payments | Explain the outbox and idempotency protocol out loud |
| 6–7 | Fault tolerance | Justify the decorator nesting order unprompted |
| 8 | Kafka + Redis concepts | Explain why keying by aggregate id gives you ordering |
| 8½ | Microservices | Argue *against* microservices convincingly |
| 9 | Design round rehearsal (§05) | Deliver it in 45 min, out loud, no notes |
| 10 | Leadership stories (§06) + question bank (§07) | Six stories, one line each, from memory |

**Compressed to five days:** do blocks 2, 3 and 9, and read §01 as needed when the code
confuses you. Blocks 4–6 become reading, not practice.

**Compressed to two days:** §05, §07, and the class comments in `IdempotencyService` and
`ResilientPartnerBankClient`. Nothing else.

---

## Exercises, in order of value

Reading is not learning. Each of these takes 20–60 minutes and is worth more than an hour of
articles.

**1. Break a test and predict the failure first.**
In `ResilienceConfig`, set `maxAttempts(1)`. Which tests fail? Write your prediction down
before running `mvn test`. Then set `minimumNumberOfCalls` to 1 and see the breaker trip on
noise. *This is the single highest-value exercise here.*

**2. Make the idempotency bug come back.**
In `IdempotencyRecord`, delete `implements Persistable<String>` and its `isNew()`. Run
`ConcurrentIdempotencyTest`. Watch 15 of 16 threads acquire the same key. Now you have felt
the failure mode rather than read about it — and you have an interview story.

**3. Add a status transition endpoint.**
`POST /v1/transfers/{id}/settle` that moves `SUBMITTED → SETTLED`. You'll have to touch the
controller, the service, a transaction and the state machine — the whole vertical slice. Then
make it idempotent, and notice that this one is naturally idempotent because the state machine
rejects the second call. That's the difference between *engineered* and *natural* idempotency,
and it's a good thing to be able to distinguish.

**4. Watch at-least-once happen.**
In `OutboxTest`, make the publisher throw on the first attempt only. Confirm the event is
retried and that `attempts` increments. Then make it succeed but crash before `markPublished`
— that's the duplicate the consumer's dedup table exists for.

**5. Write the design answer out longhand, once.**
§05, by hand, without looking. Then compare. The gaps you find are exactly what you'd have
fumbled live.

---

## What to actively skip

Time spent here is time taken from block 2. All of it is either low-signal or something a lead
is not expected to have.

- **Java syntax drills, LeetCode, Streams API depth.** Not what this interview tests.
- **JVM/GC tuning.** Know G1 is the default and ZGC is the low-pause option. Stop.
- **Reactive programming / WebFlux.** Virtual threads have removed most of its reason to exist.
  Know that sentence; skip the framework.
- **Kafka and Redis operations.** Concepts yes, running clusters no.
- **Kubernetes.** Unless they mention it first.
- **Design patterns as a catalogue.** Nobody will ask you to recite the visitor pattern.
- **"Microservices best practices" content.** See block 6.

---

## The honest framing for the interview

You are new to Java and you're applying for a role whose posting says, explicitly, that they
don't expect candidates to have everything. Don't hide it and don't over-apologise:

> *"I've been building in TypeScript for years and I'm a few weeks into Java. To get up to
> speed I built a small payment-initiation service — idempotent partner API, transactional
> outbox onto Kafka, the resilience stack around the partner client. It's what taught me the
> Spring transaction model properly, mostly by getting it wrong: I had a bug where a JPA
> `save()` on an assigned ID silently did an UPDATE instead of a failing INSERT, which quietly
> destroyed the idempotency guarantee. The concurrency test caught it — 15 of 16 threads
> acquired the same key."*

That answer does more for you than any amount of syntax practice. It shows how you learn, that
you write tests that can actually fail, and that you understand the failure mode rather than
the API. Have it ready.
