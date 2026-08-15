# A/B Testing, end to end — beginner to staff

A single document that starts at "what is a variant" and ends at "how do you run
experimentation for a bank". Every concept is paired with a worked example, and
most of them are backed by real, tested code in
[`src/features/experiments/`](../src/features/experiments) so you can read the
implementation instead of trusting the prose.

The running example throughout is the feature this repo ships: the bunq feedback
widget (`CLOSED → RATING → … → THANK_YOU`).

| Level | You can already… | This section adds |
|---|---|---|
| [0 — Beginner](#level-0--what-an-ab-test-actually-is) | ship features | why randomisation is the only thing that buys causality |
| [1 — Working](#level-1--running-one-test-correctly) | read a dashboard | hypothesis, metric, sizing, honest readout |
| [2 — Frontend engineer](#level-2--implementing-it-in-a-frontend-codebase) | write React | bucketing, exposure, flicker, QA, testing experiments |
| [3 — Senior](#level-3--making-the-result-trustworthy) | run a test | SRM, peeking, segments, interference, the ways results lie |
| [4 — Staff](#level-4--platform-portfolio-and-organisation) | run many tests | platform design, variance reduction, layers, governance, when *not* to test |
| [5 — Interview](#level-5--interview-drills) | all of the above | drills, model answers, a system-design outline |

---

## Level 0 — What an A/B test actually is

### The one-sentence version

Split users **randomly** into two groups, show each group a different version,
compare the metric. Randomisation is the whole trick: it makes the two groups
identical *in expectation* on everything — device, country, tenure, mood, the
weather — so any difference you measure afterwards can be attributed to the one
thing you changed.

### Why not just ship it and watch the graph

The tempting alternative is the **before/after** comparison: ship on Tuesday,
compare this week to last week.

> We shortened the thank-you toast on 3 March. Second-rating rate went from
> 6.0% to 6.8%. Ship it everywhere.

Everything else that changed in that week is inside your "result": a marketing
push, payday, an iOS release, a competitor outage, the weather in the
Netherlands. This is the same reasoning error as "I took vitamin C and my cold
went away in a week". A/B testing removes it by running both versions *at the
same time* on *comparable people*.

### Vocabulary (learn these six)

| Term | Meaning | In this repo |
|---|---|---|
| **Unit** | The thing you randomise. Usually a user. | `unitId` in [`assign.ts`](../src/features/experiments/core/assign.ts) |
| **Variant / arm** | One version. `control` is today's behaviour, `treatment` is the change. | `Variant` in [`types.ts`](../src/features/experiments/types.ts) |
| **Assignment** | Which arm a unit belongs to. | `Assignment` |
| **Exposure** | The moment the unit actually *sees* its arm. Analysis runs on this, not on assignment. | `ExposureEvent` |
| **Metric** | The number you compare. | `twoProportionZTest` inputs |
| **Lift** | The difference. Absolute (+2pp) or relative (+20%). Always say which. | `TestResult.absoluteLift` / `relativeLift` |

### A worked toy example

10,000 users per arm. Control: 1,000 conversions (10.0%). Treatment: 1,200
(12.0%).

```ts
import { twoProportionZTest } from './src/features/experiments';

twoProportionZTest(
  { units: 10_000, conversions: 1_000 },
  { units: 10_000, conversions: 1_200 },
);
// absoluteLift: 0.02      → +2 percentage points
// relativeLift: 0.20      → +20% relative
// zScore:       4.52
// pValue:       6.2e-6
// confidenceInterval: [0.0113, 0.0287]
// significant:  true
```

Now the same +20% relative lift on 100 users per arm (10 vs 12 conversions):
`pValue ≈ 0.66`. **Identical lift, opposite conclusion.** The effect size didn't
change; the evidence did. That gap between "what I saw" and "what I can
conclude" is the entire subject.

### The beginner's five rules

1. Randomise; never compare time periods.
2. Decide the metric **before** you look.
3. Decide the sample size **before** you start.
4. Don't stop the test early because it looks good.
5. "Not significant" means *we didn't learn*, not *no difference*.

---

## Level 1 — Running one test correctly

### 1.1 Write the hypothesis down

A usable hypothesis names the change, the mechanism, the metric and the size.
The mechanism is what makes the result *learnable* — without it, a win teaches
you nothing transferable.

> **Because** the thank-you toast holds the modal for 2s after the user has
> already finished, **we believe** cutting it to 1.2s will make the flow feel
> finished sooner, **which will show up as** a higher second-rating rate within
> the session, **by at least +1pp** on a 6.0% base. We'll be wrong if
> comprehension drops — watched via the Trustpilot CTA click-through.

That is exactly the experiment defined in
[`examples/feedbackExperiments.tsx`](../src/features/experiments/examples/feedbackExperiments.tsx).

### 1.2 Pick the metric — and the guardrails

Three tiers, and you need all three:

- **OEC / primary metric** (one, chosen in advance): the thing the change is
  supposed to move. Second-rating rate.
- **Guardrails**: things you refuse to damage even for a win. Submit-success
  rate, error rate, p95 latency, crash-free sessions, complaint volume, opt-out
  rate.
- **Diagnostics**: everything else, for explaining *why*. Click-through per
  step, time-in-state.

Two failure modes to name out loud:

- **Proxy trap.** Clicks are easy to move and easy to move *badly* — a
  misleading label raises clicks and lowers trust. Pick the metric closest to
  the value you actually want; if it's too slow (retention, LTV), use a proxy
  but keep the long-term metric as a guardrail and verify the link at least
  once with a long-running holdout.
- **Metric sensitivity.** Revenue per user is what the business cares about and
  is far too noisy to move detectably on one widget. Prefer a sensitive,
  causally-downstream metric over a noble, immovable one.

### 1.3 Size the test *before* you run it

Four inputs, one output:

| Input | Symbol | Typical | Meaning |
|---|---|---|---|
| Baseline rate | p | 6% | today's conversion |
| Minimum detectable effect | MDE | +1pp | smallest effect worth shipping |
| Significance level | α | 0.05 | false-positive rate you accept |
| Power | 1−β | 0.80 | chance of catching a real effect of MDE size |

```ts
import { sampleSizePerVariant, daysToRun, detectableEffect } from './src/features/experiments';

sampleSizePerVariant({ baselineRate: 0.06, minimumDetectableEffect: 0.01 });
// 9,540 exposed users per arm

daysToRun(9_540, 1_500); // 7 days at 1,500 exposed users per arm per day
```

Numbers worth internalising, all at α=.05, power=.80:

| Baseline | MDE | Per arm |
|---|---|---|
| 10% | +2pp (+20% rel) | 3,841 |
| 6% | +1pp (+17% rel) | 9,540 |
| 6% | +0.6pp (+10% rel) | 25,740 |
| 30% | +1pp (+3% rel) | 33,275 |
| 2% | +0.1pp (+5% rel) | 315,206 |

**Halving the MDE quadruples the sample.** Sensitivity is expensive; that single
fact drives most experimentation strategy (and all of [variance
reduction](#43-variance-reduction-buy-sensitivity-instead-of-traffic)).

Run the calculation in the other direction too, because it kills bad experiments
early:

```ts
detectableEffect({ baselineRate: 0.10, samplePerVariant: 1_000 });
// { absolute: 0.0376, relative: 0.376 }
```

1,000 users per arm can only detect a **+38% relative** change. If your feature
plausibly moves the metric by 3%, this test cannot succeed — it will return
"not significant" no matter what you build. Don't run it; pick a bigger change,
a more sensitive metric, or a different method.

### 1.4 Run it for whole weeks

Behaviour is weekly-seasonal — weekday vs weekend users are different people
doing different things. A test that starts Tuesday and ends Friday measures
"Tue–Fri users". Always run in multiples of 7 days, and at least one full week
even if the sample arrives in two days.

### 1.5 Read the result honestly

```
p-value = P(seeing a difference at least this large | the arms are truly identical)
```

It is **not** the probability that the treatment works, and 1−p is not "95%
confident it wins".

The confidence interval is the more useful object because it carries units:

- CI `[+0.4pp, +2.6pp]` → real, but the size is uncertain; check the low end is
  still worth shipping.
- CI `[−0.2pp, +2.4pp]` → not significant, and consistent with anything from a
  small loss to a solid win. **Underpowered, not "no effect".**
- CI `[−0.1pp, +0.15pp]` → genuinely flat. This is the only shape that licenses
  "no difference", and it needs a lot of traffic.

Decision table:

| Result | Action |
|---|---|
| Significant win, guardrails clean | Ship |
| Significant win, guardrail damaged | Escalate — it's a trade-off decision, not an engineering one |
| Significant loss | Don't ship. Write down why; a loss with a mechanism is a genuinely valuable result |
| Flat, well-powered | Ship the cheaper/simpler arm, and stop investing in this idea |
| Flat, underpowered | You learned nothing. Either commit more traffic or drop it |

### 1.6 The beginner mistakes, in the order people make them

1. Stopping the moment p < 0.05 (see [3.2](#32-peeking-the-most-common-way-to-fool-yourself) — it triples your false-positive rate).
2. Picking the metric after seeing the data.
3. Slicing until something is significant (20 slices, α=.05, one "win" by luck).
4. Reading a test that never had the traffic to answer.
5. Excluding "weird" users after assignment — that un-randomises the experiment.
6. Testing two changes at once and not knowing which one worked.

---

## Level 2 — Implementing it in a frontend codebase

Everything below is implemented in this repo. Run `npm test` — 120 tests, of
which ~90 cover the framework.

```
src/features/experiments/
├── types.ts                     Experiment / Variant / Assignment / ExposureEvent
├── core/
│   ├── hash.ts                  deterministic bucketing (FNV-1a + avalanche)
│   ├── assign.ts                pure assignment: gates, traffic, weights, overrides
│   └── overrides.ts             ?ab.<key>=<variant> QA plumbing
├── ExperimentProvider.tsx       React context, useExperiment, exposure logging
├── analysis/
│   ├── stats.ts                 sizing, z-test, SRM, Benjamini–Hochberg
│   └── cuped.ts                 variance reduction with pre-experiment data
└── examples/feedbackExperiments.tsx   the widget experiment, wired end to end
```

### 2.1 Bucketing must be deterministic

The most common first implementation is also the worst:

```ts
// ✗ Re-rolls on every render, every reload, every device.
const variant = Math.random() < 0.5 ? 'control' : 'treatment';
```

A user who sees the treatment, refreshes, and sees the control has a broken
product *and* has contaminated both arms. Assignment must be a pure function of
`(experiment, unitId)`:

```ts
hash = fmix32(fnv1a32(`${salt}:${unitId}`))   // → uint32
bucket = hash / 2^32                          // → [0, 1)
```

Four properties, each of which is a bug when missing
([`hash.ts`](../src/features/experiments/core/hash.ts)):

1. **Deterministic** — same input, same bucket, forever. No storage needed;
   this is why bucketing can happen on the server, the client and the analysis
   job and all three agree.
2. **Salted per experiment** — `salt` defaults to the experiment key. Without
   it every experiment splits the population the *same* way, so experiment B's
   treatment group is exactly experiment A's treatment group and their effects
   are permanently confounded.
3. **Uniform** — measured, not assumed. Raw FNV-1a on sequential ids
   (`user-1`, `user-2`, …) puts deciles between 1,598 and 2,398 where 2,000 is
   expected — up to **9.5σ** off. Adding the murmur3 `fmix32` avalanche step
   brings the worst decile to 1.3σ. Sequential ids are exactly what a real
   database hands you, so this is not a theoretical concern.
4. **Fast** — it runs on every render. Not SHA-256 via WebCrypto (async), not a
   network call.

Two *independent* draws per experiment, and this matters more than it looks:

```ts
trafficBucket(salt, unitId)  // hash of `${salt}#traffic:${unitId}` — am I in the ramp?
variantBucket(salt, unitId)  // hash of `${salt}#variant:${unitId}` — which arm?
```

Deriving both from one draw makes ramping unsafe: raising traffic from 10% to
20% shifts the variant boundary and can move already-enrolled users between
arms. With two draws, the variant draw never changes when traffic changes.
[`assign.test.ts`](../src/features/experiments/core/assign.test.ts) asserts this
directly by ramping 1% → 5% → 25% → 50% → 100% and checking that no enrolled
unit ever changes arm.

### 2.2 Assignment ≠ exposure (the expensive one)

This is the mistake that costs the most money, because it doesn't look like a
bug — the test runs, the dashboard fills, the numbers are just quietly wrong.

The feedback widget is mounted on every page but *opens* on almost none of them.
If you log an exposure when it mounts, you enrol ~50× more users than can
possibly see the change, and every one of them contributes an identical zero to
both arms. The measured effect shrinks toward zero — **dilution** — while your
traffic bill stays the same.

```tsx
// ✗ everyone who loads the page is "in the experiment"
const { payload } = useExperiment(THANK_YOU_DELAY);

// ✓ only users who actually see the modal
const { payload } = useExperiment(THANK_YOU_DELAY, { exposed: open });
```

The rules the framework enforces:

- Exposure fires **in an effect**, never during render. React can render and
  throw away a component (StrictMode, Suspense retries, concurrent re-entry);
  each of those would fire a phantom exposure.
- Exposure fires **once** per `(unit, experiment, variant)` — a component
  re-rendering 60×/s should not put 60 events/s on the wire.
- Exposure fires **only when `enrolled === true`**. Held-back users, ineligible
  users and QA overrides all render something, but none of them may enter the
  analysis.

Corollary for the analysis side: **compute the metric only from the exposure
point forward.** A conversion that happened before the user saw the treatment
cannot have been caused by it.

### 2.3 Flicker, SSR, and where assignment happens

Client-side assignment has a visible failure mode: the page renders control,
the flag arrives, the page swaps to treatment. The user sees a flash, and worse,
some users bounce *during* the flash — asymmetrically, because the treatment
flash may be slower. That is a real bias, not a cosmetic issue.

| Approach | Flicker | Latency | Notes |
|---|---|---|---|
| Assign at the edge/server, inline the decision into HTML | None | None | Best. Needs a stable id in a cookie |
| Ship the whole config in the first payload, assign synchronously in the client | None | None | What this repo does — assignment is a pure function, no async |
| Fetch flags from a service on mount | Yes | 50–300ms | Only acceptable behind a loading state that both arms share |
| Anti-flicker snippet (hide `<body>` until flags land) | None | Worse | Hides the flicker by delaying *everything*. Common, and a real performance cost |

If you must render before the decision is known, render the control **and don't
log an exposure** until the treatment could actually have been seen.

For anonymous users, the unit id lives in a first-party cookie or
`localStorage`. Two consequences you should state before someone else does:
clearing storage re-randomises that user, and consent rules may forbid setting
it at all before opt-in — in which case pre-consent traffic is simply outside
the experiment (and must be excluded from *both* arms, not just one).

### 2.4 QA and overrides

If a designer can't open the treatment, nobody reviews it.
[`overrides.ts`](../src/features/experiments/core/overrides.ts) supports
`?ab.feedback-thankyou-delay=snappy` (shareable in a ticket, persisted to
storage for the session) and `?ab.reset=1`.

The critical property: **a forced assignment is never `enrolled`**, so QA
sessions cannot contaminate the readout. It's asserted in the tests, and it's
the kind of detail that separates a toy from a framework.

### 2.5 Feature flags, experiments, and rollouts are three different things

| | Purpose | Split | Ends with |
|---|---|---|---|
| **Feature flag** | Decouple deploy from release; kill switch | Usually 0% or 100% | Flag deleted |
| **Rollout / ramp** | Limit blast radius | 1% → 5% → 25% → 100% | Feature at 100% |
| **Experiment** | *Measure* a causal effect | Fixed split, held for the full horizon | A decision, then cleanup |

One mechanism can serve all three (this framework does), but conflating the
*intent* causes real damage: ramping a live experiment changes the population
mid-flight, and killing an arm because the on-call engineer panicked destroys
the sample. Every experiment still needs a kill switch — set `status: 'PAUSED'`
and everyone falls back to control — but pausing ends the experiment; it does
not pause it.

### 2.6 Testing experiment code

Three things to test, all shown in this repo:

1. **The pure layer, exhaustively.** `assign()` takes no globals and touches no
   React, so you can test the traffic ramp with 20,000 synthetic ids in
   milliseconds — including statistical properties (50/50 within tolerance,
   90/10 weights, ramp stickiness).
2. **Each arm renders correctly.** Force the variant via `overrides` and assert
   the UI. No mocking of the hash, no `jest.mock` of a module —
   `ExperimentProvider` takes the overrides as a prop.
3. **Exposure fires exactly when it should.** Inject `track` as a jest mock and
   assert it's *not* called while the widget is closed, called once when it
   opens, and never called for a forced variant.

```tsx
render(
  <ExperimentProvider unitId="user-1" experiments={feedbackExperiments}
                      overrides={{ 'feedback-thankyou-delay': 'snappy' }} track={track}>
    <ExperimentalFeedbackWidget open submitFeedback={jest.fn()} />
  </ExperimentProvider>,
);
fireEvent.click(screen.getByRole('button', { name: /positive/i }));
act(() => { jest.advanceTimersByTime(1200); });
expect(screen.queryByText(/thanks for your feedback/i)).not.toBeInTheDocument();
```

The injected `track` and `now` follow the same dependency-injection idiom the
feedback widget already uses for `submitFeedback` — the seam exists so tests
never patch modules.

### 2.7 Keep experiments out of your components

Note that `FeedbackWidget` contains no experiment code at all. The experiment
lives in a wrapper, `ExperimentalFeedbackWidget`, in one file. Two reasons:

- The component stays directly testable and has one behaviour, not 2ⁿ.
- **Experiments are temporary; components are not.** Cleanup day is a file
  deletion instead of an archaeology project. Untracked, un-deleted flags are
  the standard end-state of an experimentation programme that skipped this —
  every one of them is a live branch nobody tests.

### 2.8 The exposure event contract

The analysis is only as good as this record. Minimum viable schema:

```jsonc
{
  "experimentKey": "feedback-thankyou-delay",
  "variant": "snappy",
  "unitId": "user-1",          // must join to the metric tables
  "timestamp": 1700000000000,  // must be ≤ any attributed conversion
  // in production, also:
  "assignmentSalt": "feedback-thankyou-delay",  // proves which randomisation produced this
  "sdkVersion": "2.3.1",       // so a bucketing bug is scopeable
  "appVersion": "8.42.0"
}
```

Log it once per user per experiment, ship it on the same pipeline as your
metrics (a separate pipeline means separate loss rates, which manifests as an
SRM), and make it idempotent.

---

## Level 3 — Making the result trustworthy

Everything so far produces *a number*. This section is about the number being
*true*. Senior-level work is mostly here.

### 3.1 Sample Ratio Mismatch — run this check first, always

You configured 50/50 and got 503,000 / 497,000. That's 0.3% off; surely fine?

```ts
checkSampleRatioMismatch([503_000, 497_000], [1, 1]);
// chiSquare: 36, pValue: 2.0e-9, mismatch: true
```

A 1-in-500-million coincidence. It is not luck — it's a bug, and the bug that
dropped 6,000 users did not drop them at random. Whatever selected them
(slow devices timing out before the treatment's extra request, a redirect that
loses Safari users, a bot filter that hits one arm harder, exposure logged later
in one arm) has almost certainly biased the *metric* too, usually in the
direction that makes the treatment look good.

The rule: **SRM at p < 0.001 means stop and debug. Do not interpret the
metrics.** No exceptions, no "but the effect is huge" — a huge effect on a
broken split is exactly what a broken split looks like.

Usual suspects, in order of frequency: exposure logged at different points in
the two arms; redirect-based tests; bot/crawler filtering applied after
assignment; one arm crashing; the analysis joining on the wrong id; a ramp
change mid-experiment.

### 3.2 Peeking — the most common way to fool yourself

Checking the dashboard daily and stopping when p < 0.05 does not preserve α.
Simulated here, two arms with **no real difference**, α = 0.05:

| Looks | False-positive rate |
|---|---|
| 1 (fixed horizon) | 5.1% |
| 5 | 14.5% |
| 10 | 19.4% |
| 20 | 24.4% |

Ten peeks means one in five null experiments produces a "significant win". Run
an experimentation programme like that and the majority of your shipped wins are
noise — which is both invisible and permanent, because nobody re-tests a win.

Three legitimate fixes:

1. **Fixed horizon.** Decide n up front, look once. Simplest, and what
   `sampleSizePerVariant` is for. (Watching *guardrails* daily is fine and
   required — you're monitoring for harm, not deciding the outcome.)
2. **Sequential testing / alpha spending** (O'Brien–Fleming, Pocock): spend α
   across planned interim looks, with a stricter threshold early.
3. **Always-valid inference** (mSPRT, confidence sequences): valid at *every*
   moment, at the cost of ~20–40% more traffic for the same power. This is what
   platforms with "always-on" dashboards use; it's how you give everyone a live
   number without giving everyone a false-positive machine.

Never fix it by silently extending a test that's "almost significant" — that's
peeking with extra steps.

### 3.3 Multiple comparisons

One test, twenty metrics, α = 0.05 → expect one "significant" result from pure
chance per readout. Same for twenty segments, or four variants against one
control.

- **Primary metric**: one, pre-registered, uncorrected.
- **Secondary/diagnostic metrics**: correct the family. Benjamini–Hochberg
  controls the false *discovery* rate and is far less brutal than Bonferroni:

  ```ts
  benjaminiHochberg([0.001, 0.008, 0.039, 0.041, 0.9], 0.05);
  // [true, true, false, false, false]
  ```
- **Segments**: treat as hypothesis-*generating*. A segment win is a reason for
  a follow-up experiment, never a reason to ship to that segment.

### 3.4 Novelty and primacy

- **Novelty**: existing users click the new thing because it's new. The effect
  decays. A win that shrinks every day across a two-week test is the signature.
- **Primacy**: users are slower with a changed UI they already knew, so a good
  change looks bad at first.

Both are handled the same way: run longer than feels necessary, plot the effect
by day-since-exposure (not calendar day), and check new-user and existing-user
segments separately — new users can't experience either, which makes them a
useful control on the phenomenon itself.

### 3.5 Segments, Simpson's paradox, and heterogeneity

Treatment can lose overall while winning in every segment, if the arms have
different segment mixes (which SRM detection is partly there to catch). Slicing
by device / platform / tenure / country is genuinely valuable for *understanding*
— but pre-register the slices you care about, correct for multiplicity, and
treat surprises as leads.

The honest framing: an A/B test estimates an **average** treatment effect. If
the change helps novices and hurts experts, the average can be zero and shipping
it can still be right (or wrong) — that's a product decision the average cannot
make for you.

### 3.6 The data itself lies to you

- **Bots and scrapers** inflate one arm and flatten effects. Filter *before*
  assignment, on rules that cannot depend on the treatment.
- **Outliers** wreck continuous metrics: one user's €50,000 transfer moves
  revenue-per-user. Winsorise (cap at the 99th percentile), or use a bounded
  metric, and pre-register whichever you choose.
- **Ratio metrics** (clicks per session, where the *denominator* is also random)
  break the naive standard error. Use the delta method or bootstrap.
- **Clustered units.** If you randomise by user but analyse by session, the
  sessions of one user are correlated; the naive SE is too small and everything
  looks significant. Analyse at the unit of randomisation, or use cluster-robust
  errors. In banking this bites specifically: joint accounts, households and
  business teams are not independent people.
- **Twyman's law.** Any figure that looks interesting or unusual is usually
  wrong. A +40% lift on a copy change is a bug in your pipeline until proven
  otherwise. Verify big wins by re-running them.

### 3.7 Interference — when units affect each other

The whole framework assumes one unit's treatment doesn't change another unit's
outcome (SUTVA). It breaks when:

- **Social/referral features**: a treated user invites an untreated one, so the
  control arm gets a piece of the treatment. Effects look smaller than they are.
- **Shared resources**: a treatment that consumes support capacity degrades
  control's experience. Effects look bigger than they are.
- **Marketplaces**: better ranking for treated buyers takes inventory from
  control buyers — a pure cannibalisation illusion.

Fixes: cluster randomisation (randomise households, regions, or social graph
components instead of users), **switchback** tests (flip the whole system
between arms over time windows — standard for pricing and matching systems), or
budget-split designs. All of them cost sensitivity; pick one only when
interference is real.

### 3.8 Ethics and regulation — sharper for a bank

Not everything may be experimented on, and in a regulated financial institution
the line is legal, not cultural:

- **Regulatory disclosures, consent flows, fee and rate disclosures, security
  warnings** — these have prescribed content. You may test comprehension and
  clarity, not whether the disclosure appears.
- **Informed consent / GDPR.** Under GDPR the lawful basis matters, and
  ePrivacy governs the identifier in storage. Experiment ids are personal data;
  they need retention limits and inclusion in DSAR/erasure flows.
- **Fairness.** Check the effect doesn't concentrate harm in a protected or
  vulnerable group; "positive on average" can hide a group it hurt.
- **Dark patterns.** An experiment optimising a metric will happily find a
  design that increases conversions by confusing people. The guardrail set is
  where you encode "we won't win that way" — complaint rate, cancellation
  regret, support contacts.
- **Blast radius.** Payments and balances are one-way doors. Ramp with a kill
  switch, not with a fixed 50/50 from minute one.

---

## Level 4 — Platform, portfolio, and organisation

Staff-level questions are rarely "how do you compute a p-value". They are: how
do a hundred engineers run trustworthy experiments without a statistician each,
and how do you keep the results honest for years.

### 4.1 Platform architecture

```
┌────────────────┐   config    ┌───────────────────┐
│ Experiment     │────────────▶│ Edge / SSR        │  assignment inlined
│ config service │             │ + client SDK      │  into first response
│ (versioned,    │             └─────────┬─────────┘
│  audited)      │                       │ exposure events
└────────────────┘                       ▼
                              ┌────────────────────┐
                              │ Event pipeline     │  same pipeline as metrics
                              └─────────┬──────────┘
                                        ▼
┌──────────────┐  metric defs  ┌────────────────────┐   ┌────────────────────┐
│ Metric       │──────────────▶│ Analysis engine    │──▶│ Scorecard + alerts │
│ repository   │               │ (sizing, SRM,      │   │ (SRM, guardrails)  │
└──────────────┘               │  CIs, segments)    │   └────────────────────┘
                               └────────────────────┘
```

Design commitments worth defending in an interview:

- **One assignment implementation, shared by client, server and the analysis
  job.** Two implementations *will* diverge, and the divergence looks like an
  SRM you can't reproduce. That's why `assign()` here is pure, dependency-free
  and framework-agnostic — it's the piece you'd extract into a shared package.
- **Config is versioned and audited.** "Who changed the traffic split on day
  four, and when" must be answerable, because it invalidates the analysis.
- **Metrics are defined once, centrally.** Per-team SQL means two teams'
  "conversion rate" disagree and nobody can compare experiments.
- **Guardrails and SRM alert automatically.** Trust cannot depend on whether the
  owner remembered to check.
- **The scorecard is the product.** Sizing, SRM, primary + guardrails with CIs,
  pre-registered segments, and a recommendation — generated identically for
  every experiment so nobody can shop for a favourable analysis.

### 4.2 Experiment interactions and layers

Two experiments on the same button will interact. Options, in increasing order
of sophistication:

- **Independent salting** (this framework's default): experiments are
  orthogonal, so effects average out across the other experiment's arms. Correct
  *on average*, which is enough for most cases and costs nothing.
- **Mutual exclusion groups**: a shared bucket space so two conflicting
  experiments never see the same user. Costs traffic; use it when the
  interaction is real, not hypothetical.
- **Layers / universes** (Google's overlapping-experiments model): the traffic
  is divided into layers, one experiment per layer per user, so unrelated
  experiments run concurrently at full traffic while related ones share a layer.
  This is how a platform runs hundreds of concurrent experiments.

### 4.3 Variance reduction — buy sensitivity instead of traffic

Since halving the MDE quadruples the sample, the highest-leverage platform
investment is reducing variance rather than adding traffic.

**CUPED** ([`cuped.ts`](../src/features/experiments/analysis/cuped.ts)): adjust
the metric using a pre-experiment covariate X (last month's value of the same
metric, prior-week sessions):

```
Y' = Y − θ(X − X̄),   θ = Cov(Y, X) / Var(X)
```

X is measured before the experiment, so it cannot be affected by the treatment:
the difference between arms is unchanged (unbiased), but the variance drops to
`Var(Y)·(1 − ρ²)`.

| Correlation ρ | Variance remaining | Traffic saved for the same power |
|---|---|---|
| 0.5 | 75% | 25% |
| 0.7 | 51% | 49% |
| 0.9 | 19% | 81% |

```ts
cupedCompare(
  { metric: controlY, covariate: controlPrePeriodY },
  { metric: treatmentY, covariate: treatmentPrePeriodY },
);
// { theta, varianceReduction: 0.81, difference, confidenceInterval, pValue }
```

The one rule: **the covariate must be strictly pre-treatment.** A "pre-period"
that overlaps the ramp bakes the treatment effect into the correction and
biases the result — precisely the failure that's hardest to spot, because the
result looks *better*.

Related levers: stratified sampling / post-stratification, capping outliers,
choosing a less noisy metric, and triggering analysis on the sub-population that
could actually respond.

### 4.4 Choosing the randomisation unit

| Unit | Use when | Cost |
|---|---|---|
| User / account | Default. Consistent experience, supports long-term metrics | Needs identity; anonymous users need a durable id |
| Device / cookie | Logged-out surfaces | Same person on two devices splits across arms |
| Session | Effect is entirely within-session and you need sensitivity | Inconsistent UX; invalid for retention metrics |
| Cluster (household, region, graph component) | Interference between users | Much lower effective n; needs cluster-robust analysis |
| Time slice (switchback) | System-level effects: pricing, matching, ranking | Temporal confounding; needs careful window design |

For a bank, "user" and "account" are not the same unit, and joint accounts break
independence. Say which you're randomising and why.

### 4.5 Beyond the fixed-horizon t-test

- **Sequential / always-valid**: continuous monitoring without α inflation. The
  right default for a self-serve platform where everyone watches dashboards.
- **Bayesian**: gives P(treatment > control) and expected loss, which is what
  decision-makers actually want. Not a licence to peek — the stopping rule still
  affects the operating characteristics; it just changes what you're
  guaranteeing.
- **Non-inferiority tests**: for migrations ("the rewrite must not be worse by
  more than 0.5pp"). Different null hypothesis, and the correct tool for the
  most common infra experiment.
- **Quasi-experiments** (diff-in-diff, synthetic control, interrupted time
  series, regression discontinuity): when randomisation is impossible — a
  country launch, a pricing change, a regulatory rollout. Weaker causal claims,
  stated as such.
- **Bandits**: when the goal is to *maximise* reward during the test rather than
  to *learn* the effect size — headline selection, creative rotation. Bad at
  producing a trustworthy estimate; good at not wasting traffic on a loser.
  Know the trade-off; it's a common interview probe.

### 4.6 The portfolio view

Individual tests are not the unit of value; the programme is.

- **Most experiments fail.** Industry-reported win rates sit around 10–30%. That
  is the system working: cheap failures replacing expensive convictions. If your
  win rate is 80%, you're either peeking or only testing sure things.
- **Long-term holdouts.** Keep 1–5% of users on "no new features" for a quarter
  to measure what all your shipped wins actually added up to. The sum of
  short-term wins routinely exceeds the measured long-term effect — that gap is
  the most valuable number your platform can produce.
- **Institutional memory.** A searchable registry of every experiment, its
  hypothesis, result and decision. It stops re-runs of settled questions, and
  it's the raw material for calibrating what your team believes about users.
- **Velocity metrics.** Experiments per quarter, time-to-first-result, share of
  launches with a measured effect. These are the platform's own OEC.

### 4.7 When *not* to run an A/B test

Staff-level judgement is mostly knowing when the answer is "don't":

- **Not enough traffic.** If the MDE calculation says three years, the test is
  theatre. Use qualitative research, or ship on judgement and monitor.
- **One-way doors and legal requirements.** Some things get decided, not tested.
- **Strategy bets.** "Should we build a new product line" is not a button colour;
  the effect takes years and the mechanism isn't isolable.
- **Obvious bug fixes.** Testing whether users prefer the crash is a waste of a
  ramp.
- **When you won't act on either result.** If both outcomes lead to shipping,
  skip the experiment and spend the traffic on a question you'd act on.

---

## Level 5 — Interview drills

**"We saw a 20% lift after two days. Ship?"**
No. Two days is not a full week (weekly seasonality), it's very likely
underpowered, and stopping because it looks good is peeking — at ten looks the
false-positive rate is ~19%, not 5%. I'd check the pre-registered sample size,
check SRM, and let it run the planned horizon. A 20% lift on a small change also
trips Twyman's law: I'd verify the pipeline before I believed it.

**"The test is flat. Does that mean the feature doesn't matter?"**
Only if it was powered to say so. I'd compute the detectable effect for the
sample we got: at 1,000 users per arm on a 10% baseline, we could only detect
+38% relative — so "flat" there means "we learned nothing". If the CI is
`[−0.1pp, +0.15pp]`, that's a real null and I'd ship the simpler arm.

**"Design experimentation for a feature that only 2% of users reach."**
Trigger the analysis on the reachable population — assign everyone, but exposure
and analysis only for users who hit the entry point. That is exactly the
dilution problem: including the other 98% multiplies the required sample by ~50.
Then check the sizing at that reduced traffic and, if it's still infeasible,
either pick a more sensitive metric closer to the change, apply CUPED, or accept
that this ships on judgement with guardrail monitoring.

**"How do you avoid flicker in a client-side test?"**
Assign at the edge or inline the config into the first HTML payload so the first
paint is already correct. Failing that, render control and don't log exposure
until the treatment could have been seen; never hide the whole page behind an
anti-flicker snippet unless you accept the performance cost. Bucketing must be
synchronous and pure, which is why it's a hash rather than a network call.

**"Your 50/50 test came back 51/49 on 100k users. Care?"**
Yes — `checkSampleRatioMismatch([51000, 49000], [1,1])` gives χ² = 40, p ≈ 2.5e-10.
That's a pipeline bug, and whatever dropped those users probably biased the
metric too. Stop, debug, don't interpret.

**"Two teams want to test on the same screen this sprint."**
Independent salts mean they're orthogonal and both can run — each averages over
the other's arms. If the changes genuinely interact (both alter the same CTA),
I'd put them in a mutual-exclusion layer and accept the traffic cost, or
sequence them. The thing I would not do is let them both ship and attribute the
combined effect to whoever reads the dashboard first.

**"System design: build an experimentation platform."**
Config service (versioned, audited) → assignment SDK shared by edge/server/client
(pure, deterministic, salted) → exposure events on the same pipeline as metrics
→ central metric repository → analysis engine producing a fixed scorecard (SRM,
primary, guardrails with CIs, pre-registered segments) → automated alerting →
registry of results. Then the hard parts: layers for concurrency, variance
reduction for sensitivity, always-valid inference so dashboards can be watched
safely, and long-term holdouts to check the whole programme is real.

---

## Cheat sheet

```
Sample size per arm (two proportions, two-sided):
    n = ( z_{1−α/2}·√(2p̄(1−p̄)) + z_{1−β}·√(p₁(1−p₁)+p₂(1−p₂)) )² / (p₂−p₁)²

Test statistic (pooled SE):    z = (p₂−p₁) / √( p̄(1−p̄)(1/n₁+1/n₂) )
CI on the difference (unpooled): (p₂−p₁) ± z_{1−α/2}·√( p₁(1−p₁)/n₁ + p₂(1−p₂)/n₂ )
SRM:                            χ² = Σ (Oᵢ−Eᵢ)²/Eᵢ,  df = k−1,  stop if p < 0.001
CUPED:                          Y' = Y − θ(X−X̄),  θ = Cov(Y,X)/Var(X),  Var ↓ by ρ²

z_{0.975} = 1.96   z_{0.995} = 2.576   z_{0.80} = 0.842   z_{0.90} = 1.282

Halve the MDE  → 4× the sample.
Double the arms → more traffic and a multiplicity correction.
```

**Pre-launch checklist**

- [ ] Hypothesis with a mechanism, written down
- [ ] One primary metric + guardrails, pre-registered
- [ ] Sample size and end date computed, whole weeks
- [ ] Randomisation unit chosen and defensible
- [ ] Exposure fires where the user can actually see the change
- [ ] Both arms QA'd via forced overrides
- [ ] Kill switch tested
- [ ] SRM + guardrail alerting on

**Pre-decision checklist**

- [ ] SRM clean (p > 0.001)
- [ ] Planned horizon reached; no early stop
- [ ] Primary read with its CI, not just p
- [ ] Guardrails checked, multiplicity corrected on secondaries
- [ ] Effect stable across days (no novelty decay)
- [ ] Result and decision written to the registry
- [ ] Losing arm's code deleted

---

## Glossary

**α** false-positive rate · **Power (1−β)** chance of detecting a true effect of
MDE size · **MDE** minimum detectable effect · **OEC** overall evaluation
criterion, the single primary metric · **SRM** sample ratio mismatch ·
**Dilution** including units that can't see the change, shrinking the measured
effect · **Novelty effect** temporary lift from newness · **SUTVA** the
no-interference assumption · **CUPED** variance reduction with pre-experiment
data · **Holdout** a group deliberately kept on the old experience ·
**Switchback** randomising time windows instead of users · **Always-valid
inference** statistics that stay correct under continuous monitoring.

## Further reading

- Kohavi, Tang & Xu — *Trustworthy Online Controlled Experiments* (the standard
  reference; chapters on SRM, metrics and institutional memory are the core)
- Kohavi et al. — *Seven Rules of Thumb for Web Site Experimenters* (KDD 2014)
- Deng, Xu, Kohavi & Walker — *Improving the Sensitivity of Online Controlled
  Experiments by Utilizing Pre-Experiment Data* (WSDM 2013) — the CUPED paper
- Tang et al. — *Overlapping Experiment Infrastructure* (KDD 2010) — layers
- Johari, Koomen, Pekelis & Walsh — *Always Valid Inference* — sequential testing
