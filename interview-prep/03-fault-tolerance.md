# 03 — Fault tolerance

Everything here is implemented and tested in
[`ResilientPartnerBankClient`](./partner-send/src/main/java/com/example/prep/partnersend/partner/ResilientPartnerBankClient.java),
[`ResilienceConfig`](./partner-send/src/main/java/com/example/prep/partnersend/config/ResilienceConfig.java)
and `ResiliencePatternsTest` (11 tests). Read them alongside this.

---

## The governing principle

**An unbounded wait is the one outcome a distributed system must never have.**

Almost every cascading outage has the same shape:

1. A downstream dependency gets slow (not down — *slow*, which is worse).
2. Callers wait. Threads and connections pile up in the waiting state.
3. The thread pool exhausts.
4. The service stops serving **every** endpoint, including ones with nothing to do with that
   dependency.
5. Its callers get slow. Repeat upward.

Note step 4. The damage is not to the feature that depends on the failing partner; it's to the
whole service. Every pattern below exists to break that chain, and they mostly do it by
**failing fast on purpose**.

---

## The five patterns

### 1. Timeouts — the foundation

Without a timeout, nothing else works. A retry can't fire, a circuit breaker can't count a
failure, a bulkhead slot never frees.

**Every network call must have one.** The default in most HTTP clients is *infinite*, which is
never what you want.

Two you need, and they're different:
- **Connect timeout** — time to establish the connection. Short (1s); if the TCP handshake
  isn't done, the host is unreachable.
- **Read/response timeout** — time waiting for the response. Sized to the operation.

**How to pick one:** from the downstream's p99 latency plus headroom, not from a round number.
If p99 is 800ms, a 2s timeout is generous; a 30s one means you'll wait 30s to learn something
you knew at 2s.

**Timeouts must shrink as you go down the stack.** If your client waits 5s, your call to the
partner must be well under that — otherwise you return an answer to someone who stopped
listening, having held a thread the whole time.

```java
TimeLimiterConfig.custom()
    .timeoutDuration(Duration.ofSeconds(2))
    .cancelRunningFuture(true)
    .build();
```

**The catch worth volunteering:** a timeout is client-side. You've stopped *waiting*; the
partner is still processing. Whatever it is doing, it will finish doing. That is why timeouts
and idempotency keys are the same design decision.

### 2. Retries — necessary and dangerous

Retries fix transient failures. They also convert a struggling dependency into a dead one.
Three rules:

**(a) Retry only what is retryable.**

| Retryable | Not retryable |
|---|---|
| 503, 502, connection reset | 400 malformed request |
| Read timeout | 401/403 auth |
| 429 (honour `Retry-After`) | 422 business rejection ("account closed") |

Retrying a 400 is pure waste aimed at a system already telling you no. This is the single most
common resilience bug, and `ResiliencePatternsTest.doesNotRetryRejections` asserts exactly
one attempt for a rejection.

**(b) Exponential backoff with jitter.**

Backoff gives the dependency room. **Jitter is the part that matters and the part people
forget.**

Without jitter: 10,000 clients fail at the same instant, all wait exactly 100ms, all retry at
the same instant. The recovering service is knocked straight over. Backoff alone *synchronises*
the herd; the randomness is what disperses it.

```java
IntervalFunction.ofExponentialRandomBackoff(
    Duration.ofMillis(100),  // initial
    2.0,                     // multiplier: 100, 200, 400...
    0.5)                     // ±50% jitter
```

**(c) Keep the budget small, and remember it multiplies.**

Three services deep, 3 attempts each, is 27 requests at the bottom. Retry storms are made of
reasonable local decisions. Prefer retrying at one level — ideally the outermost — and if
you're being thorough, mention **retry budgets** (cap retries at a percentage of total
traffic) as the technique that bounds this properly.

### 3. Circuit breakers — stop asking

When a dependency is genuinely down, retrying wastes your resources and delays its recovery.
A circuit breaker notices the pattern and stops calling.

```
CLOSED ──failure rate exceeded──▶ OPEN ──after wait──▶ HALF_OPEN
   ▲                                                       │
   └────────────── probes succeed ◀────────────────────────┘
                              │
                              └── probe fails ──▶ OPEN
```

- **CLOSED** — normal, counting outcomes.
- **OPEN** — reject immediately without calling. This is the point: the downstream gets
  breathing room and your threads come back.
- **HALF_OPEN** — let a few probes through. Success closes it; failure re-opens. This is what
  stops a recovering partner from being flattened by the entire backlog the instant it returns.

**Configuration, with reasons** (from `ResilienceConfig`):

```java
.failureRateThreshold(50f)          // half of recent calls failing
.slidingWindowSize(20)              // count-based: same behaviour at 10 rps and 10,000
.minimumNumberOfCalls(10)           // never trip on 3 samples at 04:00
.waitDurationInOpenState(ofSeconds(10))
.permittedNumberOfCallsInHalfOpenState(3)
.slowCallRateThreshold(50f)         // slow counts as failed —
.slowCallDurationThreshold(ofSeconds(2))  // 8s responses are functionally down
.ignoreException(this::isBusinessRejection)
```

Two of those lines are where the judgement shows:

- **`minimumNumberOfCalls`** — without it, low-traffic periods trip the breaker constantly on
  tiny samples.
- **`ignoreException`** for business rejections — a partner rejecting 10,000 invalid IBANs is
  *healthy and answering correctly*. Counting that as failure would cut off every other
  partner's payments because one partner sent bad data.
  (`ResiliencePatternsTest.rejectionsAreIgnoredByTheBreaker`.)

### 4. Bulkheads — contain the damage

Named after ship compartments: one floods, the ship stays up.

Give each dependency its own bounded concurrency. When partner A hangs, calls to A queue up
and get shed — but the pool for partner B, and every unrelated endpoint, is untouched.

```java
BulkheadConfig.custom()
    .maxConcurrentCalls(16)
    .maxWaitDuration(Duration.ofMillis(50))   // brief wait, then shed
    .build();
```

Keep the wait short. A long queue here just relocates the latency somewhere less visible: the
caller waits either way, but now the wait is invisible to the metric that would have told you.

Without bulkheads, one slow partner absorbs every thread in a shared pool and takes the whole
service with it — step 4 of the cascade above.

### 5. Fallbacks — only when honest

Serve stale data, a cached rate, a degraded response. Good for a quote endpoint; **wrong for a
payment**. Never fabricate a success. If you can't take a payment, say so — a `503` with
`Retry-After` is a better answer than a lie, and in a regulated domain the lie is also a
compliance problem.

---

## Composition — the question that separates candidates

Anyone can list the patterns. The interesting question is **what order do they nest in**.

```
Bulkhead ( Retry ( CircuitBreaker ( TimeLimiter ( call ) ) ) )
outermost                                          innermost
```

**TimeLimiter innermost — per attempt, not per request.** Each attempt gets its own deadline.
If the timeout wrapped the retry, one slow attempt would eat the whole budget and the retries
would never run. `ResiliencePatternsTest.timeoutAppliesPerAttemptNotPerRequest` demonstrates
it: attempt one hangs and is cut off, attempt two succeeds.

**CircuitBreaker inside Retry** (i.e. retry wraps the breaker). This is the contested one, so
know both sides:

| Retry outside breaker *(this design)* | Retry inside breaker |
|---|---|
| Breaker records every attempt → sees true failure rate, trips fast | Breaker records one outcome per logical request |
| Once open, remaining retries fail instantly instead of hammering a sick partner | Cleaner statistics, but needs far more failures to trip |
| **Better when you can overwhelm the downstream — like a partner bank** | Better when the downstream is robust and you only care about end results |

**Bulkhead outermost**, because its cap must include time spent waiting between retries. A
retrying call still occupies a slot; if the bulkhead were inside, the concurrency limit
wouldn't cover the backoff waits and wouldn't bound anything real.

**The one-liner:** *"Bulkhead outside so the concurrency cap covers the retries and their
backoff, retry outside the breaker so the breaker sees every attempt and can cut a retry storm
short, and the timeout innermost so each attempt gets its own deadline."*

### The gap worth naming

Per-attempt timeouts don't bound the *total* time. Three attempts at 2s plus backoff can
exceed 7s, long after the client gave up.

The production answer is **deadline propagation**: the caller passes an absolute deadline
(a header, or gRPC's built-in deadline), every layer respects it, and when it passes,
everything stops — no more retries, no more waiting. Raising this unprompted is a strong
senior signal, because it's the thing most resilience configs get wrong.

---

## Beyond the code library

Patterns get you through the interview; these show you've operated systems.

**Health checks — distinguish the two.** *Liveness* = "am I broken, restart me". *Readiness* =
"can I serve traffic right now". Conflating them is a classic outage: a liveness check that
depends on the database means a brief database blip restarts every pod simultaneously,
guaranteeing an outage from what would have been a blip. In Spring Boot:
`/actuator/health/liveness` and `/actuator/health/readiness`.

**Graceful degradation.** Rank features by what must survive. Settling in-flight payments
outranks accepting new ones, which outranks the analytics dashboard. Know the order *before*
the incident.

**Idempotency as a resilience feature.** Everything above assumes retries are safe. They're
only safe because of §2's idempotency work. Retries and idempotency are one design, not two —
`ResiliencePatternsTest.idempotencyKeyDoesNotChangeBetweenAttempts` asserts the key is stable
across attempts, because a key regenerated per attempt turns a network blip into three
payments.

**Observability — the four you should name.**
- **RED** for services: Rate, Errors, Duration.
- **USE** for resources: Utilisation, Saturation, Errors.
- Alert on **symptoms** (payments failing), not causes (CPU at 80%).
- **Percentiles, never averages.** Mean latency hides everything. p99 is where your worst
  customer lives, and at scale p99 is somebody's every request.

**Chaos engineering.** Inject failure deliberately — kill a pod, add 500ms of latency, blackhole
a partner. Untested failure paths do not work; they are just code that has never run. The
`SimulatedPartnerBankClient` is a toy version of this idea.

---

## Quick self-test

1. Why is a slow dependency worse than a dead one? → Slow holds threads; dead fails fast.
2. Why does jitter matter? → Backoff alone synchronises the herd.
3. Which failures should you never retry? → Anything deterministic: 400, 401, 422.
4. Why not count business rejections toward the circuit breaker? → Partner is healthy; one
   partner's bad data would cut off everyone.
5. What does a bulkhead protect? → Everything *else* in your service.
6. Where does the timeout go relative to the retry, and why? → Inside; per-attempt budget.
7. What bounds total request time? → Not per-attempt timeouts — deadline propagation.
