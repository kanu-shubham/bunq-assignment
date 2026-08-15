# Experiments

A small, dependency-free A/B testing framework: deterministic bucketing, React
bindings with honest exposure logging, and the statistics needed to size and
read a test. The full walkthrough — beginner to staff, with worked numbers — is
in [`docs/ab-testing.md`](../../../docs/ab-testing.md).

```
types.ts                       Experiment / Variant / Assignment / ExposureEvent
core/hash.ts                   FNV-1a + murmur3 avalanche → [0,1)
core/assign.ts                 pure assignment: status, audience, ramp, weights, overrides
core/overrides.ts              ?ab.<key>=<variant> QA plumbing
ExperimentProvider.tsx         context, useExperiment / useVariant, exposure logging
analysis/stats.ts              sizing, two-proportion z-test, SRM, Benjamini–Hochberg
analysis/cuped.ts              variance reduction from pre-experiment data
examples/feedbackExperiments.tsx   the feedback widget experiment, wired end to end
```

## Using it

```tsx
import { ExperimentProvider, useExperiment, resolveOverrides } from './features/experiments';

<ExperimentProvider
  unitId={user.id}                               // stable randomisation unit
  experiments={registry}
  attributes={{ country: user.country }}         // audience targeting inputs
  overrides={resolveOverrides(window.location.search, window.localStorage)}
  track={(event) => analytics.send('experiment_exposure', event)}
>
  <App />
</ExperimentProvider>;

// …in a component
const { variant, payload } = useExperiment<{ thankYouDelayMs: number }>(
  'feedback-thankyou-delay',
  { exposed: isModalOpen },      // ← exposure fires only when the user can see it
);
```

## Four properties worth knowing

**Assignment is pure.** `assign()` takes no globals, no React, no I/O — the same
function can run on the server, at the edge, in the client and in the analysis
job, and all four agree. Two implementations would drift, and drift looks like
an unreproducible SRM.

**Exposure ≠ assignment.** Exposure is logged in an effect, once per
`(unit, experiment, variant)`, and only when `enrolled === true`. Held-back,
ineligible and QA-forced units render an arm but never enter the analysis.
`{ exposed: false }` is how you keep a mounted-but-invisible treatment out of
the numbers.

**Ramping is safe.** Traffic and variant use independent hash draws, so raising
traffic 1% → 100% only ever adds units — nobody changes arm mid-flight. Asserted
in `assign.test.ts` across five ramp stages.

**Bucketing is measured, not assumed.** Raw FNV-1a on sequential ids is up to
9.5σ off uniform; the murmur3 finaliser brings it to 1.3σ. `hash.test.ts`
checks uniformity, independence across salts, and independence between the
traffic and variant draws.

## Tests

`npm test` — the framework contributes ~90 of the repo's 120 tests, covering
hash uniformity, every assignment gate, override precedence and storage
failures, exposure timing, the statistics against known reference values
(z₀.₉₇₅ = 1.959964, χ²₀.₀₅,₁ = 3.8415, n = 3,841 for 10% → 12%), and the widget
experiment end to end.
