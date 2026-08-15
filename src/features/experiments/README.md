# Experiments

A small, dependency-free A/B testing framework: deterministic bucketing, React
bindings with honest exposure logging, and the statistics needed to size and
read a test. Two walkthroughs, beginner to staff, with worked numbers:
[`docs/ab-testing.md`](../../../docs/ab-testing.md) (general + frontend) and
[`docs/ab-testing-ml.md`](../../../docs/ab-testing-ml.md) (ML engineering —
offline/online gap, triggered analysis, off-policy evaluation, interleaving,
feedback loops, LLM features).

```
types.ts                       Experiment / Variant / Assignment / ExposureEvent
core/hash.ts                   FNV-1a + murmur3 avalanche → [0,1)
core/assign.ts                 pure assignment: status, audience, ramp, weights, overrides
core/overrides.ts              ?ab.<key>=<variant> QA plumbing
ExperimentProvider.tsx         context, useExperiment / useVariant, exposure logging
analysis/stats.ts              sizing, two-proportion z-test, SRM, BH, exact binomial
analysis/cuped.ts              CUPED + MLRATE variance reduction
analysis/ratioMetrics.ts       delta method for clustered ratio metrics (CTR & friends)
analysis/offPolicy.ts          IPS / SNIPS / doubly robust + effective sample size
analysis/interleaving.ts       team-draft interleaving for ranking comparisons
analysis/sequential.ts         always-valid confidence sequences (safe to peek)
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

`npm test` — the framework contributes 136 of the repo's 164 tests, covering
hash uniformity, every assignment gate, override precedence and storage
failures, exposure timing, the statistics against known reference values
(z₀.₉₇₅ = 1.959964, χ²₀.₀₅,₁ = 3.8415, n = 3,841 for 10% → 12%), and the widget
experiment end to end.

The ML estimators are validated by simulation rather than by assertion of
remembered constants: IPS/SNIPS/DR recover a known policy value from simulated
logs and degrade exactly where support runs out; the clustered ratio test is
shown to disagree with the naive per-request test on skewed traffic (1.95×
standard error); and the confidence sequence holds a 0.2% false-positive rate
under continuous monitoring where the fixed-horizon test fires 19.0%.
