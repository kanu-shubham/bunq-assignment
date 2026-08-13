# Engineering Lead interview prep — distributed systems, fault tolerance, JVM

Prep material for the **Engineering Lead, Send for Partners** role at Wise, written for
someone who is strong in TypeScript/React and new to Java.

Everything here is built around one runnable Spring Boot service, [`partner-send/`](./partner-send),
which is a miniature of the domain the role actually covers: accepting payment instructions
from partner banks, doing so exactly once, and staying up when the partner does not.

```
mvn -q test         # 47 tests, ~60s (a real Kafka broker runs in-process)
mvn spring-boot:run # http://localhost:8080
```

**New here?** Read [§08](./08-learning-path.md) first — it answers "where do I start".
**Java feels unfamiliar?** [§10](./10-code-walkthrough.md) explains every file and every
annotation from scratch.

---

## A word on the honest strategy

You are applying for a role whose "what you'll need" list includes *"Experience with JVM
platform (Java/Kotlin/Spring Boot)"* — and the same posting says, in as many words, that
they do not expect candidates to have everything and will support you learning the rest.
That is not boilerplate; it changes what you should optimise for.

At Engineering Lead level, nobody is going to test whether you have memorised the `Stream`
API. What they will test:

1. **Can you reason about distributed systems?** Idempotency, ordering, partial failure,
   consistency trade-offs. This is language-independent and it is the bulk of the signal.
2. **Can you read and discuss JVM code credibly?** You need to follow a Spring Boot service
   in a design discussion and have opinions about it. You do not need to write it from a
   blank page under time pressure.
3. **Can you lead?** Growing seniors, making architectural calls, communicating across
   Solutions Engineering and Business Development, which this team sits between.

So: **do not** spend your prep time on Java syntax drills. Spend it on 02–05, and use 01 to
get to the point where Java prose reads as easily as TypeScript does.

Be straightforward about the Java gap if it comes up. "I've been writing TypeScript and I'm
a few weeks into Java — here's a service I built to learn it" is a much stronger answer than
hedging, and you have the service to point at.

> A caveat worth stating plainly: I don't have inside knowledge of Wise's interview process.
> The round structure below is inferred from the job description and from how companies of
> this size and type generally hire engineering leads. Treat it as a sensible default to
> prepare against, not as a leaked agenda.

---

## What the role tells you

Read the posting closely and the technical shape of the job falls out of it:

| The posting says | What that means for the interview |
|---|---|
| "banks and enterprises... through their own channels" | You are the API provider. Partner-facing contracts, versioning, onboarding, and *their* retry behaviour is your problem. |
| "event-based architectures and microservices" | Expect outbox/saga/ordering questions. §2. |
| "scalable and robust systems that power our global payment network" | Robust = fault tolerance under partner failure. §3. |
| "sits at the intersection of... Solutions Engineering, Implementation Success, Business Development" | Communication rounds are weighted. You'll be asked how you explain technical constraints to non-engineers. §6. |
| "hands-on when needed" | You will read code in the interview. Not necessarily write much. |
| "bias to action and comfort with ambiguity... while maintaining quality and compliance" | Payments-specific: they want to know you won't move fast in the places where you must not. §4. |

---

## The files

| | |
|---|---|
| [01 — Java for TypeScript developers](./01-java-for-typescript-devs.md) | The 20% of Java and Spring Boot that carries the interview, mapped from what you already know. |
| [02 — Distributed systems](./02-distributed-systems.md) | Idempotency, dual writes, delivery semantics, ordering, consistency, sagas. |
| [03 — Fault tolerance](./03-fault-tolerance.md) | Timeouts, retries, backoff, circuit breakers, bulkheads, load shedding, and how they interact. |
| [04 — Payments domain](./04-payments-domain.md) | Ledgers, money, reconciliation, compliance. The thing that separates you from a generic backend candidate. |
| [05 — System design walkthrough](./05-system-design-walkthrough.md) | A full worked answer to "design Send for Partners", the likely design round. |
| [06 — Leadership round](./06-leadership-round.md) | Stories, frameworks, and the questions to ask them. |
| [07 — Question bank](./07-question-bank.md) | Rapid-fire Q&A for the last 48 hours. |
| [08 — What to learn, in what order](./08-learning-path.md) | **Start here if you're deciding where to begin.** The sequence, the schedule, the exercises, and what to skip. |
| [09 — Events, Kafka and Redis](./09-events-kafka-redis.md) | Event processing concepts, grounded in a real broker running in-process. |
| [10 — The whole codebase, explained](./10-code-walkthrough.md) | **Beginner-level.** Every file in `partner-send/`, every annotation, mapped back to JavaScript/TypeScript. |
| [partner-send/](./partner-send) | The runnable service. Read the code; the comments are prep material. |

---

## Study plan

Pick the row that matches your remaining time.

### If you have two weeks

> §08 has a fuller version of this with exercises. The short form:

| Days | Focus |
|---|---|
| 1–2 | §01. Get Java reading fluently. Run the service, step through `TransferController`. |
| 3–5 | §02 + §03. Read the source alongside them — `IdempotencyService`, `OutboxPublisher`, `ResilientPartnerBankClient`. Break a test on purpose and watch it fail. |
| 6–7 | §04. Payments domain. This is your differentiator; most candidates skip it. |
| 8–10 | §05. Whiteboard the design three times, out loud, to a timer. Once with no notes. |
| 11–12 | §06. Write out your stories properly. Six of them, in STAR form. |
| 13 | §07 rapid-fire. Re-run the test suite and read the failures. |
| 14 | Light review. Prepare your questions for them. Sleep. |

### If you have three days

- **Day 1:** §05 (the design walkthrough) and §03. These carry the most weight per hour.
- **Day 2:** §02 and §04. Read `IdempotencyService` and `ResilientPartnerBankClient` in full.
- **Day 3:** §06 stories, §07 rapid-fire, and one clean out-loud run of the design.

### If you have tonight

Read §07, then §05, then the class comments in `IdempotencyService` and
`ResilientPartnerBankClient`. Be able to explain the idempotency protocol and the retry /
circuit-breaker interaction. That is the highest-value hour available to you.

---

## The four things to be able to say without hesitating

If you retain nothing else:

1. **"At-least-once delivery plus an idempotent consumer gives you exactly-once *effect*.
   Exactly-once *delivery* is not available over a network."** — the single most reliable
   signal of distributed-systems maturity.

2. **"You cannot atomically write to a database and publish to a broker. That's the dual-write
   problem, and the answer is the transactional outbox."** — §2, and the code in `TransferService`.

3. **"A timeout is not a failure, it's an ambiguity. You don't know whether it happened."**
   — this is what makes payments harder than CRUD, and it's why retries need idempotency keys.

4. **"Retry only what is retryable, with exponential backoff and jitter, behind a circuit
   breaker."** — and be ready to explain why jitter matters, since that's the follow-up.
