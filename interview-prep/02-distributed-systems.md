# 02 — Distributed systems

The concepts that carry an Engineering Lead interview, in the order they build on each other.
Each has a pointer to working code in [`partner-send/`](./partner-send).

---

## 0. The one idea underneath everything

**In a distributed system, a request that doesn't return doesn't tell you whether it happened.**

Locally, a function either returns or throws. Over a network there is a third outcome: no
answer. And "no answer" covers two irreconcilable worlds — the request never arrived, or it
arrived, was fully processed, and the *response* was lost.

You cannot distinguish them. Not with better monitoring, not with a longer timeout. This is
why a timeout is an **ambiguity**, not a failure, and it's the root of nearly every hard
problem below.

In CRUD, you shrug and retry. In payments, one of those worlds means you already sent
£50,000.

---

## 1. Idempotency

**Definition:** doing it twice has the same effect as doing it once.

`GET`, `PUT` and `DELETE` are naturally idempotent. `POST` is not — and "create a payment" is
inherently a POST. So you engineer it.

### The protocol

The client generates a key (a UUID) and sends it as a header. The key identifies *the client's
intent*, not the request — so all retries of the same logical payment carry the same key.

```
POST /v1/partners/acme/transfers
Idempotency-Key: 7f3c1e90-...
```

The server, per [`IdempotencyService`](./partner-send/src/main/java/com/example/prep/partnersend/idempotency/IdempotencyService.java):

1. Hash the request body.
2. `INSERT` the key as `IN_PROGRESS` **in its own committed transaction**.
   - Success → we own it. Do the work.
   - Duplicate-key violation → someone else owns it. Go to 3.
3. Read the existing row:
   - `COMPLETED`, hash matches → **replay the stored response**.
   - `COMPLETED`, hash differs → **422**. The client reused a key for a different payment.
   - `IN_PROGRESS` → **409 + `Retry-After`**. A duplicate is in flight right now.
4. After the work commits, update the row to `COMPLETED` with the response body.

### The four things interviewers probe

**"Why not just check if the key exists first?"**
Because check-then-insert is a race. Two concurrent retries both `SELECT` nothing, both
`INSERT`, and you pay twice. Only the database can arbitrate — so the uniqueness has to be a
constraint, and the loser handles the violation. Application-level locking cannot fix this
across instances. `ConcurrentIdempotencyTest` fires 16 threads at one key and asserts exactly
one winner.

**"Why hash the body?"**
Without it, a client that recycles a key for a different payment gets the *first* payment's
response and believes their second payment succeeded. The hash turns a silent, expensive bug
into a 422.

**"What if the process dies mid-request?"**
The key is stranded `IN_PROGRESS` and that client is locked out. You need a reaper, and its
threshold must exceed the longest the work can possibly take *including its retries* — set it
too short and you re-open a key while the original is still running, which is the double
payment you were preventing. See `IdempotencyClaimStore.reclaimStale`.

**"How long do you keep keys?"**
Stripe uses 24 hours. Long enough to cover any sane client retry window; short enough that the
table doesn't grow forever. Say a number and justify it — the reasoning is the answer.

---

## 2. The dual-write problem

**You cannot atomically write to your database and publish to a message broker.**

```java
transferRepository.save(transfer);   // committed
kafka.send(transferCreatedEvent);    // ...process dies here
```

No ordering is safe. Save-then-publish loses the event. Publish-then-save emits an event for a
transfer that doesn't exist. Two systems, two failure modes, and no `try/catch` merges them.

*("What about distributed transactions / two-phase commit?" They exist and are avoided:
every participant must support 2PC, locks are held across a network round trip, and a
coordinator crash leaves rows locked in doubt. Naming XA and explaining why you wouldn't use
it is a strong answer.)*

### The transactional outbox

Write the event **to the same database, in the same transaction** as the business change.
A separate process moves rows to the broker afterwards.

```java
@Transactional
public Transfer acceptTransfer(...) {
    transfers.save(transfer);
    outbox.save(OutboxEvent.of(transfer.getId(), "transfer.received", payload, now));
    return transfer;   // one transaction, two tables — atomic
}
```

Now it's one local ACID transaction: both rows or neither. `OutboxTest.rollbackLosesBoth`
proves it — force an exception after `acceptTransfer` and neither the transfer nor the event
survives.

**Polling vs CDC:** the sample polls every 500ms (`OutboxPublisher`). The grown-up option is
change data capture — Debezium tailing the database's write-ahead log — which removes polling
latency and load at the cost of running Kafka Connect. Reach for CDC when the poll interval
becomes the dominant latency or the table gets hot.

**Running several pollers:** add `FOR UPDATE SKIP LOCKED` so replicas step over each other's
claimed rows instead of blocking. Without it, N pollers serialise and you've bought horizontal
scaling you don't get. (Noted in `OutboxRepository`; omitted in code only because H2 and
Postgres differ on the hint.)

---

## 3. Delivery semantics

The classic three, and the honest version:

| | Meaning | Reality |
|---|---|---|
| **At-most-once** | Fire and forget | Loses messages. Fine for metrics, never for money. |
| **At-least-once** | Retry until acknowledged | Duplicates. **This is what you actually build.** |
| **Exactly-once** | Each message processed once | **Not achievable as a delivery guarantee.** |

### Say this precisely

> *"Exactly-once **delivery** is impossible over a network. Exactly-once **effect** is
> achievable, and you get it with at-least-once delivery plus an idempotent consumer."*

That distinction is the single most reliable signal of distributed-systems maturity, and most
candidates blur it.

Why impossible: the consumer must process the message *and* record that it did. Two writes.
Same dual-write problem, one level down. The broker can't fix it because it can't know whether
your side-effect landed before you crashed.

*(If someone says "but Kafka has exactly-once semantics" — that's true only within Kafka:
transactional reads-and-writes where source and sink are both Kafka. The moment your
side-effect is an external payment, the guarantee doesn't reach it.)*

**Practically:** every event carries a stable `eventId`. Consumers keep the ids they've
processed (a table, or Redis with a TTL) and drop repeats. That's `OutboxEvent.eventId`.

---

## 4. Ordering

Global ordering across a distributed system requires a single writer, which destroys your
ability to scale. You almost never need it.

What you need is **per-entity ordering**: events for *one transfer* must arrive in causal
order. Events for different transfers can interleave freely.

**Mechanism:** partition by entity id. Kafka guarantees ordering within a partition, so all
events for transfer `abc` go to the same partition and stay ordered. That's why
`OutboxEvent.aggregateId` doubles as the partition key.

**Consumers must still tolerate disorder**, because retries and redeliveries reorder things
anyway. Two defences:

- **Version/sequence numbers** — drop an event older than the state you already have.
- **A state machine that rejects impossible transitions** — `TransferStatus` won't let a late
  `SETTLED` webhook overwrite a `RETURNED` transfer. `TransferStateMachineTest` covers this.

The state machine is the more robust of the two, because it's a domain rule rather than
plumbing.

---

## 5. Consistency

### CAP, stated correctly

When the network **partitions** (P — not optional, networks fail), you choose between
**consistency** and **availability**. That's it. CAP says nothing about normal operation, and
"we chose AP" as a general architecture statement is a misuse of it.

The useful follow-up is **PACELC**: on Partition, choose A or C; **Else**, choose Latency or
Consistency. The second half is where you actually live day to day.

### Where payments land

Not uniformly, and saying so is the good answer:

- **The ledger is CP.** A double-spend is unacceptable; refusing the request is merely
  annoying. Single-writer per account, strong consistency, no argument.
- **Status lookups are AP.** Serving a status a few seconds stale beats returning an error.
- **Partner-facing accept is AP-ish**: accept, persist, return `202`, settle asynchronously.
  You're honest about what you know ("recorded") rather than claiming what you don't
  ("settled").

### Isolation levels — know these two

- **Read Committed** (Postgres default): no dirty reads. Two transactions can still read the
  same balance and both write, losing an update.
- **Serializable**: behaves as if transactions ran one at a time. Correct, and costs
  throughput plus serialisation failures your code must retry.

**Optimistic locking** is the pragmatic middle, and it's what `Transfer` uses via `@Version`:
Hibernate appends `WHERE version = ?` and bumps the number. The second concurrent writer
matches zero rows and gets `OptimisticLockException`. No row locks, no deadlocks, and the
loser retries against fresh state. Right whenever conflicts are rare — which for a single
transfer, they are.

---

## 6. Sagas — long-running transactions

A payment spans several services: funds reservation, compliance, FX, partner submission. You
cannot hold a database transaction across them for the same reasons as above.

A **saga** is a sequence of local transactions, each with a **compensating action** that
semantically undoes it.

```
reserve funds  →  compliance check  →  FX quote  →  submit to partner
     ↓ release        ↓ release          ↓ release      ↓ recall/return
```

**Compensation is not rollback.** You cannot un-send a payment. You send a *return*, which is
a new transaction, visible on the customer's statement. Making that distinction out loud is a
strong signal.

**Orchestration vs choreography:**

- *Orchestration* — a central coordinator holds the state machine and calls each step. Easier
  to reason about, debug and observe; the coordinator is a dependency.
- *Choreography* — services react to each other's events. No central point, but the flow
  exists only as emergent behaviour and nobody can tell you what state a payment is in.

**For payments, prefer orchestration.** When a regulator asks "what happened to this
payment?", you need an answer from one place. Say that; it lands well.

---

## 7. Backpressure and shedding

If arrival rate exceeds processing rate, something has to give. Your only choice is *what*.

- **Queue it** — bounded queues only. An unbounded queue converts a throughput problem into an
  out-of-memory crash, having first made every request slow.
- **Shed it** — reject with 429/503 and `Retry-After`. Rude, honest, and keeps the system alive.
- **Never** silently accept work you can't do. That's how you get a queue of payments nobody
  is servicing and a customer who thinks their money moved.

**Little's Law** is worth having: `L = λW`. Items in system = arrival rate × time in system.
If you're processing 100/s and each takes 2s, there are 200 in flight — so a bulkhead limit of
16 will shed load, and that's arithmetic rather than opinion.

---

## Quick self-test

Cover the answers.

1. Why can't you write to Postgres and Kafka atomically? → §2
2. What does an idempotency key identify? → Client *intent*, §1
3. Exactly-once delivery — possible? → No; at-least-once + idempotent consumer, §3
4. Two concurrent requests, same idempotency key — what stops a double payment? → A PK
   constraint, not application logic, §1
5. A `SETTLED` webhook arrives for a `RETURNED` transfer. What happens? → State machine
   rejects it, §4
6. Difference between rollback and compensation? → §6
7. Your queue is growing. Options? → Bounded queue + shed, §7
