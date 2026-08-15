# Designing a News Feed / Timeline Ranking System

**Prompt:** *"Design the ranking system for a news feed (Facebook / LinkedIn-style)."*
**Category:** multi-objective ranking — a value model blending pCTR / pComment / pShare with freshness, diversity, and integrity filters.
**Level:** staff ML engineer. The bar is not "can you name a model"; it is *can you own the objective, the loop it creates, and the org's ability to keep shipping against it.*

---

## 0. The 30-second opening

Say this before any whiteboard marker moves:

> "Feed ranking is not a click-prediction problem, it's a **constrained value-allocation** problem. We have a scarce resource — the top ~20 slots of a session — and several stakeholders: the viewer, the creator, the advertiser, and the platform's long-term health. I'll build a system that predicts *several* user responses, converts them into one calibrated value score with explicit, governed weights, and then solves a slate-level problem under freshness, diversity, and integrity constraints. Let me start by pinning down the objective and the constraints, because that's where these systems actually fail."

That sentence buys you the rest of the interview. It signals you know the failure mode of feed ranking is *optimizing the wrong scalar*, not underfitting.

---

## 1. Clarify & scope

Ask these, and **state the assumption you'll proceed with** if the interviewer waves you on. Never stall waiting for answers.

### 1.1 Business objective → ML objective

| Question | Why it changes the design | Assumption I'll carry |
|---|---|---|
| What is the surface optimizing for? | FB main feed → time & meaningful interaction. LinkedIn → professional value & creator supply. A jobs-adjacent feed weights *conversion*, not dwell. | Primary home feed; north star = **28-day retained, engaged users (L28 / DAU-over-MAU)**, not session time. |
| Is the feed the only surface? | If notifications/email pull from the same candidate pool, the ranker's negative externalities leak. | Feed only; notifications is a sibling system reusing the same value model. |
| Ads interleaved? | Ad load interacts with organic quality; slot allocation becomes a joint auction problem. | Ads exist but slot allocation is fixed policy (1 ad per 5 organic); out of scope, flagged as an interaction. |
| Creator ecosystem goals? | If creator retention matters, you need supply-side guardrails (coverage, Gini) or the system collapses to head creators. | Yes — creator coverage is a **guardrail metric**, not a free variable. |

**The translation, stated explicitly:** we cannot train on 28-day retention (label latency is 28 days, credit assignment is impossible per item). So we optimize a **per-impression proxy value** `V(u, i, c)` and treat the proxy→retention relationship as a *hypothesis we periodically re-validate* with long-term holdouts. Saying that out loud — "the proxy is a modeling decision with an expiry date" — is the staff-level move.

### 1.2 Scale, latency, infra

State the numbers; they drive every architectural choice later.

- **Users:** 400M DAU, ~10 sessions/day → 4B sessions/day.
- **Requests:** ~2 feed fetches per session (initial + paginate) → **8B ranking requests/day ≈ 92k QPS average, ~280k QPS peak** (diurnal ×3).
- **Candidate pool:** 10^9 eligible items globally; per-user eligible pool after in-network + follow + group + out-of-network sourcing is 10^4–10^6.
- **Latency SLA:** p99 **300 ms** end-to-end for the feed API; **~120 ms** of that is the ranking service's budget (breakdown in §7).
- **Logged impressions:** ~60 viewport-qualified impressions/user/day → **24B labeled rows/day**.
- **Existing infra (assume, then confirm):** an online feature store with event-time correctness, a streaming aggregator (Flink/equivalent) for counters, GPU inference fleet with dynamic batching, an experimentation platform with cluster randomization support.

The interesting number: **92k QPS × 500 heavy-ranked candidates = ~46M item-scorings/second.** That single figure is why the architecture must be multi-stage and why distillation/quantization is a *product* decision, not an optimization afterthought. Put it on the board early.

---

## 2. Frame as ML

### 2.1 It is not one model

```
                    ┌─────────────────────────────────────────────────┐
   10^6 eligible    │ RETRIEVAL (multi-source, ~1000 candidates)       │
        ───────────▶│  in-network recency · two-tower ANN (out-of-net) │
                    │  followed creators · groups · trending · fresh   │
                    └────────────────────┬────────────────────────────┘
                                         ▼
                    ┌─────────────────────────────────────────────────┐
      ~1000         │ LIGHT RANKER (distilled, ~1ms/1000 items)       │
        ───────────▶│  cheap features, single tower, keeps top 500    │
                    └────────────────────┬────────────────────────────┘
                                         ▼
                    ┌─────────────────────────────────────────────────┐
      ~500          │ HEAVY RANKER (multi-task MMoE)                   │
        ───────────▶│  p(click), p(dwell), p(like), p(comment),       │
                    │  p(share), p(hide), p(report), p(follow)        │
                    └────────────────────┬────────────────────────────┘
                                         ▼
                    ┌─────────────────────────────────────────────────┐
      ~500          │ VALUE MODEL  V = Σ wₖ·pₖ − Σ vⱼ·qⱼ              │
                    │ then RE-RANK: freshness decay · diversity (MMR) │
        ───────────▶│  · integrity demotion/filter · slate constraints │
                    └────────────────────┬────────────────────────────┘
                                         ▼
                                    top 20 slots
```

Four decisions, four different techniques. Conflating them is the most common mid-level mistake: people put freshness and diversity *inside* the model as features and then can't explain why the feed is stale — because the model learned the *logging policy's* freshness distribution, not the product's freshness preference.

**Rule I state explicitly:** the model predicts *what the user will do*; the value model encodes *what we want*; the re-ranker enforces *what we must guarantee*. Separating prediction from policy is what makes the system auditable and lets a PM change a weight without a retrain.

### 2.2 Label, granularity, loss

**Training example = one viewport-qualified impression:** the tuple `(user, item, request_id, position, timestamp, device)` with the feature vector *as logged at serving time*.

**Why viewport-qualified matters:** if a user paginates away before item #43 renders, item #43 is neither a positive nor a negative — it is *unobserved*. Counting it as a negative teaches the model that deep-position content is bad, which is pure position bias baked into the label. Definition: ≥50% of pixels visible for ≥ 1 s (video: ≥ 1s of playback). This must be enforced by client telemetry, and the client must send *the whole rendered slate*, not just interactions — otherwise you have no negatives at all.

**Label heads** (multi-task; typical base rates, order-of-magnitude):

| Head | Type | Base rate | Notes |
|---|---|---|---|
| `click` | binary | ~4% | expand / open / tap-through |
| `long_dwell` | binary | ~8% | dwell > threshold, threshold **normalized by content length** (else long videos always win) |
| `like` / react | binary | ~1.2% | cheapest signal, most gameable |
| `comment` | binary | ~0.15% | high value, high latency (hours) |
| `share` | binary | ~0.05% | highest value, sparsest |
| `follow_creator` | binary | ~0.02% | supply-side signal |
| `hide` / `see fewer` / `report` | binary | ~0.05% | **negative heads** — trained, not filtered |
| `survey_worth_my_time` | binary | sampled | small human-labeled stream; the anchor for calibration of the value weights |

**Loss:**

```
L = Σₖ αₖ · BCE(pₖ, yₖ)  +  λ‖θ‖²        (multi-task, shared bottom → MMoE)
```

with `αₖ` set to roughly equalize *gradient contribution*, not to encode business value — business value lives in the value model's `wₖ`. Confusing task loss weights `αₖ` with value weights `wₖ` is a classic trap; if you fold value into the loss, you can never re-weight without retraining, and your probabilities stop being probabilities.

Details worth volunteering:
- **Dwell** as regression is badly behaved (heavy tail, censored by session end). Bucketize into ordinal classes and use ordinal/multi-class CE, or predict `log(1+dwell)` with a heteroscedastic head. Say why: a 400-second outlier shouldn't dominate the gradient.
- **Sparse heads (share, report)** get their own loss scaling plus negative downsampling with logit correction; otherwise they're drowned.
- **Position-bias tower:** a shallow tower taking `(position, device, slate_context)` added to the logit *at training time only*, zeroed at serving (YouTube's shallow-tower trick). This absorbs presentation bias so the main tower learns relevance. Alternative: IPS weighting with propensities from the exploration slice — mention both, pick the tower for stability, and note the tower assumes additive-in-logit bias.

---

## 3. Metrics

Three tiers. Interviewers specifically listen for whether you separate them.

### 3.1 Offline (model-quality gates — necessary, never sufficient)

- **Per-head AUC / PR-AUC.** PR-AUC for the sparse heads; AUC is nearly uninformative at 0.05% base rate.
- **NDCG@10 / MAP@10 at the *slate* level**, grouped by request_id, with gain = realized value (not click) — this is the metric closest to what ships.
- **Calibration is a first-class gate, not a diagnostic.** The value model adds probabilities across heads; if `p(comment)` is 2× over-predicted, the weight `w_comment` silently doubles. Track **ECE**, and **calibration ratio (Σpredicted / Σactual) per decile and per slice** — new users, each locale, each content type, each surface. Fix with per-head isotonic or Platt calibration fitted on held-out recent data, refit on every retrain.
- **Counterfactual value estimate** — SNIPS / doubly-robust replay on the randomized-exploration slice (§8.1). This is the only offline number that estimates *the thing you ship*.

### 3.2 Online (decision metrics)

- **Primary:** 28-day retention / DAU-over-MAU, sessions per user per week. Underpowered in a 2-week test — hence the surrogate framework below.
- **Surrogates (powered, validated against the primary quarterly):** meaningful-interaction rate (comment + share + reply-thread depth), out-of-network engaged reach, "worth your time" survey score, day-2 return rate.
- **Revenue-adjacent:** ad impression opportunity, revenue per session (should be *neutral*; if organic ranking moves revenue a lot, someone is trading user value for ad load).

### 3.3 Guardrails (any of these breaks → no ship, automated rollback)

| Guardrail | Why |
|---|---|
| Integrity prevalence (views of borderline/violating content per 10k views) | The engagement–integrity tension is real and directional: engagement-maximizing rankers reliably increase borderline content. |
| Hide / report / "see fewer" rate | The user's own veto, and the earliest signal of a bad objective. |
| Creator coverage (% creators with ≥1 impression/week) and Gini of impressions | Supply collapse is slow, invisible in viewer metrics, and existential. |
| % out-of-network, topic entropy per session | Filter-bubble / homogenization proxy. |
| p99 latency, feed-fallback rate | A quality win that adds 80 ms is a loss. |
| New-user (< 7 days) engagement | Rankers optimized on the aggregate almost always regress cold-start users. |

**The line to say:** *"CTR is a guardrail, not a goal. A ranker that maximizes CTR has a known closed-form solution: clickbait."*

---

## 4. Data

### 4.1 Sources

Impression/interaction logs (client telemetry with viewport qualification) · content store (text, media, creator, taxonomy) · social graph (edges, tie strength, mutuals) · streaming counters (engagement velocity per item, per creator) · integrity classifier scores · human survey panel · experiment assignment logs (needed to *exclude or weight* traffic from training).

### 4.2 Feature logging beats feature recomputation

**The single most important data decision:** at serving time, log the exact feature vector that was scored, alongside the request. Do **not** reconstruct features offline from warehouse snapshots.

Why this is a staff-level answer:
1. It eliminates train/serve skew by construction — the same bytes that scored also train.
2. It makes point-in-time correctness free. Reconstructing "the creator's follower count as of 3 weeks ago Tuesday 14:03" from daily snapshots is where leakage actually enters production systems.
3. It costs storage (24B rows × ~2 KB ≈ 48 TB/day raw) — so log a sampled/compressed vector: all positives + downsampled negatives, sparse-ID encoded, with a schema registry. Quantify the tradeoff out loud; that's the part interviewers grade.

**Leakage checklist to recite:** never join *final* engagement counts of a post (that's the future); use `counts_as_of(impression_time)` from the streaming store. Never use a feature whose backfill differs from its online computation. Never train on the item's own label-derived aggregates.

### 4.3 Sampling

- Keep **100% of positives**, downsample negatives ~10:1, apply the logit correction `logit_corrected = logit − ln(r)` so calibration survives. State the correction — most candidates forget it and then wonder why the value blend is off.
- **Per-user capping** (e.g. ≤ 500 rows/user/day): power users are ~1% of users and can be 20% of rows; without a cap the model becomes a power-user model and cold-start regresses.
- **Window:** rolling 14–21 days for the full retrain; the model is recency-sensitive (content distribution shifts weekly), but too short a window destroys tail-creator and tail-topic coverage.

### 4.4 Delayed feedback

Comments and shares arrive minutes-to-hours after impression; a naive hourly-fresh training pipeline labels them negative. Options, and my pick:
- **Attribution window** (24 h) before a row is "mature" — simple, correct, costs 24 h of freshness. Use for the daily/weekly full retrain.
- **Delayed-feedback modeling** for the incremental hourly updates: train on immature labels with an importance weight / elapsed-time feature that models `P(conversion | not yet converted, elapsed)`, or use positive-unlabeled correction.
- Practical answer: **both** — mature labels for the base model, delayed-feedback-corrected fresh data for the hourly warm-start. Say which heads need it (comment/share yes, click no).

### 4.5 Imbalance and the exploration slice

Reserve **1–2% of traffic for randomization**: either ε-greedy insertion of random eligible candidates, or randomized permutation within the top-k slate. This is not a nicety — it is the *only* source of unbiased data for (a) counterfactual offline evaluation, (b) propensity estimation, (c) measuring position bias, and (d) breaking the rich-get-richer loop. Budget it as a permanent tax and defend it: without it, every offline number you produce is measured on the distribution your own model created.

---

## 5. Features

| Family | Examples | Notes |
|---|---|---|
| **User** | long-term interest embedding; **behavior sequence** (last 100 interactions as item embeddings + time deltas + action types); locale/language; tenure; device; historical action base rates (per-user calibration priors) | The sequence is the single highest-value feature family; feed it to a DIN/transformer block attending over the candidate. |
| **Item** | creator ID embedding; content type; multimodal content embedding (text + image + video, from a frozen upstream encoder); topic taxonomy; **age in minutes**; early engagement velocity *as of now*; language | Content embeddings solve cold start: a 30-second-old post has no counters but has content. |
| **Context** | time of day / day of week; session depth (position in session); request index (page 1 vs page 4); network quality; entry point (push vs organic open) | Session depth matters: page-4 intent differs from page-1 intent. |
| **User × Item (cross)** | tie strength with creator (past interactions, mutuals, message-adjacency proxy); user-topic affinity; historical engagement rate with this creator (**Bayesian-smoothed**, `(x+αp̄)/(n+α)`); "already seen" recency; embedding dot-products | Cross features are where the lift is. Raw counts on 3 impressions are noise — smooth them. |
| **Slate/context of slate** | what else is on the page, position, adjacent items | Only used by the re-ranker, not the pointwise scorer. |

**Embeddings:** ID embeddings with frequency-based hashing (full table for head entities, hashed buckets for tail — controls a 10^9-entity table); shared item-embedding space between retrieval and ranking so the two-tower and heavy ranker agree on what an item is; periodic re-training of the embedding tables is a *joint* decision with the ANN index rebuild (§7).

**Point-in-time correctness:** already covered by feature logging (§4.2). If the interviewer pushes: describe the offline path for *new* features — you cannot log a feature that didn't exist, so a new feature needs either a backfill with an event-time-correct pipeline (auditable, expensive) or a 2-week "log-and-wait" period before it can be trained on. Log-and-wait is the safe default and worth the delay. That tradeoff, stated unprompted, reads as someone who has been burned.

---

## 6. Model

### 6.1 Sequence the work like an engineer, not a paper

1. **Baseline (week 1–2):** per-head GBDT (or LR with crosses) on counting features. Ship the **value model blend** on top of it. Most of the first-year win comes from *changing the objective from CTR to a blended value*, not from architecture. Establish it as the champion and the offline gate.
2. **V1 (quarter 1):** multi-task DNN — shared bottom → **MMoE** (multi-gate mixture-of-experts) with per-head towers + the position-bias shallow tower. Justification for going deeper is concrete: shared representation across 8 heads fixes the sparse heads (comment/share) that GBDT can't learn from 0.05% positives; and MMoE's per-task gates handle the fact that `p(share)` and `p(hide)` are *negatively* correlated tasks that a shared-bottom net forces into one representation.
3. **V2:** user-behavior sequence modeling (DIN-style target attention, then a small transformer); multimodal content embeddings; creator-quality tower.
4. **V3:** distillation of the heavy ranker into the light ranker (train the light model on heavy-model scores over the *retrieved* distribution — this is what makes the funnel consistent), quantization (int8), early-exit.

Interviewers reward the phrase "**I'd justify each step by the metric it's supposed to move and the cost it adds**," followed by actually doing it.

### 6.2 Retrieval (why two-stage at all)

10^6 eligible → 1000 candidates must happen in ~30 ms. Multiple sources, each with its own quota (quotas are a product lever):
- **In-network recent** (friends/connections, last 72 h) — inverted index by author, merged by recency.
- **Out-of-network two-tower ANN** — user tower (from profile + sequence) queries an HNSW/IVF-PQ index of item embeddings. Trained with in-batch sampled softmax + **log-Q correction** (popularity correction on the sampled negatives) — mention this; it's the detail that separates people who've built retrieval from people who've read about it.
- **Followed creators / groups / topics** — deterministic pulls.
- **Trending / fresh** — a small quota reserved for items < 60 min old, which the ANN index may not have caught up on yet.

Union → dedupe → seen-filter (per-user Bloom filter of impressed IDs, ~1% FPR, cheap) → light ranker.

**Target:** retrieval recall@1000 ≥ 0.95 against a brute-force scorer on a sampled offline set. If retrieval drops the item, no ranker can save it — and retrieval regressions are invisible in ranking metrics. Monitor it separately.

### 6.3 The value model (the heart of this question)

```
V(u, i, c) =  Σₖ wₖ · pₖ(u, i, c)        # positive heads: click, dwell, like, comment, share, follow
            − Σⱼ vⱼ · qⱼ(u, i, c)        # negative heads: hide, "see fewer", report
```

then a policy layer applied multiplicatively:

```
Score = V · f_freshness(age) · f_integrity(item) · f_creator_quality(creator)
```

Illustrative weights, normalized to `w_click = 1`: `w_dwell ≈ 2`, `w_like ≈ 3`, `w_comment ≈ 25`, `w_share ≈ 40`, `w_follow ≈ 30`, `v_hide ≈ 100`, `v_report ≈ 500`. The shape matters more than the numbers: **rare, effortful, and reciprocal actions carry the most weight; the negative heads are weighted an order of magnitude above the positives**, because one hide should out-vote several likes.

Three things to say about the weights, because this is where staff candidates separate:

1. **Calibration is a hard prerequisite.** Summing `wₖ·pₖ` is only meaningful if each `pₖ` is a true probability. Otherwise the weights encode miscalibration, and the whole scheme is uninterpretable. This is why §3.1 makes calibration a gate.
2. **Where the weights come from.** Not vibes: (a) initialize from survey-anchored regression — regress "was this worth your time?" and 28-day retention onto realized actions to get relative value; (b) refine with periodic weight-sweep A/B tests or Bayesian optimization on the surrogate metric, using a small number of arms; (c) re-derive quarterly, because user behavior and the content mix drift.
3. **Weights are governed, versioned, and auditable.** They are the product's ethics expressed in numbers. Store them in config with review, not in model code — a weight change is a product decision that must be reviewable by policy and legal, and reversible in seconds without a retrain. Volunteer this; it is exactly the "staff" signal.

### 6.4 Freshness, diversity, integrity — the policy layer

- **Freshness:** multiplicative decay `f = exp(−age / τ)` with per-content-type `τ` (news τ ≈ 4 h, evergreen professional content τ ≈ 72 h). Keep it *outside* the model so it's tunable per surface and doesn't get relearned from logging-policy artifacts. Plus a hard fresh-content quota from retrieval so new posts get their first impressions (cold start needs exposure, not just scoring).
- **Diversity:** slate-level, greedy MMR or a DPP over item embeddings — select slot by slot maximizing `Score(i) − γ·max_similarity(i, already_selected)`. Plus hard slate constraints: ≤ 2–3 items per creator, ≤ N per topic, ≥ X% out-of-network, ≥ 1 fresh item in the top 5. Frame it honestly as **constrained optimization**: maximize `Σ V` subject to slate constraints, solved greedily per slot on marginal value, because exact solving is not happening in 10 ms.
- **Integrity:** tiered, and *not* a single knob.
  - **Tier 1 — hard filter:** violating content. Removed pre-ranking. Never a score adjustment; a demotion is a probability of showing violating content, and that probability should be zero.
  - **Tier 2 — demotion:** borderline/"near-violating", low-quality clickbait, engagement bait, unoriginal/repost content, misinformation flagged but unadjudicated. Multiplicative demotion `f_integrity ∈ (0, 1)`.
  - **Tier 3 — the negative heads** (`p(report)`, `p(hide)`) inside `V` catch the personalized long tail no classifier enumerates.
  This tiering matters because "just add integrity as a feature" fails an audit: you cannot show a regulator *why* an item was down-ranked if it's diffused through a DNN. Keeping demotions as an explicit multiplicative factor makes every ranking decision decomposable and explainable.

**The honest tension to name:** integrity demotions cost engagement in the short run. That is expected and acceptable, and it is precisely why integrity prevalence is a *guardrail with rollback authority* rather than a term the ranker can trade away. Anyone who claims there's no tradeoff hasn't shipped one.

---

## 7. Serving

### 7.1 Latency budget (p99, 300 ms end-to-end)

| Stage | Budget | Notes |
|---|---|---|
| Gateway, auth, session context | 10 ms | |
| Retrieval fan-out (parallel sources + ANN) | 30 ms | Sources run concurrently; slowest source is hedged, not awaited past deadline. |
| Seen-filter + dedupe | 5 ms | Bloom filter in the user's session blob. |
| Feature fetch (online store, batched) | 35 ms | One multi-get for ~500 items × item features + 1 user fetch. **This, not inference, is usually the p99 driver.** |
| Light ranker | 10 ms | CPU, 1000 items. |
| Heavy ranker | 55 ms | GPU, dynamic batching, ~500 items in 1–3 batches. |
| Value blend + re-rank (MMR, constraints) | 10 ms | Pure CPU, embeddings already in memory. |
| Hydration (content, media URLs, ads interleave) | 40 ms | |
| Serialization + slack | ~60 ms | Slack is not optional at p99. |

Note the shape: **feature fetch + hydration ≈ inference.** Candidates who only budget for the model are revealing they haven't run one of these in production.

### 7.2 Precompute vs. real time

- **Precomputed (async, at publish):** item content embeddings, integrity classifier scores, taxonomy. Pipeline SLA **< 60 s from publish** — this is a *ranking-quality* requirement, because a fresh post that misses embedding generation is invisible during exactly the window it matters.
- **Near-real-time (streaming, seconds):** engagement counters, creator velocity, "seen" sets, trending signals.
- **Per-session:** user embedding computed at session start from the behavior sequence, cached for the session, incrementally updated on in-session actions (in-session adaptation is a real win: what you engaged with 30 s ago is the strongest predictor of the next 30 s).
- **Per-request:** the heavy ranker. Nothing about the *pairing* can be precomputed at 10^6 candidates × 400M users.

### 7.3 Caching, ANN, and the fallback ladder

- **Feed cache:** pre-generate the next page's ranked IDs at page-1 serve time; short TTL (minutes) because freshness decays; always re-apply the seen-filter and integrity filter at read time (**never serve a cached item that has since been actioned by integrity** — cache the ranking, re-check the policy).
- **ANN index:** HNSW for recall, IVF-PQ if memory-bound; sharded by partition; **incremental insertion for fresh content** plus a full rebuild on embedding-table retrain. The index and the model are *versioned together* — a ranker trained against embedding table v7 querying an index built from v6 is a silent, brutal quality regression. Enforce with a version handshake at load.
- **Degradation ladder (always return a feed):** heavy ranker timeout → serve light-ranker order; light ranker down → serve retrieval order with freshness decay; everything down → chronological in-network. Track `feed_fallback_rate` as a guardrail; a "latency win" that quietly triples fallbacks is a quality loss disguised as a perf win.
- **Cost:** 46M item-scorings/s dominates the budget. Levers in order of ROI: distillation to a smaller heavy ranker, int8 quantization, candidate-count tuning (the value curve from 500 → 300 candidates is usually flat — measure it), early exit for low-light-ranker-score items, and embedding-table sharding to fit in HBM.

---

## 8. Evaluation & rollout

### 8.1 Offline replay → shadow → interleaving → A/B → ramp

1. **Offline replay** on the randomized-exploration slice using SNIPS / doubly-robust estimators. Say the limitation plainly: replay is only valid where the new policy's support overlaps the logging policy's. For a ranker that reorders aggressively, variance explodes — clip the weights, report the effective sample size, and treat the estimate as a *screen*, not a verdict.
2. **Shadow:** run the challenger on live traffic, serve the champion. Compare score distributions, per-slice calibration, feature coverage/null rates, p99 latency, and *slate composition deltas* (freshness mix, out-of-network %, creator concentration). Catches the majority of would-be incidents at zero user risk. Ship-blocking findings here are cheap; the same findings in a 5% ramp are not.
3. **Interleaving** (team-draft) for a fast, high-sensitivity relevance read at ~1% traffic and ~1/10 the sample size of an A/B. **Caveat to state:** interleaving is valid for *relevance ordering* changes but **invalid for value-weight or composition changes** — it can't measure effects that depend on the whole slate's composition (diversity, ad load, session-level effects). Knowing when interleaving is *not* valid is the differentiator.
4. **A/B:** 1% → 5% → 20% → 50%, 2–3 weeks. Power analysis up front: retention effects are ~0.1–0.5% relative; with 400M DAU a 1% cell has plenty of users, but the *variance* of session-level metrics and the *duration* needed for retention are the binding constraints. Use surrogate metrics for the ship decision, with the primary as a directional check.
5. **Network interference is real here** and worth raising unprompted: a ranking change that increases sharing changes *other users'* feeds, contaminating control. Mitigation: **cluster/ego-network randomization** (randomize connected components or communities) for changes expected to move sharing; accept the power loss. For pure relevance-ordering changes, user-level randomization is fine. Naming interference correctly is a strong staff signal in social-feed interviews.
6. **Long-term holdout:** a permanent ~0.5–1% cell on a frozen or lagged model. This is the only instrument that measures *cumulative* effects — feedback loops, ecosystem shifts, and the slow decay of the proxy→retention relationship. Budget it as a permanent cost and defend it in the review; short A/Bs systematically miss the effects that matter most in feed ranking.

### 8.2 Automated ship gates

Rollback triggers wired into the ramp, no human in the loop: integrity prevalence up > X%, hide/report rate up > Y%, p99 latency up > 20 ms, fallback rate up, new-user engagement down, creator coverage down. Gates are pre-registered *before* the experiment — otherwise a guardrail regression becomes a negotiation.

---

## 9. Monitoring & iteration

**Data & feature health:** per-feature null rate and PSI/KL drift vs. the training window; score-distribution drift; **training/serving skew canary** — replay a sample of production requests through the offline pipeline and assert score parity within ε (this catches the class of bug that no unit test finds).

**Model health:** per-slice calibration (new users, each locale, each content type, low-activity users) — aggregate calibration hides catastrophic slice miscalibration; embedding staleness; ANN recall@k against periodic brute force; per-head AUC on the rolling fresh window.

**Feedback loops — the ones I'd actively instrument:**
- **Rich-get-richer:** the model trains on impressions it chose. Mitigate with the exploration budget, IPS training weights, and the creator-coverage guardrail.
- **Engagement-bait ratchet:** value weights that reward comments incentivize "comment below!" content. Counter with an engagement-bait classifier feeding the Tier-2 demotion, and by tracking the *composition* of comments (reply depth, not just count).
- **Position bias re-measurement:** re-estimate quarterly from the randomization slice; the shallow tower's assumption goes stale as the UI changes. Every UI redesign invalidates the current bias model — tie a re-measurement to the launch.
- **Filter bubble:** track per-user topic entropy over time, not just cross-sectionally.

**Retraining:** hourly incremental warm-start on fresh (delayed-feedback-corrected) data; full retrain weekly on the 14–21 day window; embedding tables + ANN index rebuilt together on the same cadence. **Triggers for off-cycle retrain:** calibration alarm, drift threshold, a major UI or product launch, a content-mix shock (elections, major news events). Champion/challenger runs continuously with automatic rollback if the challenger's live calibration or guardrails degrade.

**Cold start:**
- *New user:* onboarding interest selection → locale-popularity prior → elevated exploration for the first ~week; a dedicated new-user calibration slice; measure separately, because the aggregate metric will happily hide a new-user regression.
- *New content:* content-embedding-based scoring (no counters needed) + a reserved fresh quota + Thompson sampling over a creator-level prior, so a new post inherits an informed prior instead of starting from zero.

---

## 10. Deep-dive ammo (where interviewers actually push)

| They ask | The crisp answer |
|---|---|
| "Why not one model predicting value directly?" | Value is not observable — only actions are. A direct value head would need a value label, which is exactly the survey stream: too small and too slow to train on alone. Multi-task keeps each head learnable from abundant labels and keeps the weights *editable without a retrain*. |
| "Why is calibration so important here?" | Because we *add* probabilities across heads. Ranking-only systems can tolerate monotone distortion; a value blend cannot — miscalibration silently reweights the objective. |
| "How do you set the value weights?" | Survey/retention-anchored regression for the initialization, weight-sweep A/Bs for refinement, quarterly re-derivation, versioned config with product+policy review. They're a governed product artifact, not a hyperparameter. |
| "Engagement vs. integrity tradeoff?" | Real and directional. Handle it with tiering (filter / demote / personalized negative heads) and by giving integrity guardrails rollback authority over engagement wins. Don't pretend it's free. |
| "How do you handle position bias?" | Shallow position tower at train time, zeroed at serve; validated against IPS estimates from the randomization slice; re-measured after every UI change. |
| "Your A/B is contaminated by the social graph — what now?" | Ego-network / community cluster randomization for share-affecting changes, accept the variance hit, and check the SUTVA assumption per change type rather than assuming it. |
| "Latency budget blew up — what do you cut?" | Candidate count first (measure the value curve — usually flat below 500), then distill/quantize the heavy ranker, then trim feature fetch (the actual p99 driver), and never the fallback ladder. |
| "How do you know the proxy still predicts retention?" | The long-term holdout, plus a quarterly re-fit of the surrogate→primary relationship. If the correlation decays, the value weights are stale — that's a scheduled review, not an incident. |

---

## 11. Timeboxing a 45-minute round

| Minutes | Do this |
|---|---|
| 0–5 | Clarify objective, scale, latency, constraints. Write the assumptions on the board and move on. |
| 5–8 | The opening frame (§0) + the 4-box architecture diagram. |
| 8–14 | ML framing: example granularity, label heads, viewport qualification, loss. |
| 14–20 | Metrics: the three tiers, with calibration and guardrails called out. |
| 20–26 | Data & features: feature logging, leakage, delayed feedback, exploration slice. |
| 26–34 | Model: baseline → MMoE, retrieval, and **the value model + policy layer** (spend the most time here — it's the actual question). |
| 34–39 | Serving: latency table, precompute vs. real-time, fallback ladder. |
| 39–45 | Rollout + monitoring + feedback loops. Close with the top-2 risks and what you'd build first. |

**Close with:** *"If I had one quarter: ship the multi-task model with the value blend and the integrity tiering on top of the existing retrieval, with the exploration slice and long-term holdout in place from day one — because the instrumentation is what lets everything after that be measured."*

---

## 12. Signals that separate levels

**Senior sounds like:** two-stage architecture, MMoE, AUC/NDCG, A/B test, feature store.

**Staff sounds like:**
- Owning the objective: proxy value with an expiry date, weights as governed product config, CTR as a guardrail not a goal.
- Naming the loop: rich-get-richer, engagement bait, filter bubble — and the *instrumentation* (exploration slice, long-term holdout, creator coverage) that makes them observable rather than theoretical.
- Correctness by construction: feature logging over recomputation, log-and-wait for new features, model/index version handshake, training/serving skew canary.
- Knowing what each eval *can't* do: replay needs support overlap; interleaving can't measure composition; user-level randomization breaks under network interference.
- Organizational design: pre-registered rollback gates, weights reviewable by policy, integrity holding veto authority over engagement.
- Honest tradeoffs with numbers attached — 46M scorings/sec, 48 TB/day of logs, 1% exploration tax, 60 ms of p99 slack — instead of a clean story with no costs in it.
