# 12 — Observability & Experimentation

## 12.1 Metric hierarchy

Four layers; an incident is diagnosed by walking *down* it, and a model launch is judged by walking
*up* it.

| Layer | Examples | Audience |
|---|---|---|
| **Business** | Revenue, RPM, fill rate, advertiser ROAS, budget delivery % | Exec, advertisers |
| **Model** | pCTR AUC, calibration ratio, retrieval recall@500, coverage, drift (PSI) | ML team |
| **Service** | p50/p99/p99.9 per stage, error rate, degradation mix, cache hit rates, QPS | Serving on-call |
| **Infra** | CPU, memory, GC pause, Kafka lag, Aerospike latency, network | Platform |

### The dashboards that actually get used

1. **Serving health:** p99 by stage (stacked), degradation-level mix over time, error rate,
   QPS by region. One screen answers "is it fast and up?"
2. **Money:** spend velocity vs. pace per campaign, fill rate, no-fill reasons breakdown, ledger vs.
   counter reconciliation delta.
3. **Model health:** calibration ratio per placement, score distribution vs. yesterday, feature
   null rates, drift scores, shadow-vs-live agreement.
4. **Funnel:** eligible → retrieved → ranked → won → shown → viewed → clicked, with drop-off at
   each edge. This is the screen where "revenue is down 4%" becomes "retrieval recall dropped
   after the index rebuild".

## 12.2 Instrumentation

- **Metrics:** Prometheus, histograms not averages, labels bounded (`placement_type`, `region`,
  `model_version`, `degradation_level` — **never** `campaign_id` or `user_id`; unbounded
  cardinality kills the TSDB and is the most common self-inflicted observability outage).
  Per-campaign metrics come from the analytics store (ClickHouse), not Prometheus — different tool,
  different job.
- **Tracing:** OpenTelemetry, **1% head sampling plus 100% tail sampling of slow (>60 ms) and
  errored requests**. Tail sampling is what makes tracing useful for a p99 problem: sampling 1%
  uniformly gives you almost no slow traces.
- **Logging:** structured, leveled; the *request log* to Kafka is a separate, schema'd data path
  (not stdout logging). Operational logs are sampled; error logs never are.
- **Profiling:** continuous CPU/heap profiling (pprof/Pyroscope) in production at low overhead —
  it's the only way to attribute a p99 regression to a code change after the fact.

### Latency accounting

Every stage records into a per-request breakdown that ships with the log row:

```
{ total_us: 31200, feature_us: 4100, eligibility_us: 1800, retrieval_us: 3900,
  assemble_us: 3600, score_us: 9800, auction_us: 2600, overhead_us: 5400 }
```

`overhead_us` (total minus the sum of parts) is a deliberately tracked metric. When it grows, the
cause is GC, scheduling, or lock contention — none of which show up in any stage timer. Most teams
don't measure it and then can't explain their tail.

## 12.3 Alerting

Alert on **symptoms**, page on **user impact**, ticket on **causes**:

| Alert | Threshold | Action |
|---|---|---|
| p99 latency | > 45 ms for 5 min | Page |
| Error rate | > 0.5% for 2 min | Page |
| Revenue drop | > 10% vs. same hour last week (seasonality-adjusted) | Page |
| Budget overspend | any campaign > 1% over | Page |
| Degradation mix | non-FULL > 5% for 10 min | Page |
| Calibration ratio | outside [0.9, 1.1] for 30 min | Ticket (business hours) |
| Feature freshness | > 5 min stale | Ticket |
| Kafka lag | > 5 min of data | Ticket |
| Drift (PSI) on a top feature | > 0.2 | Ticket |

Burn-rate alerting on the SLO error budget (fast burn: 2% of monthly budget in 1 h → page; slow
burn: 10% in 6 h → ticket) rather than raw thresholds alone, so a brief blip doesn't page and a
slow bleed doesn't hide.

## 12.4 Experimentation

### Assignment

```
bucket = hash(user_id ⊕ experiment_salt) mod 10000
```

Deterministic, stateless, identical in both regions (see
[11](./11-reliability-active-active.md#112-state-classification)). Properties:

- **User-level, not request-level**, for anything with a memory effect (frequency, fatigue,
  learning). Request-level randomization would leak treatment across a user's session and bias the
  result toward zero.
- **Mutually exclusive layers** (Google's overlapping-experiments model): auction experiments,
  ranker experiments and UI experiments live in different layers and can run concurrently; two
  experiments in the same layer never touch the same user.
- **Holdback:** a permanent 1% global holdback on the previous model generation, which is the only
  way to measure the *cumulative* effect of a year of 0.5% wins (they rarely add up to the sum).

### Analysis

- Primary metric fixed **before** launch, with a minimum detectable effect and the resulting sample
  size/duration computed up front. Peeking at a running test without sequential-testing correction
  is the most common way ad teams ship noise.
- **CUPED** variance reduction using pre-experiment metrics — routinely cuts required runtime by
  30–50%, which matters when you want to run many experiments.
- **Guardrails always evaluated**, not just the primary: latency, fill rate, advertiser
  concentration, user-side engagement. A revenue win that degrades user experience is a loan, not a
  win.
- Minimum runtime of 7 days regardless of significance, to capture the weekly cycle.
- **Marketplace interference:** ad auctions violate SUTVA — the treatment arm's bids affect the
  control arm's prices through shared budgets. For auction/pacing changes, use **budget-split or
  time-split (switchback) designs** rather than user-split, and say so explicitly. This is the
  subtlety that separates people who have run ad experiments from people who have read about them.

### Offline policy evaluation

Before an experiment costs live traffic, estimate its effect from logged exploration data:

```
IPS:  V̂(π) = (1/n) Σ [ π(a|x) / π₀(a|x) ] · r
DR:   IPS residual + a learned reward model, lower variance
```

Requires logging the **propensity** `π₀(a|x)` from the exploration slot — which is why exploration
is randomized *and logged with its probability*, not just randomized. Estimates are used to
**rank candidate policies and reject bad ones**, never as a substitute for the A/B test.

## 12.5 Drift & model monitoring

| Signal | Method | Response |
|---|---|---|
| Feature drift | PSI / KL vs. training distribution, per feature, daily | >0.2 → investigate; often an upstream pipeline change, not real drift |
| Prediction drift | Score histogram vs. previous week | Sudden shift → check feature nulls first, then the data |
| **Calibration drift** | Σpred/Σactual per placement, hourly | The earliest reliable signal of a real problem; alert at 10% |
| Concept drift | Rolling AUC on the last 24 h of labels | Sustained decline → retrain cadence too slow |
| Skew | Daily offline-vs-logged feature diff | Any diff > 0.1% → treat as a bug, not a tolerance |

**Calibration drift leads every other model metric.** AUC is remarkably stable while a model
becomes badly mis-priced, because ordering survives distribution shift that ruins probabilities —
and in an auction, the probability *is* the price.

---
Next: [13 — Privacy & compliance](./13-privacy-and-compliance.md)
