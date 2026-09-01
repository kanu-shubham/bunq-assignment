# Circuit breaker round — how to run the 40 minutes

For the Wise pair-programming question: *"implement a circuit breaker with
response caching inside `WebClient.execute(Request)`"*, run by two
Head-of-Engineering-level interviewers.

Code lives in [`src/features/circuitBreaker/`](../src/features/circuitBreaker/).
**Type `minimal.ts` in the room. Everything else is what you say when asked.**

---

## 1. Before you type anything (3–4 min)

Do not start coding. Every positive report of this round starts with the
candidate clarifying `HALF_OPEN`. Ask these, in this order, and write the
answers down as comments — they become your spec:

1. **"What counts as a failure?"** — 5xx and timeouts, presumably. Does a 4xx
   count? *(You want them to say no; if they say yes, push back once.)*
2. **"What opens it — N failures in a row, or a failure rate over a window?"**
   Start with consecutive, say you'd move to a rate later.
3. **"In HALF_OPEN, how many requests get through — one, or all of them?"**
   ← **this is the question they are waiting for.**
4. **"What closes it — one good response, or several?"**
5. **"On a failed probe, do we go straight back to OPEN, or spend the failure
   budget again?"**
6. **"When the circuit is open and I have a cached response — do I serve it,
   or fail?"** *(This is what makes it a caching question rather than two
   unrelated features.)*
7. **"Is there a timeout on the underlying call?"** If they say no, say you're
   adding one and why: without it a hung downstream never registers as a
   failure and the breaker never opens.

Then state the plan in one sentence before typing:
> "I'll do the three states with an injected clock so it's testable, get the
> transitions green, then wire in the cache and the stale fallback."

## 2. The build order (25 min)

Write it in this order. Each step is runnable, so you're never 20 minutes into
something that doesn't work.

| Order | What | Why this order |
|---|---|---|
| 1 | `State` type, fields, constructor | Cheap, and it makes the design visible on screen immediately |
| 2 | `execute()` admission block (OPEN → refuse / lazily go HALF_OPEN) | The heart of it |
| 3 | `onSuccess` / `onFailure` | Now the whole state machine exists |
| 4 | **One test: 3 failures opens it** | Do this before adding the cache. Showing a green test early buys you enormous credit |
| 5 | Probe limiting (`probing` flag) | The thing they're grading |
| 6 | Cache: hit → return, success → store | |
| 7 | **Stale fallback in the `catch`** | The line that ties the two halves together — say it out loud |
| 8 | Cacheable-methods guard | See §5; volunteer this, don't wait |

Narrate while typing. Silence fails this round even when the code is right.

## 3. HALF_OPEN — the exact answers

> **"How many requests do you let through in HALF_OPEN?"**
> One, by default. The permit is taken **synchronously, before any `await`**,
> so two callers in the same tick can't both take it. If you let everything
> through the moment the timer expires, you knock over the service that was
> just getting back on its feet — you've built a load generator aimed at your
> sickest dependency.

> **"What closes the circuit?"**
> N consecutive successes, not one. A single lucky response isn't evidence of
> recovery. Two is a reasonable default.

> **"And if the probe fails?"**
> Straight back to OPEN, immediately — I don't spend the whole failure
> threshold again, I already know it's unwell. And the open window **restarts
> from the failed probe**, so backoff is measured from the last evidence of
> failure, not from the original trip.

> **"What if the probe never returns?"**
> That's why there's a per-call timeout. Without one the breaker sits in
> HALF_OPEN holding its only permit forever — the exact deadlock it exists to
> prevent.

> **"Is this thread-safe?"** *(they will ask — the reference answer is Java)*
> In JS there are no data races, but there is interleaving across `await`, so
> the same bug exists in a different costume. Two defences: nothing is
> `await`ed between reading the state and mutating it, and every transition
> bumps a **generation counter** that each in-flight call carries — a probe
> that lands after the circuit already moved on is dropped instead of being
> allowed to close it. In Java I'd get the same property with an
> `AtomicReference` holding an immutable state record and a CAS loop.

## 4. The escalation ladder — what they'll ask you to code next

Ranked by how likely you are to get there. Each has a working implementation
in this repo; the point is to be able to *write the first 10 lines and explain
the trade-off*, not to reproduce mine.

| # | The ask | The move | File |
|---|---|---|---|
| 1 | "N in a row is a bit crude — improve it" | **Failure rate over a sliding window**, ring buffer, plus a `minimumCalls` floor so one failed call isn't a 100% error rate. Make it a strategy the breaker takes as a dependency | `failurePolicy.ts` |
| 2 | "Now it runs in 5 pods" | Keep state **per-pod**. See §6 | — |
| 3 | "Add retries" | `retry(breaker(call))` — **retry outside**, and `CircuitOpenError` is non-retryable. Exponential backoff with **full jitter** | `retry.ts` |
| 4 | "One endpoint is slow, why is everything down?" | **One breaker per downstream**, keyed by route template not concrete URL | `breakerRegistry.ts` |
| 5 | "What if it's slow but not failing?" | **Bulkhead** — cap concurrent calls with a *bounded* queue. A breaker doesn't help with slow-but-succeeding, and slow is what drains your connection pool | `bulkhead.ts` |
| 6 | "Two requests for the same thing at once" | **Single-flight** — one downstream call, both callers get the result | `webClient.ts` |
| 7 | "How do you know it's working in prod?" | Emit every transition; alert on *open count across the fleet*, not on one pod | `onStateChange` |
| 8 | "Improve the cache" | `stale-while-revalidate`: serve stale **and** kick off a background refresh once the circuit closes | (say it, don't build it) |

## 5. Say these unprompted — they're the fintech signals

Wise is grading product thinking, not just the state machine. Three lines that
land hard in a payments interview:

- **"I'll only cache safe methods."** Replaying a `POST /transfers` out of a
  cache moves the money twice; single-flighting two of them silently drops
  one. Say this *while writing the guard*.
- **"A 4xx shouldn't open the circuit."** A 400 means *we* sent a bad request.
  Tripping on it takes out a perfectly healthy downstream.
- **"The cache key has to be scoped per customer."** Otherwise one user is
  served another user's balance. This is a real incident, not a hypothetical.
- And when you add the stale fallback: **"this is the difference between
  'something went wrong' and 'your balance, as of two minutes ago' — so I'm
  returning the age with the response, not hiding it."**

## 6. The multi-pod answer (they asked this in the Hyderabad round)

> Keep breaker state **per-instance**. Putting it in Redis adds a network hop
> to the hot path of every call — doubling the IO you were trying to save —
> and makes your resilience component a single point of failure: Redis blips
> and now every pod is either fully open or fully blind. Per-pod state
> converges anyway; if the downstream is genuinely down, every pod learns that
> within one threshold. What I *would* centralise is the observability —
> ship transitions to metrics so you can see how many pods are open — plus a
> manual override to force it open during an incident.

## 7. What loses this round

- Coding in silence. The problem is easy; the round is about the narration.
- Starting to type before clarifying HALF_OPEN.
- Letting all traffic through in HALF_OPEN, then not noticing when asked.
- No timeout on the wrapped call.
- Counting a `CircuitOpenError` as a failure — the circuit then never recovers,
  because being refused keeps it refused.
- Treating the two interviewers as one. One report describes them as
  interruptive, one asking detail questions while the other says "let's move
  on". If that happens: *"Happy to go either way — shall I finish this bit
  first, or jump to that?"* Making the conflict explicit and cheap to resolve
  is itself a senior signal.
- Reproducing a 300-line library from memory. They want to watch you think,
  not type. `minimal.ts` and then a conversation beats a perfect artefact.

## 8. If you have five minutes left

Say what you'd do with more time — Wise asks this explicitly on their take-home
guidance, so it's a rehearsed answer, not an admission:

> "Sliding-window failure rate instead of a consecutive count; jittered,
> growing open durations so a fleet doesn't probe in lockstep; per-endpoint
> breakers; metrics on transitions and on stale-serve rate. And I'd want the
> stale-serve path visible in the UI rather than silent."
