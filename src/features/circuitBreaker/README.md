# Circuit breaker with response caching

A TypeScript answer to the most-attested Wise pair-programming question:

> Implement a circuit breaker with response caching for an existing request
> handler — inside `WebClient.execute(Request)`.

See [`docs/wise-pair-programming-questions.md`](../../../docs/wise-pair-programming-questions.md)
for where that prompt comes from.

| File | What it is |
|---|---|
| `circuitBreaker.ts` | The state machine. Knows nothing about HTTP. |
| `responseCache.ts` | Bounded TTL cache that keeps entries *past* their TTL. |
| `webClient.ts` | `execute(request)` — the handler, with both retrofitted. |

`npm test` — 32 tests here, covering every transition, the concurrency edges,
and the fail-static paths.

## The state machine

```
                 failureThreshold consecutive failures
      ┌────────┐ ─────────────────────────────────────► ┌──────┐
      │ CLOSED │                                         │ OPEN │
      └────────┘ ◄─────────────────────────────────────  └──────┘
           ▲       successThreshold consecutive probes       │
           │                                                 │ openDurationMs
           │                                                 │ elapsed
           │            ┌───────────┐                        │
           └─────────── │ HALF_OPEN │ ◄──────────────────────┘
                        └───────────┘
                              │  any probe fails
                              └──────────────────► OPEN (window restarts)
```

- **CLOSED** — everything through. `failureThreshold` *consecutive* failures
  opens it; one success resets the run.
- **OPEN** — nothing through, `CircuitOpenError` with a `retryAfterMs` instead.
  Being refused does not itself count as a failure, so a flood of blocked
  traffic can't extend the outage.
- **HALF_OPEN** — entered lazily on the first call after the window elapses.
  A limited number of probes are admitted; the rest are still refused.

## HALF_OPEN, in detail

This is the part the interviewers push on, so it's the part with the most
deliberate answers:

**How many requests get through?** `halfOpenMaxConcurrent`, default 1. The
permit is taken **synchronously** in `acquire()`, before any `await`, so two
callers arriving in the same tick cannot both take the last one. Releasing the
gates entirely the moment the window elapses is how you knock over a service
that was just getting back on its feet.

**What closes it?** `successThreshold` *consecutive* successes, default 2. One
lucky response isn't evidence of recovery.

**What reopens it?** A single failed probe — immediately, without spending the
whole `failureThreshold` budget again. We already know the downstream is
unwell; the probe just confirmed it. The open window **restarts** from the
failed probe, so backoff is measured from the last evidence of failure.

**What if a probe never comes back?** `callTimeoutMs` (default 5s). Without a
per-call timeout, a hung downstream leaves the breaker stuck in HALF_OPEN
holding its only permit — the exact failure mode the breaker exists to prevent.

**What about a probe that comes back *late*?** Every transition bumps a
`generation` counter, and each admitted call carries the generation it was
admitted under. A probe that resolves after the circuit has already moved on is
dropped. JavaScript has no data races, but it has plenty of interleaving across
`await`, and this is the JS-shaped answer to "make it thread-safe": there is no
`await` between reading state and mutating it, and results that arrive into a
different generation are ignored. In Java this same reasoning is what an
`AtomicReference` on an immutable state record buys you.

**No background timers.** OPEN → HALF_OPEN is evaluated on the next call, not
by a `setInterval`. A breaker with a timer keeps the event loop alive and needs
an explicit `dispose()`; a lazy check needs neither and is trivially testable
with an injected clock.

## Why the cache is part of the same answer

A breaker alone converts a slow failure into a fast one. Pair it with a cache
and it converts a failure into a **degraded success**:

1. fresh cache hit → return it, no downstream call
2. identical call already in flight → join it (single-flight)
3. otherwise call downstream through the breaker; cache the success
4. call failed or was refused → serve a **stale** entry if we have one

Step 4 is why the cache retains entries past their TTL. The TTL answers "fresh
enough to serve without asking?", not "safe to delete". Eviction is by size
(LRU), never by age.

Every result is tagged `network` / `cache-fresh` / `cache-stale` with an
`ageMs`, because for a money app the UI has to be able to say *"your balance,
as of two minutes ago"* rather than silently showing a stale number.

**Only safe methods are cached.** Replaying a `POST /transfers` out of a cache
moves the money twice, and single-flighting two of them silently drops one.
`GET`/`HEAD` by default; anything else is an explicit, deliberate opt-in.

**4xx doesn't trip the breaker.** A 400 means *we* sent a bad request — opening
the circuit on it takes out a healthy downstream. 5xx, 429 and non-HTTP errors
(network, timeout) count. That policy lives in `isDownstreamFailure`, injected
into the breaker, which is why the breaker itself stays HTTP-agnostic.

## The follow-ups

**"Now deploy it across multiple pods."** Breaker state stays **per-instance**,
on purpose. Sharing it through Redis puts a network hop on the hot path of
every call, doubles the IO you were trying to save, and makes your resilience
component a single point of failure — the thing fails and now *every* pod is
either fully open or fully blind. Per-pod state converges anyway: if the
downstream is genuinely down, every pod observes it within one threshold. What
you do want centralised is the *observability* — emit `onStateChange` to
metrics so you can see how many pods are open, and keep a manual operator
override (`reset()`) for the cases where you need to force it.

**"How would you test it?"** The injected clock is the whole answer — state
timing is exercised with `clock.advance(1_000)` instead of real waits or fake
timers, so the suite runs in milliseconds and is not flaky. Concurrency edges
use a deferred promise to hold a probe in flight while asserting that a second
caller is refused.

**"What would you add with more time?"**
- A rolling-window or percentage-based failure threshold instead of a
  consecutive count — 5 failures out of 5 and 5 out of 5000 are different
  situations.
- Jittered, exponentially growing open durations on repeated reopens, so a
  fleet of pods doesn't probe a recovering service in lockstep.
- Metrics for cache hit/stale-serve rates, not just breaker transitions.
- Per-endpoint breakers (one slow endpoint shouldn't open the circuit on a
  healthy one), which is a keyed registry over this same class.
- `stale-while-revalidate`: serve the stale entry *and* kick off a background
  refresh once the circuit has closed again.
