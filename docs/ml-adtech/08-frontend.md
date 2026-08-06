# 08 — Frontend

Two distinct frontends with opposite constraints:

| | **Ad SDK** | **Ops console** |
|---|---|---|
| Users | every end user, every page view | ~hundreds of internal/advertiser users |
| Constraint | bytes, jank, correctness of measurement | information density, correctness of *decisions* |
| Budget | < 15 KB gzipped, 0 layout shift | normal SPA |
| Stack | vanilla TS, no framework | React + TypeScript (matches this repo) |

## 8.1 Ad SDK

### Responsibilities

1. Request an ad without blocking page render.
2. Render it without causing layout shift.
3. **Measure viewability correctly** (this is a billing input, not analytics).
4. Fire impression/click beacons reliably, including on page unload.
5. Respect consent, and never collect anything before it is granted.

### Lifecycle as a state machine

The same discipline as the feedback widget already in this repo
([`src/features/feedback/state/feedbackMachine.ts`](../../src/features/feedback/state/feedbackMachine.ts)):
a reducer over a discriminated union, with an exhaustiveness check in `default` so a new state that
misses a case is a **compile error**. Ad rendering has more edge cases than a feedback modal, not
fewer, and the same failure mode (an unhandled transition leaving the UI stuck) here means an
unbilled or double-billed impression.

```
IDLE
 └─ REQUESTED ──(no-fill)──> EMPTY (collapse slot or house ad)
      └─ RENDERED ──(≥50% pixels for ≥1s)──> VIEWABLE ──> BILLED
            ├─(click)──> CLICKED
            └─(unload before viewable)──> ABANDONED
```

Every terminal state emits exactly one beacon. `BILLED` is server-confirmed, not client-asserted.

### Viewability measurement

MRC standard: **≥50% of pixels in view for ≥1 continuous second** (≥2 s for video, 100% for large
formats). Implemented with `IntersectionObserver` — the same primitive as the infinite-scroll
work in this repo, used here for money rather than for loading:

```ts
const io = new IntersectionObserver(
  (entries) => {
    for (const e of entries) {
      if (e.intersectionRatio >= 0.5) startTimer(e.target);   // 1s timer
      else cancelTimer(e.target);
    }
  },
  { threshold: [0, 0.25, 0.5, 0.75, 1] }   // multiple thresholds → no polling
);
```

Correctness details that separate a real implementation from a demo:

- **Tab visibility:** an ad "in view" in a background tab is not viewable. `document.visibilityState`
  gates the timer; `visibilitychange` cancels it. `IntersectionObserver` alone will not tell you this.
- **Cross-origin iframes:** `IntersectionObserver` cannot see through them; use
  `IntersectionObserver v2` (`trackVisibility`, where supported) or the SDK-in-frame + `postMessage`
  pattern, and fall back to a conservative *unmeasurable* classification rather than assuming viewable.
- **Timer accuracy:** accumulate with `performance.now()` deltas, not `setInterval` counting —
  throttled timers in background tabs would otherwise over-count.
- **Unload:** `navigator.sendBeacon()` on `pagehide`/`visibilitychange:hidden` (not `unload`, which
  is unreliable on mobile Safari). Beacon payloads are kept under 64 KB.

### Loading without hurting the page

- **Slot reservation:** the container gets its final dimensions from CSS before the request, so
  there is **zero cumulative layout shift**. Ad size is known from the placement config, not from
  the response.
- The SDK script is `async` + `defer`, ~12 KB gzipped, no dependencies, and self-hosted from the
  same origin as the ad endpoint (one fewer DNS+TLS handshake, ~40–100 ms saved on mobile).
- **Preconnect** to the ad origin in `<head>` so the TCP+TLS cost is paid in parallel with page
  parse rather than on the ad request.
- Requests are `fetch(..., {keepalive: true})` with a client-side timeout equal to the server's
  `timeout_ms` + network slack; on timeout the slot collapses gracefully or shows a house ad.
- Multi-slot pages batch into **one** request (`num_slots`), not N — the single biggest client-side
  latency win on a feed page.

### Accessibility & UX (non-negotiable, and often skipped in ad code)

- Ad containers are labelled (`role="complementary"`, `aria-label="Advertisement"`) and clearly
  marked as advertising in visible text — a legal requirement under the DSA, not a nicety.
- No autoplay audio; motion respects `prefers-reduced-motion`.
- Keyboard-reachable click target; the whole creative is one focusable link, not a div with an
  onclick.
- Skeleton/placeholder state while loading, so a slow ad looks intentional rather than broken.

### Testability

The SDK takes its transport as an injected dependency — the same DI seam pattern as
`submitFeedback` in the existing feedback widget:

```ts
createAdSlot(el, { request: transport, beacon: beaconFn, now: () => performance.now() });
```

Tests inject a fake transport, a fake clock, and a mock `IntersectionObserver`, making viewability
rules (999 ms → not viewable, 1001 ms → viewable, tab hidden mid-timer → not viewable) fully
deterministic unit tests. Billing logic must be testable without a browser and without a network.

## 8.2 Ops console (React + TypeScript)

### Surfaces

| Surface | Contents | Refresh |
|---|---|---|
| **Campaign manager** | CRUD, targeting builder, budget/flight, creative upload + policy status | on write |
| **Delivery dashboard** | spend vs. pace, impressions, CTR, CVR, fill rate, **rejection reasons** | 30 s |
| **Diagnostics ("why isn't it delivering?")** | funnel for a chosen campaign: eligible → retrieved → ranked → won → shown, with drop-off reasons at each step | on demand |
| **Experiments** | running A/B tests, metric deltas with confidence intervals, guardrail status | 5 min |
| **Model & system health** | model version per region, calibration drift, p99 latency, degradation-level mix | live |

The **diagnostics funnel is the highest-value screen** and the one most ad platforms lack. It maps
directly to the rejection reason codes emitted by the auction
([06](./06-auction-pacing-budgets.md#selection-is-not-just-argmax-ecpm)); building the codes without
building the screen wastes them.

### Frontend architecture

```
src/
  features/
    campaigns/     # forms, targeting builder, optimistic writes
    delivery/      # charts, funnel views
    experiments/
    health/
  shared/
    api/           # generated TS clients from the protobuf/OpenAPI schema
    charts/        # one chart system, consistent scales and colours
    state/         # server-state via React Query; UI state via reducers
```

- **Types generated from the same protobuf schema the backend serves** — the console cannot drift
  from the API, because a field rename fails `tsc`.
- **Server state via React Query** (caching, refetch, stale-while-revalidate); local UI state via
  explicit reducers. No global store for data that belongs to the server.
- **Charts stream from a read-optimized aggregate store** (ClickHouse or Druid over the Kafka
  events), never from the serving path or the lake. Console load must not be able to affect ad
  serving — separate store, separate credentials, separate capacity.
- **Optimistic writes with rollback** for campaign edits, but budget changes are **confirmed
  writes only**: a change that affects money waits for the server. Different affordances for
  different consequences.
- Virtualized tables for advertisers with thousands of campaigns; no unbounded lists.

### Real-time without websocket sprawl

Dashboards poll (30 s) rather than hold sockets: hundreds of users × persistent connections is
infrastructure to operate for data that changes at minute granularity anyway. The one exception is
the live-health screen during a deploy, which uses SSE for the canary's guardrail metrics — a
bounded number of viewers at a bounded time.

---
Next: [09 — Data & training pipeline](./09-data-and-training-pipeline.md)
