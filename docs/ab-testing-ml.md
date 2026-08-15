# A/B Testing for ML Engineers — beginner to staff

The general guide ([`ab-testing.md`](./ab-testing.md)) covers the shared core:
randomisation, hypotheses, sizing, p-values and CIs, SRM, peeking, multiple
comparisons. This one covers what changes when **the treatment is a model** —
and almost everything changes, because a model is not a button:

| A button… | A model… |
|---|---|
| does the same thing for every user | does a different thing for every user, so the effect is heterogeneous by construction |
| is either shipped or not | has an offline score that is *supposed* to predict the online effect, and usually doesn't |
| is deterministic | is retrained, drifts, and can learn from the very traffic you're measuring |
| affects the users who see it | affects its own future training data, and often the other arm's |
| can be evaluated one way | can be evaluated five ways, four of which are cheaper than an A/B test |

Everything below is backed by tested code in
[`src/features/experiments/analysis/`](../src/features/experiments/analysis) —
off-policy estimators, clustered ratio metrics, interleaving, always-valid
inference, MLRATE. Numbers quoted in the text are measured by those tests, not
recalled.

| Level | This section adds |
|---|---|
| [0](#level-0--why-your-offline-metric-doesnt-ship) | the offline/online gap and the evaluation ladder |
| [1](#level-1--your-first-model-experiment) | metrics, guardrails, sizing for tiny ML effects, triggered analysis |
| [2](#level-2--experiments-in-a-serving-stack) | assignment at the gateway, the logging contract, shadow mode, retraining |
| [3](#level-3--why-ml-experiments-lie) | clustering, feedback loops, position bias, delayed labels, monitoring |
| [4](#level-4--the-evaluation-stack) | OPE, variance reduction, bandits, LLM features, governance |
| [5](#level-5--interview-drills) | drills and model answers |

---

## Level 0 — Why your offline metric doesn't ship

### The gap

You improved AUC from 0.812 to 0.828 on the holdout set. The A/B test came back
flat. Nothing went wrong; this is the normal case, and there are only a few
reasons for it:

1. **The offline metric isn't the online metric.** AUC is a ranking property of
   scores; the product cares about clicks on the top three slots, and about
   what happens after the click.
2. **The offline data was collected by the old model.** Your training and
   evaluation sets contain only outcomes for items the *current* policy showed.
   The new model's favourite recommendation may have no logged outcome at all —
   this is the missing-support problem that [off-policy
   evaluation](#41-off-policy-evaluation-answering-before-you-have-traffic)
   formalises.
3. **The system around the model absorbs the change.** Business rules, dedup,
   diversity re-ranking, caps and fallbacks all sit downstream. A better score
   that never changes the final ordering changes nothing.
4. **Only a slice of traffic is affected.** If the new model returns a different
   answer on 8% of requests, 92% of your experiment is measuring nothing — see
   [triggered analysis](#14-triggered-analysis-the-biggest-single-win).
5. **The effect is real but small.** Which brings us to the fact that shapes ML
   experimentation more than any other: **ML effects are tiny**. A 1–3% relative
   improvement in a business metric is a good quarter's work, and detecting 1%
   relative on a 3% CTR needs **5.1 million users per arm**.

### The evaluation ladder

Nobody goes from notebook to 50/50 A/B. Each rung is cheaper and weaker than the
one above it, and its job is to kill bad candidates before they cost traffic:

| Rung | Cost | Answers | Fails to answer |
|---|---|---|---|
| Offline metrics (AUC, NDCG, RMSE) | minutes | is the model learning? | anything about users |
| Replay / counterfactual eval (IPS, DR) | hours | roughly what would this policy have scored? | anything outside logged support |
| Shadow mode | days, no user impact | latency, cost, disagreement rate, crash safety | user response — nobody sees the output |
| Interleaving (ranking only) | hours of traffic | which ranker do users prefer? | business metrics, long-term effects |
| A/B test | days–weeks of traffic | the causal effect on the real metric | very long-term effects, tiny effects |
| Long-term holdout | a quarter | what the whole programme was worth | anything fast |

The staff-level skill is picking the *cheapest rung that can kill the
candidate*, and knowing what each rung cannot see.

---

## Level 1 — Your first model experiment

### 1.1 What the arms are

The variant payload is a **model configuration**, not a UI change:

```ts
const rankerExperiment: Experiment<{ modelId: string; scoreThreshold: number }> = {
  key: 'feed-ranker-v4',
  status: 'RUNNING',
  traffic: 0.2,
  variants: [
    { key: 'control',   weight: 1, payload: { modelId: 'ranker-v3.2.1', scoreThreshold: 0.55 } },
    { key: 'candidate', weight: 1, payload: { modelId: 'ranker-v4.0.0', scoreThreshold: 0.55 } },
  ],
};
```

Pin the **exact artefact version**, not "latest". A variant that points at a
moving model is not a variant; it's a moving target, and the effect you measure
belongs to no particular model.

### 1.2 Metrics: three tiers, and the ML-specific ones

- **Primary**: the online business metric the model is supposed to move —
  click-through, completion, fraud caught per euro of friction, support
  deflection.
- **Guardrails** (this is where ML differs most): **p99 inference latency**,
  **cost per prediction**, **fallback/timeout rate**, **coverage** (fraction of
  requests the model actually scored), **calibration drift**, and **per-segment
  performance** for fairness. A model that wins on CTR and adds 40ms at p99 has
  not won; latency itself moves conversion.
- **Model-health diagnostics**: score distribution shift, feature null rates,
  disagreement rate with control, prediction entropy.

The classic ML failure here is optimising a proxy the model can game.
Engagement-maximising rankers find clickbait; fraud models with a
catch-rate-only objective decline everything borderline. Encode the thing you
refuse to trade away as a guardrail, or the optimiser will trade it away.

### 1.3 Sizing, when your effect is 1%

Same formula as the general guide, dramatically less forgiving inputs
(α=.05, power=.80, per arm):

| Surface | Baseline | MDE (relative) | Users per arm |
|---|---|---|---|
| Feed ranking | 3% CTR | +5% | 207,938 |
| Feed ranking | 3% CTR | +2% | 1,281,194 |
| Feed ranking | 3% CTR | +1% | 5,100,197 |
| Fraud model | 0.4% catch rate | +10% | 410,335 |

```ts
sampleSizePerVariant({ baselineRate: 0.03, minimumDetectableEffect: 0.01, effectType: 'relative' });
// 5_100_197
```

Read that table as a strategy document. It says: with typical product traffic you
can detect a *large* model improvement and nothing else, so most of your
engineering effort must go into **sensitivity** — triggering, variance
reduction, interleaving — rather than into running more tests.

### 1.4 Triggered analysis: the biggest single win

If the candidate returns a different result on only a fraction *f* of requests,
the users who saw identical output contribute exactly zero signal and pure
noise. The measured effect is diluted by *f*, and required sample scales as
**1/f²**:

| Requests where the models differ | Sample multiplier |
|---|---|
| 20% | 25× |
| 8% | 156× |
| 2% | 2,500× |

So the single most valuable thing you can log is **whether the arms would have
differed on this request** — a counterfactual flag. Then analyse only triggered
requests, and the test that needed 156× the traffic needs 1×.

Two rules that make it valid:

1. Trigger on a condition evaluated in **both** arms. Running both models (or
   logging the control's decision from the candidate's arm and vice versa) is
   the price of admission — trigger on "the candidate would have differed",
   computed identically on both sides.
2. Never trigger on anything post-treatment (e.g. "users who clicked"). That
   conditions on an outcome and destroys randomisation.

The framework's `{ exposed }` gate is exactly this mechanism: exposure — and
therefore inclusion in the analysis — is logged only when the unit could
actually be affected.

### 1.5 Randomisation unit: user or request?

| Unit | Good for | Breaks |
|---|---|---|
| **User** | Default. Any metric with memory: retention, session depth, repeat purchase | Needs identity; halves your effective n on request-heavy surfaces |
| **Request** | Latency, cost, infra changes, stateless scoring | Any per-user metric; the same user sees both models and personalisation state gets mixed |
| **Session** | Within-session effects when sessions are independent | Long-term metrics |
| **Cluster** (household, merchant, region) | Interference: shared accounts, marketplace supply | Much lower effective n |
| **Time slice** (switchback) | System-level ML: pricing, matching, inventory-aware ranking | Temporal confounding |

Request-level randomisation is tempting because it looks like more data. It is
the wrong unit for almost every model whose output the user can remember, and
its noise-reduction is partly an illusion: the observations are clustered by
user anyway (see [3.1](#31-your-metric-is-a-ratio-and-your-units-are-clustered)).

### 1.6 The rollout sequence

```
offline eval → shadow (0% user impact) → canary 1% → 5% → 50/50 experiment → ramp to 100% → holdout
```

Shadow mode deserves its own emphasis: run the candidate on live traffic,
discard its output, log everything. It costs no user risk and buys latency
profile under real load, cost per prediction, crash and timeout rates, feature
availability in production, and the **disagreement rate** that tells you the
trigger fraction *f* before you spend any traffic. Every model should have been
through shadow before an experiment is designed, because *f* determines whether
the experiment is feasible at all.

---

## Level 2 — Experiments in a serving stack

### 2.1 Where assignment happens

Assignment belongs at the **gateway or model router**, before feature fetch, and
it must be the same pure function the analysis job uses:

```ts
const assignment = assign({
  experiment: rankerExperiment,
  unitId: request.userId,                    // stable across requests and devices
  attributes: { country: request.country, appVersion: request.appVersion },
});
const config = assignment.payload ?? controlConfig;
```

The bucketing properties from the general guide matter more here, not less:
deterministic (a user who flips models between requests has an incoherent
personalised experience *and* pollutes both arms), salted per experiment, and
uniform. The one addition for ML: **the training pipeline must be able to
recompute the same assignment offline** from the logged unit id, so the analysis
never depends on a serving-side join that can silently lose rows.

### 2.2 The logging contract

This is the deliverable that determines whether any of the analysis in this
document is possible. Per scored request:

```jsonc
{
  "requestId": "...", "unitId": "user-123", "timestamp": 1700000000000,
  "experimentKey": "feed-ranker-v4", "variant": "candidate",
  "modelId": "ranker-v4.0.0",          // exact artefact, not "latest"
  "featureSnapshotId": "fs-2026-08-15-a",  // reproducibility of the input
  "prediction": 0.71,
  "action": "item-9931",
  "propensity": 0.42,                  // ← the field teams forget, see 4.1
  "wouldControlHaveDiffered": true,    // ← the trigger flag, see 1.4
  "latencyMs": 38, "fellBackToDefault": false
}
```

Two fields carry outsized value. **`propensity`** — the probability with which
the serving policy chose this action — is the difference between "we can
evaluate future candidates on these logs" and "we cannot", and it can only be
recorded at decision time. **`wouldControlHaveDiffered`** is what makes
triggered analysis possible. Neither can be backfilled.

### 2.3 Failure modes that live in serving

- **Timeouts and fallbacks.** The candidate is 15ms slower, times out slightly
  more often, and those requests fall back to a default. If the fallback isn't
  logged as part of the candidate arm, you've dropped the *slowest* users from
  one arm only — an SRM and a bias in the same event, and it flatters the
  candidate.
- **Caching.** A response computed under one arm and served from cache to a user
  in the other arm silently mixes the arms. Include the variant in the cache
  key, or exclude cached responses from both arms.
- **Training-serving skew.** Features computed one way in the training pipeline
  and another way in serving means the model you evaluate offline is not the
  model you serve. It shows up as "offline says +2%, online says −1%".
- **The candidate retrains mid-experiment.** Then the treatment is not one model
  but a sequence of them, and your estimate belongs to none. Either freeze the
  artefact for the experiment's duration, or explicitly declare the treatment to
  be the *pipeline* (retraining included), size for the extra variance, and
  never attribute the result to a specific model version.
- **Feature store drift** between arms — a feature backfill that lands mid-test
  changes the control's behaviour too, which is not a comparison of anything.

### 2.4 QA and reviewability

The same override plumbing applies (`?ab.feed-ranker-v4=candidate`), and the
same rule: a forced assignment is **never enrolled**, so the engineer eyeballing
the candidate's recommendations all afternoon cannot contaminate the readout.
For models, add a debug endpoint that returns both arms' outputs side by side
for a given input — most model bugs are found by looking at 50 disagreeing
examples, not at aggregate metrics.

---

## Level 3 — Why ML experiments lie

### 3.1 Your metric is a ratio, and your units are clustered

CTR is clicks/impressions. You randomise users; you count requests. One user's
500 impressions are not 500 independent observations, so the naive per-request
test uses a standard error that is far too small.

Measured on simulated traffic with realistic skew (10% of users make ~50
requests) *and* heterogeneous per-user click propensity, the correctly clustered
standard error is **1.95× larger** than the naive one — the naive test reports a
z-score roughly twice what the data supports, and manufactures significance out
of nothing:

```ts
compareRatioMetric(controlUsers, treatmentUsers);
// { absoluteLift, standardError, clusteringInflation: 1.95, significant: false }
// …where the naive per-request z-test on the same data says "significant".
```

The delta method at the user level is the fix
([`ratioMetrics.ts`](../src/features/experiments/analysis/ratioMetrics.ts)):

```
Var(R) ≈ ( Var(nᵢ) + R²·Var(dᵢ) − 2R·Cov(nᵢ, dᵢ) ) / (m · d̄²)
```

Note *why* volume skew alone isn't the problem: with one shared click rate,
requests really are independent draws and the naive SE is right. It's
**heterogeneity** — users differing in their personal rates — that creates the
correlation. Personalised systems maximise exactly that heterogeneity, which is
why this bug is endemic to ML experiments specifically.

### 3.2 Everyone is watching the dashboard

Model rollouts get monitored continuously — that is a feature, not a discipline
problem. But a fixed-horizon test re-read at every look is not a 5% test.
Measured over 600 simulated null experiments with 10 looks each:

| Rule | False-positive rate |
|---|---|
| Fixed-horizon z-test, re-read at every look | **19.0%** |
| Always-valid confidence sequence | **0.2%** |

```ts
alwaysValidRateDifference(
  { units: 10_000, conversions: 1_000 },
  { units: 10_000, conversions: 1_050 },
  { tuningSample: 10_000 },
);
// { confidenceSequence, decisive, inflationVsFixedHorizon: 1.55 }
```

The interval is 1.55× wider at its tuning point (≈2.4× the sample for equal
power). Pay it when the alternative is a dashboard that lies one time in five —
which, for a model rollout with a rollback decision attached, it is.

### 3.3 Feedback loops: the model eats its own tail

Unique to ML, and the most subtle interference there is. The candidate ranker
shows different items → users interact with different items → those
interactions become training data → the next model is trained on data shaped by
the experiment. Consequences:

- **Within the experiment**: if both arms retrain from a shared stream, the
  treatment's data changes the control's model. The arms are no longer
  independent, and the measured difference understates the true one.
- **After the experiment**: a model that was neutral in a 5% test can behave
  differently at 100%, because at 100% it is training on its own output. This is
  why a two-week win sometimes decays over a quarter.
- **Popularity spirals**: recommending popular items generates popularity,
  which the next model reads as quality.

Mitigations, in increasing order of cost: freeze training data for the
experiment window; train each arm on its own arm's data only (expensive,
halves data); hold out a permanently untreated slice to detect drift; use
cluster or switchback randomisation when the loop runs through shared state.
None of these are free, and the first step is simply *knowing that your metric
includes a feedback term*.

### 3.4 Position bias, and the interleaving escape hatch

Users click the top slot regardless of relevance, so click metrics measure
position at least as much as quality. For ranking changes, **team-draft
interleaving** sidesteps both position bias and between-user variance by showing
one blended list to every user and asking whose contributions got clicked
([`interleaving.ts`](../src/features/experiments/analysis/interleaving.ts)):

```ts
const blended = teamDraftInterleave(productionRanking, candidateRanking, coinFlip, 10);
const outcome = scoreQuery(blended, clickedIds);       // 'A' | 'B' | 'TIE'
interleavingPreference(outcomes);
// 120 of 200 decisive queries prefer B → p = 0.0057
```

**200 decisive queries** settle a 60/40 preference. The equivalent A/B test on
3% CTR at +5% relative needs 207,938 users per arm. That is the one-to-two
orders of magnitude sensitivity gain the literature reports, and it's why
search and recommendation teams gate every ranking change on interleaving
first.

What it cannot do: measure revenue, session length, or retention; evaluate
anything you can't blend (a fraud decision, a credit limit); or detect that both
rankers are bad. The winner still needs an A/B test for the business metric —
interleaving is the filter, not the verdict.

### 3.5 Labels that arrive late

Fraud chargebacks land 30–90 days out. Loan defaults take a year. Refunds,
cancellations and complaints all lag. Naively computing "fraud caught" on
day 7 of an experiment measures the *mature* labels of early users against the
immature labels of recent ones, and the arms accumulate users at different
points in that window.

Fixes: fix a maturity window and only count units with a full window elapsed
(costs calendar time); model the label-delay distribution and correct;
or use a fast proxy for the readout and confirm on mature labels before the
final decision. Whichever you pick, decide it **before** the readout, because
"which day do we cut the labels" is a devastatingly effective p-hacking knob.

### 3.6 The rest of the trust checklist

- **SRM first, always.** In ML systems the usual culprit is asymmetric
  infrastructure: the heavier model times out more, gets throttled more, or
  fails on a specific device class.
- **Heavy tails.** Watch time, transaction value and session depth are heavily
  skewed; one whale moves the mean. Winsorise at the 99th percentile or use a
  bounded metric, and pre-register the choice.
- **Fairness slices.** Run the primary metric per protected/vulnerable segment
  with a multiplicity correction. An average win that hides a segment loss is a
  compliance problem in a bank, not a rounding error.
- **Twyman's law, ML edition.** A model change that moves a business metric by
  10% is a data leak, a logging bug, or a broken join — until you have
  reproduced it. The strongest "wins" of my career were mostly bugs.

---

## Level 4 — The evaluation stack

### 4.1 Off-policy evaluation: answering before you have traffic

Every logged decision carries the probability with which it was taken. Re-weight
the logged rewards and you can estimate what a *different* policy would have
scored — offline, on data you already have
([`offPolicy.ts`](../src/features/experiments/analysis/offPolicy.ts)):

```
V_IPS  = (1/n) Σ wᵢ·rᵢ                     wᵢ = π_e(aᵢ|xᵢ) / π_b(aᵢ|xᵢ)
V_SNIPS = Σ wᵢ·rᵢ / Σ wᵢ
V_DR   = (1/n) Σ [ q̂(xᵢ, π_e) + wᵢ·(rᵢ − q̂(xᵢ, aᵢ)) ]
ESS    = (Σwᵢ)² / Σwᵢ²
```

Measured on 40,000 simulated logged decisions where the candidate policy's true
value is **0.200**:

| Logging policy overlap | IPS | SNIPS | Doubly robust | ESS | max weight |
|---|---|---|---|---|---|
| Explores well (30% on the candidate's action) | 0.198 ± 0.004 | 0.198 ± 0.004 | 0.198 ± 0.004 | 30% | 3.3 |
| Barely explores (2%) | 0.184 ± 0.015 | 0.191 ± 0.016 | 0.191 ± 0.014 | **1.9%** | 50.0 |

Read the second row carefully: all three estimators still produce a number with
a plausible-looking interval, and all three are resting on ~2% of the data. The
**ESS is the go/no-go**, not the confidence interval:

```ts
supportDiagnostics(log);   // { effectiveSampleSize, ratio: 0.019, sufficient: false }
```

Practical rules:

- **Log propensities at decision time or forfeit this entire tier.** A
  deterministic argmax policy has propensity 1 on one action and 0 elsewhere —
  every estimator above is undefined. Buying evaluability means spending a small
  slice of traffic on exploration (ε-greedy, softmax sampling). That is a
  serving design decision, made months before anyone asks for OPE.
- **Prefer doubly robust** when you have a reward model — the residuals it
  weights are small, so exploding weights multiply near-zero.
- **Cross-fit the reward model**; fitting it on the rows you evaluate reintroduces
  the bias you were removing.
- **Clip and report both.** If clipped and unclipped estimates disagree wildly,
  the honest answer is "the logs cannot answer this".
- OPE **ranks candidates**; it does not license a launch. Its job is to take 20
  candidates down to 2.

### 4.2 Variance reduction: buy sensitivity, not traffic

Since sample scales with 1/MDE², sensitivity work compounds harder than anything
else on the roadmap. In rough order of payoff for an ML team:

1. **Triggered analysis** (§1.4) — up to 100×+ when the trigger rate is low.
2. **Interleaving** for ranking (§3.4) — 10–100× on the preference question.
3. **MLRATE / CUPED** — regression-adjust the metric with a model prediction
   built from pre-treatment features
   ([`cuped.ts`](../src/features/experiments/analysis/cuped.ts)):

   ```ts
   mlrateCompare(
     { metric: controlOutcomes, prediction: controlPreExperimentPredictions },
     { metric: treatmentOutcomes, prediction: treatmentPreExperimentPredictions },
   );
   // { varianceReduction: 0.81, difference, confidenceInterval, pValue }
   ```

   Variance drops to (1−ρ²): ρ=0.5 saves 25% of traffic, ρ=0.7 saves 49%,
   ρ=0.9 saves 81%. This is the rare case where being an ML team is a *statistical*
   advantage — you already have a model that predicts the outcome. Two
   non-negotiables: strictly pre-treatment features, and cross-fitted
   predictions.
4. **Stratification** on pre-treatment segments, **outlier capping**, and
   picking a less noisy primary metric.

### 4.3 Bandits, and when to stop A/B testing

| | A/B test | Bandit |
|---|---|---|
| Goal | estimate the effect | maximise reward while learning |
| Traffic on losing arms | full, for the whole horizon | shrinks over time |
| Output | an unbiased effect size with a CI | a policy, and a much messier estimate |
| Good for | decisions you'll live with for years | choices that refresh constantly: creatives, headlines, ranking candidates |
| Bad for | fast-refreshing content | anything you must *report* on, or where non-stationarity fools the exploration |

Contextual bandits are the natural end state of a mature recommendation system,
but two warnings: they make clean inference much harder (the assignment
probabilities move, so the analysis must handle time-varying propensities), and
they interact violently with concurrent A/B tests — a bandit that reallocates
traffic in response to a metric another experiment is moving will chase that
experiment's effect. Give bandits their own layer.

### 4.4 System-level effects

When the model's decisions affect shared state, per-user randomisation measures
the wrong thing:

- **Marketplaces / inventory**: better ranking for treated users consumes
  inventory that control users would have taken. Pure cannibalisation, reported
  as a win.
- **Pricing and matching**: a treated request changes prices or supply for
  everyone.
- **Capacity**: a heavier model degrades latency for both arms.

Tools: **switchback** designs (flip the whole system between policies over time
windows — the standard for pricing and dispatch), **cluster randomisation** by
region or merchant, and budget-split designs. All cost sensitivity; use them
only when interference is real and material, and say so explicitly when you do.

### 4.5 Evaluating LLM features

The newest surface, and the one where teams most often skip straight to vibes.
What actually works:

- **The variant is the whole configuration**: model id, prompt version,
  retrieval settings, temperature, tool set. Pin and log every part — a prompt
  edit mid-experiment is a new treatment.
- **Guardrails are cost and latency first.** Cost per request and p95 time to
  first token are the two metrics that decide whether a win is shippable.
- **Offline: LLM-as-judge, calibrated to humans.** Judges are usable only once
  you've measured their agreement with human raters on a sample, and re-measured
  when the judge model changes. Report the agreement rate alongside the score.
- **Online: pairwise preference and behavioural proxies.** Explicit thumbs are
  sparse and biased toward the annoyed; prefer behaviour — task completion,
  follow-up rate, escalation to a human, retry rate.
- **Safety and hallucination guardrails** as hard blockers, with an escalation
  path rather than an average.
- **Non-determinism**: at temperature > 0 the same user can get different
  outputs within an arm, which inflates variance — usually justifying more
  traffic, or a fixed seed for the experiment.
- **Human evaluation is a sample size problem too.** 50 rated examples cannot
  detect a 5-point preference difference; size it like any other experiment.

For a bank specifically: an assistant that discusses balances, fees or products
is subject to disclosure and advice rules. The guardrail set has to include
"did it give financial advice it isn't allowed to give", and that check cannot
be an average — it's a per-instance blocker.

### 4.6 Governance: models in a regulated shop

"Champion/challenger" is the banking term for what this document calls
control/treatment, and it comes with obligations most product experiments don't
have: documented model risk management (validation, monitoring, an inventory of
deployed models), fairness/adverse-impact testing per protected group,
explainability for adverse decisions, and human review paths for credit and
fraud decisions. Two practical consequences:

1. **Some models can't be A/B tested on live decisions at all** — you shadow
   them, validate offline against mature outcomes, and roll out with human
   review, because the cost of the treatment arm being wrong is borne by a
   customer who was denied something.
2. **Retention and reproducibility are legal, not engineering, requirements.**
   "Which model version produced this decision, on which features, under which
   experiment" has to be answerable years later.

### 4.7 When not to A/B a model

- The effect is smaller than your traffic can ever resolve → ship on judgement
  with monitoring, or invest in sensitivity instead.
- It's a pure infrastructure change with identical outputs → verify equivalence
  offline (and A/B the *latency*, request-randomised).
- The decision is safety- or compliance-critical → shadow and validate.
- You wouldn't act on either outcome → skip it and spend the traffic elsewhere.

---

## Level 5 — Interview drills

**"AUC went up 2 points, the A/B was flat. What happened?"**
Most likely nothing broke. I'd check, in order: does the score change actually
change the final ordering after business rules and re-ranking (disagreement rate
from shadow mode); what fraction of requests are triggered, since dilution at
f=0.08 costs 156× the sample; is the online metric even downstream of ranking
quality; and was the offline evaluation done on data collected by the old policy,
which systematically lacks outcomes for the new model's picks. Then I'd check the
test was powered at all — 1% relative on 3% CTR needs ~5M users per arm.

**"How would you evaluate a ranking change with modest traffic?"**
Interleaving. Blend the two rankings per query with team draft, credit clicks
per team, sign test over queries — 200 decisive queries settle a 60/40
preference, versus ~200k users per arm for a 5% relative CTR A/B. Then A/B the
winner for the business metric, because interleaving can't see revenue or
retention.

**"We can't run an A/B. Estimate the effect from logs."**
Off-policy evaluation, if and only if the logs carry propensities and the
logging policy explored where the candidate wants to act. I'd start with the
effective sample size, not the estimate: at 2% ESS the interval looks fine and
the number rests on 2% of the data. With a reward model I'd use doubly robust,
cross-fitted, and report clipped and unclipped estimates. The output ranks
candidates; it doesn't authorise a launch.

**"Your model retrains nightly. Can it run in an experiment?"**
Not as a single treatment. Either freeze the artefact for the experiment window
and attribute the result to that version, or declare the treatment to be the
pipeline including retraining — in which case the estimate belongs to the
pipeline, the variance is higher, and I'd watch for the feedback loop where the
candidate's own data changes what it becomes.

**"CTR is up 3% with p=0.001 on 2 million impressions. Ship?"**
Not yet. Impressions aren't the randomisation unit — with users clustered and
heterogeneous, the naive per-request SE is understated by roughly 2×, so I'd
recompute with the delta method at the user level. Then SRM (a slower candidate
that times out asymmetrically is the usual cause), then guardrails: p99 latency,
cost per prediction, fallback rate, and per-segment effects.

**"Design experimentation for a recommender at scale."**
Shared deterministic assignment library used by serving and analysis; logging
contract with propensity and trigger flags; the ladder — offline, OPE, shadow,
interleaving, A/B, long-term holdout; triggered analysis and MLRATE by default
in the analysis engine; always-valid inference so dashboards can be watched;
layers so ranking, ads and UI experiments don't collide; SRM and guardrail
alerting; a registry of results. Then the hard parts: feedback loops through
retraining, marketplace interference, and a quarterly holdout to check the sum
of the wins is real.

---

## Cheat sheet

```
Dilution:        required sample ×  1/f²      (f = fraction of requests affected)
Clustered ratio: Var(R) ≈ (Var(nᵢ) + R²Var(dᵢ) − 2R·Cov(nᵢ,dᵢ)) / (m·d̄²)
IPS / SNIPS:     Σwᵢrᵢ/n   |   Σwᵢrᵢ/Σwᵢ        wᵢ = π_e/π_b
Doubly robust:   (1/n)Σ[ q̂(xᵢ,π_e) + wᵢ(rᵢ − q̂(xᵢ,aᵢ)) ]
Support check:   ESS = (Σw)²/Σw²  → stop if ESS/n < ~0.1
MLRATE/CUPED:    Y' = Y − θ(ŷ − ȳ̂)  → variance × (1 − ρ²)
Always-valid:    ±se·√( 2(nρ+1)/(nρ) · ln(√(nρ+1)/α) )   ≈1.55× fixed-horizon width
Interleaving:    sign test over decisive queries; ties excluded
```

**Before the experiment**

- [ ] Model artefact pinned; retraining frozen or declared part of the treatment
- [ ] Shadow run done: latency, cost, fallback rate, disagreement rate *f*
- [ ] Sized with *f* accounted for; infeasible tests killed here, not later
- [ ] Propensity and trigger flag logged at decision time
- [ ] Guardrails wired: p99 latency, cost/prediction, fallback, coverage, fairness slices
- [ ] Randomisation unit chosen (user unless the metric is stateless)
- [ ] Cache keys include the variant; fallbacks attributed to the right arm

**Before the decision**

- [ ] SRM clean
- [ ] Ratio metrics computed at the unit of randomisation
- [ ] Triggered population analysed, not all traffic
- [ ] Label maturity window respected and pre-registered
- [ ] Guardrails checked with multiplicity correction; fairness slices reviewed
- [ ] Effect stable over days (no novelty decay, no feedback-loop drift)
- [ ] Result, model version and decision written to the registry

## Further reading

- Kohavi, Tang & Xu — *Trustworthy Online Controlled Experiments*
- Deng, Xu, Kohavi & Walker — *Improving the Sensitivity of Online Controlled
  Experiments by Utilizing Pre-Experiment Data* (CUPED)
- Guo, Coey, Konutgan, Li, Schoener & Goldman — *Machine Learning for Variance
  Reduction in Online Experiments* (MLRATE)
- Chapelle, Joachims et al. — *Large-Scale Validation and Analysis of Interleaved
  Search Evaluation*
- Dudík, Langford & Li — *Doubly Robust Policy Evaluation and Learning*
- Howard, Ramdas, McAuliffe & Sekhon — *Time-uniform confidence sequences*
- Bottou et al. — *Counterfactual Reasoning and Learning Systems*
