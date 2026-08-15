# Design a Recommendation System — Staff-Level Walkthrough

Netflix / YouTube / Spotify class problem. Two-stage retrieval + ranking.
Structured as the nine moves of the interview, in the order you should speak them.

The one-line thesis to open with:

> "The catalog is too big to score, so I'll build a funnel: cheap recall first, expensive precision second,
> and a policy layer last. The hard parts aren't the models — they're the label definition, the feedback
> loop the system creates in its own training data, and the latency budget that decides how much model I can afford."

---

## 00 · Assumption set (state these, get them corrected, then do arithmetic on them)

Interviewers grade the arithmetic, not the guess. Fix numbers early so every later claim is checkable.

| Quantity | Assumption | What it decides |
|---|---|---|
| MAU / DAU | 200M / 70M | Sharding, A/B power |
| Feed requests | ~25 per DAU/day → 1.75B/day | 20k QPS avg, **60k QPS peak** |
| Catalog | 800M items, ~80M eligible after regional/policy filters | Retrieval is mandatory |
| New items | ~50k/hour | Index must be incremental, not nightly-only |
| Logged impressions | ~30B/day | Training data volume, storage cost |
| Latency SLA | p99 **300ms** server-side for the feed | Model capacity budget |
| Base CTR / valid-watch rate | ~4% impressions → click, ~55% clicks → valid watch | Class imbalance, sampling |
| Existing infra | Feature store (online KV + offline warehouse), streaming bus, GPU inference, experimentation platform | Whether I build or borrow |

**Also ask:** surface (home feed vs. "more like this" vs. autoplay-next — they have different objectives), two-sided marketplace or licensed catalog, regulatory constraints (GDPR deletion propagating into embeddings, EU DSA requirement to offer a non-profiling feed, minors' accounts), and whether there is already a random-traffic holdout. That last one matters more than it sounds — see §06.

---

## 01 · Clarify & scope: business objective → ML metric

**Business objective.** For a subscription product (Netflix, Spotify Premium) the objective is *retention* — the probability the member renews next month. For an ad-supported product (YouTube) it is *long-term satisfied engagement*, which monetizes through ad load, but where the long-term qualifier is doing the real work.

**The translation problem.** Retention is not a trainable label. It is delayed by weeks, extremely low-variance per user (most users renew), confounded by everything outside the product, and impossible to credit-assign to an individual impression. So I do **not** train on it. I decompose:

```
value(u, i, ctx) = Σ_k  w_k · P(event_k | u, i, ctx)
```

where the events are things I *can* observe per impression — click, valid watch, completion, like, subscribe/follow, share — minus penalty terms for observed dissatisfaction — skip-in-5s, hide, "not interested", report.

The `w_k` weights are the **value model**. They are not learned from the impression data (nothing in that data says how much a "share" is worth); they are set by product judgment and then *tuned by A/B against the true north-star metric*. That split — surrogate objective trained offline, weights validated online against retention — is the honest answer to "how do you optimize retention with a model that can't see retention."

**Say out loud:** the north-star metric (retention) is the *arbiter of the weights*, not the training target. If I get this backwards I end up optimizing a metric I can measure and shipping regressions in the one I care about.

---

## 02 · Frame as ML: label, granularity, loss

Two models, two different framings. This is the core of the answer.

### Retrieval (candidate generation)

- **Granularity of an example:** one *positive interaction* — (user state at time t, item they engaged with). Not one impression.
- **Task:** given the user's state, produce a distribution over the entire catalog; learn `P(next positive item | user context)`.
- **Loss:** sampled softmax over the catalog with **logQ correction**. In-batch negatives are sampled proportional to item popularity, which systematically penalizes popular items; correcting the logit by the log sampling probability fixes it:

  ```
  s'(u, i) = s(u, i) − log Q(i | u)
  L = −log softmax over batch of s'(u, ·)
  ```

  Without this, retrieval quietly over-suppresses head content and the offline/online gap is unexplainable. It's a two-line change and a classic staff-level tell.
- **Constraint that defines the architecture:** the item side must be computable *without the user*, because item embeddings are precomputed and indexed. That forbids user×item cross features, and that limitation is precisely why a second stage exists.

### Ranking

- **Granularity of an example:** one **impression** — `(request_id, user, item, position, surface, timestamp, serving_model_version)`. Logged at serve time, with the features that were actually used.
- **Labels:** a vector, not a scalar. `{clicked, valid_watch (≥30s or ≥50% duration), completed, watch_seconds, liked, subscribed, hidden, reported}`.
- **Loss:** multi-task. Binary heads with weighted cross-entropy; watch time either as a regression on log-seconds or — better — as **bucketed cross-entropy over duration bins**, which is robust to the heavy tail and gives you a distribution to integrate rather than a conditional mean dominated by outliers. Weighted-logistic (weighting positives by watch time) is the YouTube trick that lets one sigmoid head express expected watch time; it's fine, but multi-head + explicit value model is more controllable.
- **Negatives:** impressed-not-engaged. These are *hard* negatives and are exactly the right ones for ranking — the ranker's job is to separate items the retrieval stage already thinks are plausible.

**Say out loud:** retrieval and ranking are trained on *different data distributions on purpose*. Ranking sees only what was shown (hard, biased, in-distribution). Retrieval must see the whole catalog (random negatives) or it never learns that 99.99% of the catalog is irrelevant. Training retrieval only on impressed data is a common and fatal mistake — it inherits the previous ranker's blind spots and can never recover the items the old system never showed.

---

## 03 · Metrics

### Offline

| Stage | Metric | Why |
|---|---|---|
| Retrieval | **Recall@k** (k = 500 / 1000 / 4000) against next-day positives | Retrieval's only job is not to lose the good item |
| Retrieval | Catalog coverage@k, fresh-item recall (items < 24h old) | Detects head-collapse and cold-start failure |
| Ranking | **User-grouped AUC** (AUC within each request, averaged) | Global AUC is dominated by between-user differences and can improve while per-request ordering degrades |
| Ranking | **NDCG@10** with graded gains (valid watch > click > impression) | Position-weighted ordering quality |
| Ranking | **Log-loss + ECE / calibration ratio (Σp / Σy)** per slice | Scores are combined with weights and business multipliers; a miscalibrated head silently reweights the value model |
| Both | Metrics sliced by: new users, new items, market, device, head/tail popularity decile | Aggregate metrics hide the regressions that generate complaints |

Calibration deserves emphasis: the moment scores from several heads are linearly combined, *relative* ordering quality is not enough — the heads must be on a common probability scale, or the value-model weights mean nothing.

### Online

- **Primary:** a retention proxy that moves within an experiment window — weekly active days, or DAU/MAU stickiness. Not raw watch time.
- **Secondary:** valid watches per DAU, session count, session depth, CTR.
- **Guardrails (the ones that stop you shipping a disaster):**
  - dissatisfaction: hide rate, "not interested" rate, report rate, unsubscribe/unfollow rate
  - **sessions vs. dwell-per-session moving in opposite directions** — the signature of a rabbit-hole regression: people watch longer per visit but come back less
  - diversity/ecosystem: Gini of impressions across items and creators, % of DAU seeing ≥1 new-to-them creator, share of impressions to items <7 days old
  - systems: p99 latency, error rate, fallback-serving rate
  - fairness/supply: impression share for small creators, catalog coverage

**The trap to name before they ask it:** optimizing CTR alone produces clickbait; optimizing watch time alone produces long, autoplay-y, low-quality content and rabbit holes. The industry answer is a *satisfaction* signal — an in-product survey ("was this worth your time?") on a small random sample, used to train a satisfaction head that is then distilled to full traffic, plus explicit negative feedback as a penalty term.

---

## 04 · Data

**Sources.** Impression/serving logs, playback events, explicit feedback, catalog metadata, content-derived embeddings (text, audio, video frames), user profile, subscription/follow graph, search queries.

**The non-negotiable logging requirement.** Log, at serve time, for every request: the full candidate list, positions, per-item scores from each stage, model versions, the **feature values actually used**, and the **propensity** if the slot came from exploration. None of this can be reconstructed later. A recsys with bad serving logs cannot be improved, only replaced.

**Labeling strategy (implicit feedback).** Definitions are product decisions with big consequences:

- Video: "valid watch" = ≥30s or ≥50% of duration, whichever is shorter — protects short-form content from a fixed threshold.
- Music: a stream counted at ≥30s, and **skip within 30s treated as an explicit negative** — the skip signal is the highest-value label in music recsys.
- Netflix-style: title start + ≥70% completion; also treat "started and abandoned in 5 minutes" as a strong negative.

**Sampling.** Keep all positives; downsample negatives (e.g. keep 1 in 10) for the ranker to control cost. Then **recalibrate**: with negative keep-rate `r`, true odds = sampled odds × `r`, so

```
p_true = p_s · r / (1 − p_s + p_s · r)
```

Skipping this step is the most common source of "the model is great offline and the ad/value system behaves insanely in production."

**Leakage.**
- Split by **time**, never randomly. Train on `t < T`, evaluate on `t ∈ [T, T+1d]`. Random splits let the model see the future of the same session and inflate offline metrics by amounts that never materialize online.
- All aggregate features must be **point-in-time**: "user's watch count in this genre" computed as of the impression, not over the full window.
- Watch for label-adjacent features (e.g., a "recently played" counter updated before the log row is written).

**Imbalance.** ~4% CTR; positives for rare events (subscribe, share) are <0.1%. Use per-head loss weights, and evaluate rare heads with PR-AUC, not ROC-AUC.

**Delayed feedback.** Click is instant; valid watch arrives seconds-to-minutes later; subscribe/return-visit arrive hours-to-days later. For hourly incremental training this creates *fake negatives* — rows labeled 0 that will become 1. Options: (a) an attribution window (wait 4h, accept staleness), (b) a **delayed-feedback model** — jointly model `P(convert eventually)` and `P(delay ≤ t)`, and importance-weight the not-yet-converted rows, (c) train fast heads online and slow heads on the daily batch. Naming this problem is a strong differentiator; most candidates never mention it.

**Presentation bias.** Position, ranking-slot size, thumbnail treatment and the fact that the item was *shown at all* all confound the label. Two mitigations, used together:
- **Shallow position tower:** feed position/device into a small separate tower whose output is added to the logit; at serving, set position to a fixed constant. The main network is then forced to learn position-independent relevance.
- **IPS weighting** on logged propensities for the exploration-served fraction.

**Privacy/compliance.** Retention windows on raw event logs; deletion requests must propagate into training sets *and* into learned user embeddings (practically: user vectors are derived at request time from recent events rather than stored as long-lived learned parameters — which also makes deletion tractable); separate handling for minors; a non-personalized feed option.

---

## 05 · Features

| Family | Examples | Notes |
|---|---|---|
| **User — long-term** | genre/creator affinity vectors, historical valid-watch rate, tenure, price tier, language | Shrink toward segment priors for low-activity users |
| **User — short-term** | last N (50–200) interacted item IDs + dwell + timestamps, current session events | The single biggest source of lift; also the mechanism that makes cold-start users work |
| **Item** | content embeddings (text/audio/frame), duration, language, category, age since publish, popularity priors | Popularity features are the main feedback-loop vector — see §06 |
| **Context** | device, hour-of-day/day-of-week, network quality, surface, slot index, session depth, autoplay vs. explicit | Time-of-day matters enormously for music, moderately for video |
| **Cross (ranking only)** | retrieval dot-product score, user's count of plays in this item's cluster, co-visitation counts, days since last interaction with this creator | These are exactly what the two-tower cannot express |

**Representation.** Learned ID embeddings for users/items/creators with hashing for the tail (accept collisions; monitor collision rate); multimodal content encoders frozen or fine-tuned; continuous features normalized by quantile and expanded (`x`, `x²`, `√x`) — cheap and consistently helps in these architectures.

**Point-in-time correctness — the practical answer.** Don't reconstruct training features from the warehouse; **log the served feature vector** and train on that. It makes training/serving skew structurally impossible for logged features. For the rest, use one shared transformation library compiled into both paths, plus a skew detector: recompute offline features for a 0.1% sample of logged requests and alert on distribution divergence. Training/serving skew is the #1 cause of "great offline, flat online" in real deployments.

---

## 06 · Model

### Baseline ladder — earn the complexity

| # | Model | Ships in | Why you'd stop here |
|---|---|---|---|
| 0 | Popularity by (country, language, device), time-decayed | days | Astonishingly strong; the honest A/B control |
| 1 | Item–item co-visitation counts ("because you watched X") | 1–2 weeks | Explainable, near-zero latency, big win over #0 |
| 2 | Implicit MF / ALS / BPR | 2–4 weeks | Good when catalog is small and stable |
| 3 | **GBDT ranker (LambdaMART) on tabular features** | 4–6 weeks | Often beats a DNN until you add sequence features. This is the baseline to beat, and the one candidates skip |
| 4 | **Two-tower retrieval + multi-task DNN ranker** | quarter | Needed at 80M eligible items, multi-objective, content-based cold start |
| 5 | Sequence model (transformer over user history) | quarter+ | Justified when session intent dominates (music, short-form) |

**Justify going deeper, explicitly:** a DNN earns its keep when you need (a) content embeddings for cold start, (b) multi-task heads with shared representation, (c) sequence modelling. If none of those apply — a 20k-title licensed catalog, mostly stable — GBDT + a good candidate set is the right architecture and going further is résumé-driven engineering.

### Why two stages (do the arithmetic)

Scoring 80M items at 60k QPS with a 1ms-per-1000-items ranker is 4.8×10¹² item-scorings/second. Not a budget question — a physics question. So:

```
eligible catalog        ~80,000,000
   ↓  retrieval: ~8 parallel sources, ANN + rules          ~20ms
candidate union             ~4,000
   ↓  pre-rank: tiny MLP on cached embeddings             ~15ms
ranking set                   ~600
   ↓  rank: multi-task DNN, full cross features           ~70ms
scored set                    ~600
   ↓  policy / diversity / business rules                 ~20ms
returned feed                    30
```

Each stage is ~100× cheaper per item and ~10–100× less accurate; the funnel buys accuracy exactly where the candidate set is small enough to afford it.

### Retrieval: an ensemble, not one model

A single ANN index is a common wrong answer. Production retrieval is a **union of sources with per-source quotas**:

1. **Two-tower ANN** — user tower computed online from live session state; item tower precomputed. Dot-product, ANN index.
2. **Item–item co-visitation** seeded from the last few interactions — catches short-term intent the tower smooths away.
3. **Follows/subscriptions** — deterministic, high precision.
4. **Continue watching / resume** — rule-based, highest precision of all.
5. **Fresh & trending** per locale — the *only* source that can surface items with no interaction history.
6. **Search/intent-derived** from recent queries.
7. **Exploration source** — sampled with logged propensities (see feedback loops).

Quotas prevent one source from starving the others, and per-source recall is monitored independently — when quality drops, you need to know *which* source degraded.

**Two-tower specifics worth naming:** shared item encoder over content + ID; L2-normalized embeddings with a learned temperature; sampled softmax + logQ; item embeddings refreshed nightly with incremental updates every few minutes for new items; **the user tower runs at request time**, not precomputed, because a user's last three actions are the most predictive features you have and a nightly user vector throws them away.

### Ranking

Multi-task DNN — shared bottom (embeddings + sequence encoder over the last N items) feeding **MMoE**-style experts with per-task gates, one head per label, plus the shallow position tower added to the logits and disabled at serving. Per-head isotonic/Platt calibration fitted on held-out recent data, refreshed daily. Final score = value model over calibrated head probabilities.

### Policy / re-ranking layer (the stage people forget)

Runs on the top ~600 and is mostly *not* ML: hard eligibility (licensing, region, age rating, already-watched, policy), dedupe near-identical items, creator/topic caps per page, **diversity via MMR or a DPP** over item embeddings, freshness boost, exploration slot injection, and business overrides. Being explicit that hard constraints belong here — not in the loss — is a maturity signal: you cannot guarantee a licensing constraint with a soft penalty term.

### Cold start

**New item (the harder one).**
- Day 0 the content tower gives a usable embedding from title/description/audio/frames — the item is retrievable before it has a single interaction. This is the main reason to pay for a DNN retrieval model at all.
- Reserve an **exploration budget** (~3–5% of slots) allocated by Thompson sampling over each item's engagement posterior, with the Beta prior *seeded from content-nearest-neighbours* rather than uniform — this cuts the exploration cost by an order of magnitude.
- Graduate: once an item passes ~1k impressions, its learned ID embedding takes over — **initialized from the content vector**, not randomly, so there's no quality dip at handover.
- Index freshness: incremental ANN inserts every few minutes. A nightly index rebuild means news, live, and new-release music are structurally broken.

**New user.**
- Onboarding preference selection if the product allows it; otherwise a prior from (locale, language, device, referrer).
- Because the user tower is computed online, the model conditions on interaction #1 within seconds — this is the strongest cold-start mechanism and it comes free from the serving design.
- Shrink personalized estimates toward segment priors with weight rising in interaction count.
- Deliberate early diversity: an exploitative feed for a brand-new user learns nothing about them.

**New market.** Transfer the content encoder, retrain ID embeddings, lean on locale popularity priors, and expect the value-model weights to need re-tuning per market.

### Feedback loops (the answer that separates senior from staff)

The system trains on data it generated. Untreated, this produces rich-get-richer popularity collapse, a filter bubble per user, and *offline metrics that improve while the product gets worse* — because the eval set is drawn from the model's own choices.

Countermeasures, in order of value:

1. **A random-traffic holdout** — 0.5–1% of requests served from a fixed, non-personalized or uniformly-sampled policy. It is the only unbiased evaluation set you will ever have and the only training data that contains items your ranker refuses to show. If the org has one, protect it; if not, buying it is the highest-leverage thing on the roadmap.
2. **Logged propensities + off-policy evaluation** — IPS, self-normalized IPS (SNIPS), and doubly-robust estimators with clipping, so an offline replay of a new policy means something.
3. **logQ / popularity-debiased sampling** in retrieval training.
4. **Diversity constraints in the policy layer** (DPP), which bound bubble formation regardless of what the model wants.
5. **Monitor the loop directly:** impression Gini over time, catalog coverage, share of impressions to items <7 days old, % of users seeing new creators. These trend badly for *months* before anyone notices in engagement metrics.

---

## 07 · Serving

**Latency budget, p99 = 300ms** (state it as a budget, not a hope):

| Stage | ms |
|---|---|
| Request parse, auth, eligibility context | 10 |
| User + context feature fetch (parallel) | 25 |
| User tower forward pass | 8 |
| Retrieval — ANN shards + other sources, parallel | 25 |
| Merge, dedupe, hard filters | 10 |
| Pre-rank 4,000 → 600 | 15 |
| Item + cross feature hydration for 600 | 45 |
| Ranker (batched, multi-task) | 70 |
| Policy / diversity re-rank | 20 |
| Assembly + logging | 12 |
| **Subtotal / headroom** | **240 / 60** |

The two expensive lines are feature hydration and the ranker — which is exactly why the pre-ranker exists, and the number to attack first if the budget is tighter.

**Precompute vs. real-time.**
- Precompute: item embeddings, item features, ANN index, co-visitation tables, segment popularity lists.
- Real-time: user tower, cross features, ranking, policy.
- Fully precomputed feeds are right for *push notifications and email* (no latency constraint, huge batch efficiency) and as a warm fallback for the feed. A hybrid — precompute a nightly feed, patch it with real-time results for active sessions — is a legitimate cost lever at very high QPS.

**Caching.** Session-scoped candidate cache (TTL ~1 min, invalidated on new interaction) — cheap because consecutive requests in a session share most of the funnel; item feature cache (near-100% hit, items outnumber changes); embedding cache; negative caching for ineligible items.

**ANN index.** 80M × 128-dim fp32 = 41GB — too much per replica, so product-quantize (m=32 subquantizers → 32B/vector ≈ 2.6GB plus graph) and shard. HNSW where recall/latency dominate, IVF-PQ where memory does. Rebuild nightly, incremental inserts continuously. **Measure ANN recall against exact search** on a sample — an ANN index silently degrades as it's mutated, and nothing else in the funnel will tell you.

**Graceful degradation — define a fallback per stage, and never return empty:**
`ranker times out → serve pre-rank order` · `retrieval fails → cached session candidates` · `cache miss → precomputed nightly feed` · `total failure → popularity-by-locale`. Track the fallback rate as a first-class metric; a silently rising fallback rate looks exactly like a model regression.

**Cost check.** 60k QPS × 600 ranked items = 36M scorings/sec. At a few hundred FLOPs-per-item-per-layer this sets the ranker's parameter budget directly — model capacity is a function of the latency and cost budget, not of what's fashionable.

---

## 08 · Evaluation & rollout

The full ladder, cheapest and least trustworthy first:

1. **Offline replay** on logged data with propensity correction (IPS/SNIPS/DR, clipped). Good for eliminating bad ideas; never trust the magnitude.
2. **Interleaving** (team-draft) — mix two rankers' results into one list and attribute engagement per source. 10–100× more sample-efficient than an A/B, and it controls for user-level variance completely. The right intermediate step for ranker comparisons; it cannot measure whole-experience effects like session count.
3. **Shadow deployment** — score live traffic, serve nothing. Validates latency, feature coverage, score distribution, calibration under real load.
4. **A/B test.** Randomization unit = **user**, not request (request-level randomization contaminates via session state and user learning). Power: with per-user daily watch minutes having cv ≈ 2, detecting a 0.5% relative lift at 80% power needs roughly `16·cv²/δ² ≈ 2.5M users per arm` — worth computing out loud, because it explains why small teams cannot detect the effects they claim. Run ≥2 weeks for novelty decay and weekly seasonality; use sequential/always-valid tests if you need to peek.
5. **Ramp** 1% → 5% → 20% → 50% → 100%, with automated rollback on guardrail breach.
6. **Long-term holdback** — keep 0.5–1% on the previous system for months. Recsys changes compound; a year of individually-positive two-week experiments can sum to a negative annual effect, and the holdback is the only way to see it.

**Two-sided caveat:** for changes affecting supply (creator exposure, new-item boosts), user-level randomization leaks through the shared item pool — treatment users' engagement changes the item's popularity features, which affects control users. Use cluster/market-level or creator-level randomization, and accept the much larger variance.

---

## 09 · Monitoring & iteration

**Data health:** feature freshness and null rates per feature, schema validation, distribution drift (PSI/KL vs. training window), the training-serving skew detector from §05, and *logging completeness* — a silently dropped propensity field kills off-policy eval a month before anyone notices.

**Model health:** daily calibration by slice, score-distribution drift, per-slice user-AUC (new users, new items, market, device), ANN recall vs. exact, and a **staleness curve** — plot online metric against days-since-training to derive the retraining cadence empirically instead of guessing.

**Ecosystem health:** impression Gini, catalog coverage, fresh-item impression share, exploration budget actually spent (it silently drifts to zero when someone "optimizes" the policy layer), creator concentration.

**Retraining cadence,** justified by the staleness curve:
- ANN index: incremental inserts every few minutes
- Ranker: incremental hourly, full retrain daily
- Retrieval towers: daily
- Value-model weights: quarterly, by experiment
- Content encoders: on a slow cycle, monthly or on catalog shift

**Operational:** model version is a config flip with the last N versions kept warm; rollback in seconds, not a redeploy. Every serving log row carries the model version, or you cannot attribute a regression.

---

## Product variants — what actually changes

| | Netflix | YouTube | Spotify |
|---|---|---|---|
| Catalog | ~10⁴ titles | ~10⁹ videos | ~10⁸ tracks |
| Retrieval needed? | **No** — score everything; spend the compute on 2-D page/row layout and artwork selection instead | Yes, aggressively | Yes, plus strong sequence/session modelling |
| Dominant signal | completion, abandonment | valid watch, satisfaction survey | **skip within 30s**, repeat listens |
| Hard part | page composition, per-title artwork, small-catalog cold start for new releases | scale, freshness, clickbait/rabbit-hole control | sequence + context (time of day, activity), playlist coherence, repeat vs. discovery balance |
| Cold start | licensed metadata is rich; exploration is cheap | 50k new items/hour; content embeddings mandatory | audio embeddings are unusually informative |

Saying "at Netflix scale I would **not** build a retrieval stage" is a stronger answer than reciting two-stage architecture universally. The two-stage design is a response to catalog size, not a law.

---

## Likely follow-ups, with short answers

**"A user watches one video about X and the feed floods with X."**
Three fixes at different layers: topic caps in the policy layer (bounded regardless of scores); time-decay on short-term affinity features so one session can't dominate; an explicit intent-vs-taste split, where short-term intent gets a quota of slots rather than the whole page. Measure it with intra-session topic entropy, not with engagement.

**"How would you add ads / sponsored content?"**
Separate the auction from relevance: the ranker produces a calibrated `P(engagement)`, the ads system produces `eCPM = bid × P(click)`, and a blending policy trades user value against revenue at a tunable exchange rate. This makes **calibration a hard requirement** — an uncalibrated organic score corrupts the auction.

**"Offline metrics improved, online is flat. What do you check?"**
In order: training/serving skew (recompute serving features offline and diff), calibration drift, whether the offline eval set was drawn from the current policy's own impressions, negative-sampling recalibration, position-bias handling, and whether the winning offline slice is a segment with little traffic.

**"How do you know retrieval, not ranking, is the bottleneck?"**
Ceiling analysis: replace the ranker with an oracle that reorders the candidate set by observed labels. If oracle-ranking of current candidates doesn't move the metric much, the good items aren't in the candidate set and retrieval is the bottleneck. Corollary: track Recall@k of the retrieval union against items the ranker eventually ranked top-10 in an unconstrained offline run.

**"Why not one big model?"**
Because the item tower must be precomputable to build an index. The moment you add user×item cross features, you have to evaluate the model per (user, item) pair, and 80M forward passes per request is not available. One model is the right answer only when the catalog is small enough to score exhaustively.

**"Ranking looks great but engagement dropped for new users."**
Expected: the model is trained on data dominated by heavy users, so the loss barely notices new users. Fix with per-slice evaluation as a gate, loss reweighting or a segment-specific model, more exploration for low-history users, and stronger reliance on the session-based path.

---

## The 90-second opening (if you only get one shot)

> "I'll assume a video home feed: 70M DAU, ~60k peak QPS, 80M eligible items, 300ms p99. The business objective is retention, which isn't trainable, so I'll optimize a per-impression surrogate — a weighted combination of calibrated engagement and satisfaction probabilities — and tune those weights by A/B against retention.
>
> Scoring 80M items per request is impossible, so it's a funnel: an ensemble of retrieval sources cuts 80M to ~4k, a pre-ranker to ~600, a multi-task DNN ranks those, and a policy layer applies diversity and hard constraints to produce 30. Baseline is popularity plus co-visitation, then a GBDT ranker; I'd only go to DNNs for content-based cold start, multi-task heads, and sequence features.
>
> The three things I'd expect to actually go wrong: training/serving skew, position and popularity bias feeding back into training data — which is why I want a random-traffic holdout — and cold start for new items, which I'd handle with a content tower plus a Thompson-sampled exploration budget. Where should I go deep?"
