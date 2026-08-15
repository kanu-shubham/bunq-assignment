# Design a Search Ranking System

An ML system design walkthrough, staff level, structured as a 45-minute interview.

**Running example:** search inside a large marketplace app — 100M items, 10k QPS peak, 200ms p99 budget for the whole search response. The template at the bottom shows how to swap in a different domain (docs search, feed, job matching, in-app banking search) without rewriting the answer.

**Time budget for 45 minutes.** The failure mode is spending 20 minutes on model architecture and never reaching serving or evaluation. Interviewers score breadth of the *system*, then depth in two or three places.

| Phase | Minutes | What you must land |
|---|---|---|
| Clarify & scope | 5 | One business objective, translated to one ML metric |
| ML framing | 4 | Label, example granularity, loss |
| Metrics | 5 | Offline + online + guardrails |
| Data | 6 | Logging, sampling, position bias, leakage |
| Features | 5 | Four families, point-in-time correctness |
| Model | 8 | Baseline → two-stage → LTR, with justification |
| Serving | 6 | Latency budget that adds up, ANN, caching |
| Eval & rollout | 4 | Replay → shadow → A/B → ramp |
| Monitoring | 2 | Drift, feedback loops, retraining triggers |

---

## 1. Clarify & scope

Do not start designing. Ask five questions, and say out loud what each answer changes.

**Business objective — what is search *for* here?** "Relevance" is not an objective. Candidates:

- Marketplace: conversion / GMV per search session, subject to not tanking buyer trust.
- Content platform: session depth, long-term retention.
- Support search: deflection rate — the user finds the answer without opening a ticket.
- Enterprise/internal docs: time to first useful result.

Pick one and commit: **maximize the probability that a search session ends in a purchase, without regressing p99 latency or seller diversity.** Everything downstream — label, loss, guardrails — follows from that sentence. If the interviewer won't pick, state your assumption and move on; blocking on this wastes the clock.

**Translate to an ML metric.** Session-level conversion is not directly optimizable per-request, so decompose:

> business: session conversion → proxy: user finds a relevant item high in the list → **ML metric: NDCG@10 on graded engagement labels**, with purchase-weighted gains.

Say the gap out loud — NDCG going up does not guarantee GMV goes up. That gap is exactly what the A/B test exists to measure, and naming it early is a strong signal.

**The four scale questions.** Their answers are load-bearing, not trivia:

| Question | Typical answer | What it changes |
|---|---|---|
| QPS | 2k avg, 10k peak, spiky (promotions) | Whether a cross-encoder is affordable; cache design; index replica count |
| Latency SLA | 200ms p99 server-side | The entire model choice. 200ms → GBDT rank + optional small neural; 500ms → cross-encoder on top-50 |
| Corpus size & volatility | 100M items, ~5% change daily, prices change hourly | Two-stage is mandatory; embedding index refresh cadence; which features can be precomputed |
| Traffic volume | 50M searches/day, ~30% with a click | You have enough clicks for a neural ranker; you do *not* have enough purchases per (query, item) pair — that drives label design |
| Existing infra | Elasticsearch cluster, Spark, a feature store, no vector DB | Ship BM25 + GBDT first. Introducing a vector index is a project, not a line in the design |

**Scope cuts to state explicitly:** English only for v1; web + mobile share one ranker with a surface feature; no personalized ranking for logged-out users beyond session context; ads and organic are ranked separately and blended by a downstream policy layer (mixing them into one model is a different, harder problem).

---

## 2. Frame as ML

Three questions. Answer them in this order and be precise — this is the part beginners skip and staff engineers dwell on.

### What is the label?

You do not have relevance labels. You have logs. Construct graded relevance from engagement:

| Signal | Grade | Notes |
|---|---|---|
| Purchase / booking | 4 | Sparse but the closest to the objective |
| Add to cart / save | 3 | ~10× denser than purchase |
| Click with dwell ≥ 30s or scroll depth | 2 | Dwell threshold tuned per vertical |
| Click, immediate bounce (< 10s) | 0 or 1 | A "bad click" — title looked good, item didn't. Treating this as positive teaches clickbait |
| Impression, no click | 0 | Only counts if the item was **viewed** — see below |

Two rules that separate a real design from a toy one:

1. **Only viewed impressions are negatives.** An item rendered at position 30 that the user never scrolled to is not a negative — it is missing data. Log a viewport/visibility signal and drop everything below the deepest viewed position.
2. **Bad clicks must be distinguishable from no clicks.** A model trained on raw clicks optimizes for clickbait. Dwell-gating is the cheapest fix.

Optionally add a **human-rated golden set** — a few thousand (query, item) pairs graded 0–4 by trained raters against a written guideline, refreshed quarterly. It is too small to train on and essential to evaluate on, because it is the only label source not contaminated by what your current ranker chose to show. LLM-as-judge can extend it cheaply for pre-launch checks; calibrate it against human raters before trusting it, and never let it be the only ground truth.

### What is one training example?

**Not** one (query, item) row. One example is **one slate**: a query-session and the full list of viewed candidates with their labels.

```
example = {
  qid:      (query_text, user_id, timestamp, surface),
  docs:     [(features_1, grade_1, position_1), ..., (features_n, grade_n, position_n)],
}
```

Granularity matters for three reasons: (a) listwise losses need the group; (b) your train/test split must be **by query group and by time**, never random rows, or the same slate leaks across the split; (c) NDCG is computed per group and averaged, so grouping is how the metric and the loss agree.

### What is the loss?

Ranking is not classification. Three families:

- **Pointwise** — predict grade per item, MSE or cross-entropy. Simple, works with any classifier, but optimizes absolute score rather than order, and lets easy queries with many candidates dominate the gradient. Fine as a baseline, and required if you also need a calibrated probability (for ads auctions or blending).
- **Pairwise** — for every pair (i, j) with grade_i > grade_j within a group, learn P(i ranked above j) = σ(s_i − s_j), optimize cross-entropy (RankNet). Directly models order. Weakness: all pairs weighted equally, so fixing positions 30 vs 40 counts as much as 1 vs 2 — which NDCG doesn't care about.
- **Listwise** — optimize a list-level objective. **LambdaRank** is the practical answer: take the RankNet gradient and scale it by the NDCG change from swapping the pair.

$$\lambda_{ij} = \frac{-\sigma}{1 + e^{\sigma(s_i - s_j)}} \cdot \left| \Delta \text{NDCG}_{ij} \right|$$

That `|ΔNDCG|` term is the whole trick: it makes the gradient care about the swaps that move the metric, which are the ones near the top. Plug those gradients into gradient-boosted trees and you have **LambdaMART** — still the default production ranker for tabular ranking features, and the correct thing to name here. Softmax cross-entropy over the slate (ListNet / ListMLE) is the neural equivalent and is what you use once features go dense.

**Say this:** "I'd start pointwise for the baseline because it's the fastest path to a working system, then move to LambdaMART, because the metric I committed to is NDCG@10 and LambdaMART optimizes a smooth surrogate of exactly that."

---

## 3. Metrics

Three tiers. Naming all three, and knowing that they disagree, is the point.

### Offline

| Metric | Where it applies | Why |
|---|---|---|
| **Recall@k** (k = 500–1000) | Retrieval stage only | The ceiling on everything downstream. If the relevant item isn't in the candidate set, no ranker can save you. Measure it separately per retrieval source |
| **NDCG@10** | Ranking stage | Primary. Position-discounted, handles graded labels. Report @1, @5, @10 — @1 moves differently and users feel it most |
| **MRR** | Navigational queries | When there is exactly one right answer ("track my order"), NDCG is the wrong lens |
| **Precision@1** | Ranking | The single most user-visible number |
| **Calibration (ECE, reliability plot)** | Pointwise scores only | Only matters if the score is *used* as a probability — ad auctions, blending organic with sponsored, thresholding for "no good results". A pure ranker does not need calibration, and saying so shows you know why the metric exists |
| **AUC** | Diagnostic only | Global AUC over pooled rows is misleading for ranking — it rewards separating easy queries from hard ones. Use per-query grouped AUC if you use it at all |

Always report metrics **sliced**: head vs torso vs tail queries, new vs returning users, mobile vs web, and by query intent class. An aggregate NDCG gain of +2% that comes entirely from head queries while tail regresses is a bad launch, and slicing is how you catch it before the A/B test does.

### Online

- **Primary:** session conversion rate / purchases per search session.
- **Engagement:** CTR@top-3, mean reciprocal click position, click-through depth.
- **Success proxies:** query reformulation rate ↓ (reformulating means you failed), zero-click session rate ↓, time-to-first-click ↓.
- **Long-term:** searches per user per week, 7/28-day retention. Underpowered in a two-week test — flag that and use holdback groups for long-horizon reads.

### Guardrails (a launch blocker if any regress)

- p99 latency, error rate, index freshness lag.
- **Zero-result rate** — semantic retrieval usually improves this; a regression means the query understanding layer broke.
- **Diversity / seller concentration** — Gini or top-k supplier share. Pure relevance optimization concentrates traffic on a few sellers and slowly kills marketplace supply.
- **Catalog coverage** — fraction of inventory that receives any impression over a week. Guards the feedback loop in §9.
- Fairness/compliance slices where applicable.

---

## 4. Data

### Sources

1. **Search logs** — query, user, timestamp, the full ranked slate with positions, viewport events, clicks, downstream conversions. This is the training set. It is also *biased by the ranker that produced it*, which is the central problem of the whole field.
2. **Catalog** — item text, attributes, images, price, stock, seller.
3. **Query logs** — reformulation chains, spelling corrections, autocomplete acceptances. Rich source for query understanding.
4. **Human ratings** — the unbiased golden set.

### Logging (design this deliberately)

Log the **features as they were computed at serving time**, not just the IDs. "Log and serve" is the single most effective defense against train/serve skew: if you recompute features later from warehouse tables, you will silently use a price that changed, a CTR that includes the future, or a user profile that already saw the result. Logging feature vectors costs storage and saves months of debugging.

### Sampling

- **Negatives come from the same slate**, not from random catalog items. A random negative is trivially separable and teaches the ranker nothing about the hard decisions it actually faces. (Exception: the *retrieval* two-tower model does want easy random/in-batch negatives, plus mined hard negatives — different stage, different distribution.)
- **Downsample negative-only slates** — queries with zero engagement are ~70% of traffic and mostly carry no ranking signal. Keep a sample, and reweight if you need calibrated outputs.
- **Cap per-query contribution** so one high-volume head query doesn't dominate. Weight groups by log(frequency) or cap at N slates/query/day.
- **Window:** 3–6 months of training data with recency weighting. Longer captures seasonality; too long and it encodes an obsolete catalog.

### Position bias — the thing you must bring up unprompted

Users click position 1 because it is position 1. Under the examination hypothesis:

$$P(\text{click} \mid q, d, k) = \underbrace{P(\text{examine} \mid k)}_{\text{propensity } p_k} \cdot \underbrace{P(\text{relevant} \mid q, d)}_{\text{what you want}}$$

Train naively and you learn "whatever the old ranker put on top is good" — a self-fulfilling loop that ossifies the system. Three fixes, in increasing cost:

1. **Position as a feature, zeroed at inference.** Cheap, partially works, standard practice. Feed position (or a "was shown at rank k" feature) during training; set it to a constant at serving so the model attributes the rest of the signal to real relevance.
2. **Inverse propensity scoring.** Weight each example by 1/p_k. Estimate p_k from a small randomization experiment (swap adjacent pairs on 1% of traffic — cheap and unbiased) or by regression-EM over natural rank variation across sessions. Watch variance: clip weights.
3. **Two-tower debiasing model** — one tower learns bias from position/context, one learns relevance; keep only the relevance tower at serving.

Also name **trust bias** (users believe top results, so clicks there are more likely even when irrelevant) and **selection bias** (you only observe items the old system retrieved — which caps how much a new retrieval stage can be evaluated offline at all).

### Leakage

- **Split by time, always.** Train on weeks 1–8, validate 9, test 10. A random split leaks future engagement into the past through item-level CTR features.
- **Point-in-time features.** `item_ctr_7d` must be computed as of the request timestamp. Computing it from a snapshot table means the feature includes clicks caused by the very impression you're predicting. This is the most common silent killer of offline/online correlation and is worth stating with conviction.
- Watch entity leakage between splits for personalization features.

### Imbalance & delayed feedback

- Purchases are ~1% of clicks. Handle by graded labels (a purchase is grade 4, not a separate binary task) rather than by resampling, which distorts calibration.
- **Delayed conversions:** a purchase can land days after the search. Choose an attribution window (e.g., 24h same-session, 7d for the long tail), accept that the freshest data is incomplete, and either wait out the window before training or model the delay explicitly (delayed-feedback modeling with an exponential delay distribution). At minimum, never train on a partially-attributed last day and wonder why conversion features look depressed.

---

## 5. Features

Four families. Interviewers listen for whether you name interaction features, which are where the signal actually lives.

**Query:** length, language, detected intent (navigational / informational / transactional), spell-corrected form, extracted entities (brand, category, size, color, price constraint), query frequency bucket, historical query-level CTR, embedding.

**Item:** price, discount, ratings count/mean, age, stock, seller quality tier, image quality score, category, text embedding, historical CTR and conversion rate (smoothed — a Bayesian prior toward the category mean, otherwise a 1-impression-1-click item scores 100%).

**User / context:** device, surface, time of day, locale, session queries so far, recently viewed/purchased categories, long-term affinity embedding, logged-in state, price-tier affinity. Cold users get backoff to segment averages, never NaN.

**Interaction (query × item) — the highest-signal family:**

- Lexical match: BM25 score, exact title match, coverage (fraction of query terms present), match position (title vs description vs reviews).
- Semantic: cosine similarity between query and item embeddings, plus a cross-encoder score if the latency budget allows.
- Behavioral: historical CTR for *this* (query, item) pair and for (query-cluster, item) — the strongest single feature in most production rankers, and also the one most prone to feedback loops and point-in-time leakage.
- Personalization: similarity between user affinity embedding and item embedding; distance between the item's price and the user's typical price tier.

**Point-in-time correctness** applies to every aggregate here. The clean implementation is a feature store with as-of joins for training and a low-latency online store serving the same transformation code. If training and serving compute a feature through two different code paths, they will diverge — and the divergence shows up as "great offline, flat in A/B", which is the most expensive failure mode in this entire design.

---

## 6. Model

### Architecture: two stages (three in practice)

100M items, 200ms budget. You cannot score 100M items. Nobody can. So:

```
query
  │
  ├─ Query understanding: spell correct, tokenize, intent classify, entity extract, expand
  │
  ├─ RETRIEVAL  (100M → ~1000, budget ~40ms, metric: Recall@1000)
  │    ├─ Lexical: BM25 / inverted index  ← handles exact, rare tokens, SKUs, filters
  │    └─ Semantic: two-tower embeddings + ANN  ← handles paraphrase, intent, zero-result queries
  │         fused by reciprocal-rank fusion or a light learned blend
  │
  ├─ PRE-RANK   (1000 → ~100, budget ~10ms, cheap GBDT on ~30 features)   [add when needed]
  │
  ├─ RANK       (100 → ordered, budget ~40ms, metric: NDCG@10)
  │    └─ LambdaMART / neural listwise ranker on the full feature set
  │
  └─ POLICY LAYER: business rules, diversity/dedup, freshness boosts, ads blending, filters
```

The policy layer deserves a sentence: never bake business rules into the model. Promotions, banned-item filters, and seller quotas change weekly and need to be auditable and instantly reversible. Model produces a score; policy produces the final list.

### Retrieval detail

**BM25** as the lexical backbone:

$$\text{score}(q,d) = \sum_{t \in q} \text{IDF}(t) \cdot \frac{f(t,d)\,(k_1+1)}{f(t,d) + k_1\left(1 - b + b\frac{|d|}{\text{avgdl}}\right)}$$

with k₁ ≈ 1.2, b ≈ 0.75. It is strong, free, interpretable, and unbeatable on rare exact tokens ("iPhone 14 Pro 256GB"). It fails on vocabulary mismatch — "laptop for kids" vs an item titled "children's notebook computer."

**Semantic retrieval** fixes exactly that failure. Two-tower (dual encoder): a query encoder and an item encoder trained to put engaged pairs close in embedding space, with in-batch negatives and a sampled-softmax loss. Two details worth naming:

- **logQ correction** — in-batch negatives oversample popular items, so subtract log(sampling probability) from the logits, otherwise the model learns popularity, not relevance.
- **Hard negative mining** — after a first round, retrieve top-k with the current model, take the non-engaged ones as negatives, retrain. This is what moves Recall@k; random negatives alone plateau fast.

Item embeddings are precomputed offline (nightly full rebuild + streaming updates for new items) and loaded into an ANN index. The query tower runs at request time — it must be small, a distilled 4–6 layer transformer or even a bag-of-embeddings model for the tightest budgets.

**Hybrid, not either/or.** Ship BM25 first; add semantic as a second source and fuse. Reciprocal rank fusion (`Σ 1/(60 + rank_i)`) is a strong, tuning-free baseline for combining the two lists.

### Ranking model progression

Justify each step by what it buys, not by novelty:

1. **Baseline: BM25 + hand-tuned boosts.** Ship in a week. Establishes the A/B floor. Every later model is measured against this.
2. **Logistic regression** on ~20 features. Proves the data pipeline, logging, and serving path end-to-end. The value is the infrastructure, not the model.
3. **GBDT — LambdaMART (LightGBM `lambdarank`).** The workhorse. Handles heterogeneous tabular features with no scaling, missing values natively, trains in minutes, is debuggable via feature importance and partial dependence, and gives typically +5–15% NDCG over the linear baseline. ~500 trees, depth 6–8, scores 100 docs in single-digit milliseconds. **This is the right production answer for the stated latency budget**, and saying so confidently — rather than reaching for a transformer — is what reads as senior.
4. **Go deeper only when a specific limitation bites:**
   - Rich text/image semantics that trees can't consume from raw form → neural ranker with embeddings, GBDT score as an input feature.
   - Sequential intent within a session → transformer over the session's event sequence.
   - Fine-grained query-item token interaction → **cross-encoder** (query and item jointly encoded). Big quality jump, and 100× the cost — so apply it only to the top 20–50 documents, and distill it into a smaller student or into the GBDT as a feature.
5. **Multi-objective** when the business wants relevance *and* revenue *and* diversity: multi-task heads sharing a trunk (P(click), P(purchase), predicted margin), combined at serving as `score = P(click)^α · P(purchase)^β · value^γ`. Tune the exponents by A/B, not by offline metric — the whole point is that the tradeoff is a business decision, not an optimization one.

**Cold start:** new items have no engagement features. Backoff to content features and category-level priors, plus a small exploration budget (ε-greedy slots or Thompson sampling over an uncertainty estimate) so new inventory can earn impressions. Without this, the ranker cannot discover anything it hasn't already shown.

---

## 7. Serving

### Latency budget (200ms p99, server-side)

| Stage | Budget | Notes |
|---|---|---|
| Gateway, auth, parse | 10ms | |
| Query understanding | 15ms | Spell + intent + entities; cache aggressively — query distribution is Zipfian |
| Retrieval (lexical ∥ semantic) | 40ms | Run in parallel, take the slower; ANN search is ~10ms, BM25 ~30ms with a warm index |
| Feature fetch | 30ms | The usual bottleneck. One batched multi-get for 100–1000 items, not N calls |
| Pre-rank | 10ms | Only if the candidate set exceeds the ranker's throughput |
| Ranking | 40ms | 100 docs × ~500 trees, batched, SIMD-friendly |
| Policy, dedup, diversity | 10ms | |
| Serialization + network | 25ms | |
| **Slack** | **20ms** | Reserve it. Budgets that sum exactly to the SLA fail at p99 |

**If it doesn't fit:** cut candidates before cutting model quality (1000 → 500 costs less NDCG than dropping the ranker), add the pre-rank stage, distill the model, or degrade gracefully — return the retrieval-order list on ranker timeout rather than erroring. Every stage needs a timeout and a defined fallback; a search box that returns nothing is worse than one that returns BM25 order.

### Precompute vs. real-time

| Precompute (offline/nightly) | Real-time (per request) |
|---|---|
| Item embeddings, ANN index | Query embedding, query understanding |
| Item aggregates (CTR, conversion, quality) | Session context features |
| User long-term affinity embeddings | Price, stock, eligibility (must be fresh — never serve an out-of-stock item because the index was stale) |
| Head-query result lists | Ranking scores |

### Caching

- **Query understanding cache** (Redis, ~1h TTL) — the top 10k queries are a large share of traffic; near-100% hit rate on head.
- **Full result cache** for head queries, keyed by (normalized query, filters, user segment), TTL 5–15 min. Never key by raw user ID — the hit rate collapses and you leak personalization across users.
- **Embedding cache** for repeated queries within a session.
- Cache invalidation on price/stock change, or accept the staleness and re-verify availability at render time.

### ANN index

**HNSW** for the quality/latency sweet spot (M=32, efConstruction=200, efSearch tuned to hit recall ≥0.95 against exact search — measure this, don't assume it). Memory is the constraint at 100M × 768 float32 ≈ 300GB, so: reduce dimension to 128–256 via a trained projection, quantize to int8 (~4× saving with negligible recall loss), or move to **IVF-PQ / ScaNN** if memory dominates. Shard by item ID across replicas, rebuild nightly, apply incremental updates for new items, and blue/green swap the index so a bad rebuild never takes serving down.

### Infrastructure notes worth one line each

Model served behind a versioned inference service; features fetched from the online store with the *same* transformation code as training; index and model versions pinned together in one deployable so a rollback restores a consistent pair; per-stage circuit breakers; shadow-traffic capability built in from day one (see next section).

---

## 8. Evaluation & rollout

Four gates, each answering a different question, each able to stop the launch.

**1. Offline replay.** Score held-out logged slates with the new model, compute NDCG@10 / Recall@1000, sliced by segment. Fast, cheap, and *biased* — it can only tell you how well you reorder what the old system retrieved. For a new retrieval stage it is nearly blind, and saying that is a strong signal. Use counterfactual estimators (IPS / SNIPS) with clipped weights when you need an unbiased-ish estimate of a policy that reorders substantially.

**2. Shadow / dark traffic.** Run the new pipeline on real production traffic, serve the old results, log both. This validates infrastructure, not quality: latency at true peak, error rates, feature availability, and **train/serve skew** — compare feature distributions between the shadow request path and training data. This is where the "great offline, dead online" bugs get caught. Run for a few days.

**3. Interleaving (optional, high value).** Blend the two rankings into one list (team-draft interleaving) and attribute clicks to the source ranker. Roughly 10–100× more sensitive than an A/B test because it controls for user variance, so you get a directional read in hours instead of weeks. It measures preference, not business metrics — so it triages candidates cheaply, and the A/B still decides.

**4. A/B test.** The only thing that measures the business objective. Do the arithmetic out loud: at a 3% baseline conversion rate, detecting a 2% relative lift at 80% power and α=0.05 needs roughly 1.5M sessions per arm — about 2 weeks at 1% traffic, or 2 days at 10%. Randomize by **user**, not by request, or the same user sees both rankers and you contaminate the experiment. Check the guardrails before the primary metric; a +1.5% conversion win that costs 40ms of p99 and 10% of seller diversity is not a launch.

**Ramp:** 1% → 5% → 25% → 50% → 100%, holding at each step long enough to read guardrails, with a maintained holdback (1–5%) for a month to measure long-horizon effects like retention and to catch slow feedback-loop damage. Automated rollback on guardrail breach, and a kill switch that reverts to the previous model+index pair in one action.

**Novelty effect:** engagement often spikes for a few days on any visible change. Discount the first 3–7 days; if the effect vanishes by week two, it was novelty.

---

## 9. Monitoring & iteration

**What to watch, and what each alert means:**

| Signal | Alert on | Diagnosis |
|---|---|---|
| Feature drift (PSI > 0.2, null rate spike) | Per feature, daily | Upstream pipeline broke — the most common real-world outage, and it degrades silently |
| Score distribution shift | Mean/percentile shift | Model or feature regression |
| NDCG on the golden set | Weekly, > 2% drop | True quality decay; the golden set is the only unbiased tracker |
| CTR@1, reformulation rate, zero-result rate | Hourly | Live user-experienced quality |
| p99 latency, ANN recall vs. exact | Continuous | Index degradation after rebuilds |
| Seller concentration, catalog coverage | Weekly | Feedback loop tightening |

**Position bias in production** is not solved once. Re-estimate propensities periodically — the curve changes when the UI changes. A layout redesign silently invalidates every propensity you trained on.

**Feedback loops.** The ranker chooses what is shown; what is shown becomes tomorrow's training data. Left alone, this concentrates traffic on already-popular items, starves new inventory, and slowly narrows the catalog while every offline metric looks fine. Counter with: a permanent exploration budget (a randomized slot, or Thompson sampling on score uncertainty), the coverage guardrail above, and periodic randomized-traffic collection that yields genuinely unbiased evaluation data. Budget ~1% of traffic for this and defend it — it is the cost of being able to learn anything new.

**Retraining triggers** — cadence *and* condition:

- Scheduled: GBDT weekly, embeddings/two-tower monthly (index refresh nightly).
- Triggered: golden-set NDCG drop > 2%, PSI breach on a top-10 feature, or a catalog/UI change large enough to shift the feature distribution.
- Every retrain goes through the same gates: offline → shadow → A/B. A retrain is a new model, not a config change. Auto-retrain plus auto-deploy without a quality gate is how a bad data day reaches production.

**Iteration roadmap** (states priorities, which is a staff-level move):

| Quarter | Work | Expected |
|---|---|---|
| Q1 | BM25 + LambdaMART, logging, feature store, A/B infra | +8–12% NDCG over baseline |
| Q2 | Semantic retrieval, hybrid fusion | Zero-result rate ↓, tail-query recall ↑ |
| Q3 | Position-bias correction, exploration budget | Healthier data, better long-term learning |
| Q4 | Cross-encoder reranking on top-50, personalization | +3–5% NDCG, latency-gated |

---

## Adapting to another domain

The skeleton never changes: objective → label → two-stage → LTR → latency budget → A/B. What changes:

| Domain | Label | Retrieval twist | Dominant constraint |
|---|---|---|---|
| E-commerce | Purchase-weighted grades | Filters + faceting are first-class | Freshness (price, stock) |
| Docs / enterprise search | Click + dwell, small volume | Semantic matters more (small corpus, sparse clicks) | Little training data → lean on pretrained embeddings and rules |
| Feed / recommendations | No query — user *is* the query | Candidate generation from user embedding | Diversity and fatigue |
| Job / dating matching | Two-sided (both parties respond) | Reciprocal ranking | Fairness constraints are legal, not optional |
| In-app search (banking, SaaS) | Task completion / deflection | Small corpus, heavy intent classification | Precision@1 dominates; a wrong top result is expensive |
| Ads | Click, with a price | Auction downstream of the model | **Calibration is mandatory** — the score is a bid input, not a rank |

---

## Signals that read as senior

- Naming the objective→metric gap before designing, and treating the A/B as the arbiter.
- Choosing the *simplest* model that meets the constraint and defending it — LambdaMART over a transformer, with the latency budget as the argument.
- Bringing up position bias and feedback loops without being asked.
- Point-in-time correctness and log-and-serve, with the failure mode named ("great offline, flat online").
- A latency budget with slack in it and a defined fallback per stage.
- Guardrail metrics that can block a launch, including one that protects the ecosystem rather than the user.
- Sequencing: what ships in Q1 versus what needs a dedicated project.

## Traps

| Trap | Fix |
|---|---|
| Jumping to a neural ranker in minute 3 | Baseline first, then justify depth by a named limitation |
| Random train/test split | Split by time and by query group |
| Random negatives for the ranker | Negatives from the same slate; random negatives are for retrieval only |
| Recomputing features from snapshots | Point-in-time joins, log-and-serve |
| Treating all clicks as positive | Dwell-gate; bad clicks train clickbait |
| Ignoring items never viewed | Log viewport; unviewed ≠ negative |
| Latency budget that sums exactly to the SLA | Reserve 10% slack |
| "We'll A/B it" as the whole eval plan | Replay → shadow → interleave → A/B → ramp with holdback |
| Business rules inside the model | Policy layer, auditable and reversible |
| Optimizing NDCG and declaring victory | NDCG is the proxy; conversion is the objective |
