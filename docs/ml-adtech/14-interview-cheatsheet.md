# 14 — Interview Cheat Sheet

The whiteboard version. Everything here is defended in depth elsewhere in this doc set; this is the
sequencing and the one-liners.

## 14.1 The first 5 minutes: clarify before drawing

Do not start drawing boxes. Ask:

1. **Owned inventory or an exchange?** (Changes everything: OpenRTB timeouts, bid requests *out*
   vs. ad requests *in*.)
2. **How many active ads/creatives?** → 1M fits in-process; 100M does not. *This is the fork in the
   architecture.*
3. **What's the objective?** Revenue, advertiser ROAS, or user experience — and who arbitrates when
   they conflict?
4. **Is the 45 ms end-to-end or server-side?** Client RTT is a third of the budget.
5. **Consent/regulatory regime?** EU changes the feature set available.
6. **Pricing models supported?** CPM only is a much simpler system than CPA.

Then state the numbers back: *128M/day → 1,481 RPS avg → ~2,370 peak → design each region for
3,100 with 4,000 burst, because active-active means each region must survive alone.*

## 14.2 The 10-minute drawing order

Draw in this order; it tells a story rather than producing a diagram.

1. **The funnel first** — 100k → 5k → 500 → 20 → 1, with a latency number on each arrow. This
   frames every subsequent question.
2. **The request path** — client → LB → ad-server, and inside it the four stages.
3. **The one remote call** — user features. Explicitly say: *ad features are in-process, because
   500 candidates × 2,370 RPS would be 1.19M remote reads/second.*
4. **The async side** — Kafka out of the ad server (fire-and-forget), Flink to features, lake to
   training, registry back to serving. Emphasize: **no synchronous dependency from serving into the
   control or data plane.**
5. **The second region** — and immediately raise budgets as the hard state problem, with leases as
   the answer.
6. **The degradation ladder** — end on this. It is the answer to half of the follow-up questions.

## 14.3 The seven answers to have ready

| Question | Answer in one breath |
|---|---|
| "How do you hit 45 ms p99?" | Batch all candidates into one in-process INT8 inference; keep ad-side state local so there's exactly one remote read; overlap that read with local filtering; run pods at ≤50% utilization because queueing punishes the tail; hedge slow reads; degrade rather than queue. |
| "Why not Triton/GPU?" | The batch is already 500 — dynamic batching's benefit doesn't apply, but its queue delay does. In-process ORT removes a hop and a queueing system. I'd move to GPU when FLOPs/candidate grow 10× or candidates exceed ~2,000, via the same ONNX graph with shared-memory tensors. |
| "How do you not overspend the budget across regions?" | Lease-based sub-allocation from a global allocator; leases shrink as the budget depletes so exposure is smallest exactly when it matters; the impression ledger is the truth and reconciles counters every 5 minutes; fail closed on partition — under-deliver, never double-spend. |
| "How do you avoid training/serving skew?" | Log the exact feature vector used, and train on that rather than recomputing. One feature definition executed by both Flink and Spark. Preprocessing compiled into the ONNX graph. A daily job replays logged requests through the offline pipeline and diffs. |
| "The model's offline AUC improved but revenue dropped — why?" | Calibration, most likely: ordering held, probabilities shifted, so eCPM mis-priced. Then: position/selection bias making offline eval optimistic; a slice regression hidden by the aggregate; or degraded-traffic rows polluting training. Check calibration ratio per placement first. |
| "How do you handle delayed conversions?" | Flink keyed-state joins with windows sized from the empirical delay distribution; a delayed-feedback model that treats recent non-conversions as censored rather than negative; fake-negative-with-correction for the incremental path; nightly repair of late arrivals via Iceberg partition rewrites. |
| "What happens when the feature store dies?" | Nothing fatal. Hedge → 5 ms timeout → in-process LRU → context-only embedding. Latency actually improves; RPM drops 15–25%; the degradation level is tagged on the response and the log row so the cost is measurable and those rows are excluded from training. |

## 14.4 Numbers to have memorized

```
128M/day = 1,481 RPS avg;  ×1.6 diurnal ≈ 2,370 peak;  per-region design 3,100
500 candidates × 2,370 RPS  = 1.19M scores/s      ← the number that drives the design
Ranker: ~212 kFLOP/candidate → ~251 GFLOP/s/region → ~5 cores of pure math, ~40 with overhead
Serving tier: 24 pods × 4 vCPU × 3 GB per region
Ad-side state in RAM: 400 MB features + 250 MB HNSW + 64 MB embeddings ≈ 1.2 GB/pod
Log volume: ~1.5 KB/request → ~300 GB/day → ~120 TB/year
Online store: 10M users × 2 KB = 20 GB — fits in RAM, so p99 < 3 ms is achievable
Latency: p50 ~14 ms, p99 ~33 ms server-side, 45 ms SLO
Network hop cost: 1–3 ms p99 each — this is why the funnel is one process
```

## 14.5 Trade-offs to volunteer (don't wait to be asked)

Volunteering the cost of your own choices is the strongest signal available in a design interview.

- **In-process inference** costs independent model scaling and couples model deploys to server
  deploys. Mitigated by shipping models as versioned bundles pulled at runtime, so a model rollout
  isn't a binary rollout.
- **Active-active** costs ~2× serving spend for sub-90-second failover. Justify it with revenue per
  minute of downtime, or don't do it.
- **1M-ad in-process assumption** is the design's load-bearing wall. At 100M ads, retrieval becomes
  a sharded remote service, latency budget gets re-cut, and the whole shape changes. Say this before
  the interviewer finds it.
- **Second-price auctions** are increasingly non-standard for exchange demand; keeping the pricing
  rule per-placement and configurable is the hedge.
- **Probabilistic pacing** drops requests where bid shading would smooth them; simpler to explain
  and to debug, at some revenue cost. Hybrid is the evolution.
- **Logging full feature vectors** costs ~130 GB/day. Bought deliberately, to eliminate the skew
  class of bug.
- **Eventual consistency on frequency caps** means ~1% over-delivery across regions. Measured, not
  hidden.

## 14.6 Common traps

| Trap | Correct move |
|---|---|
| Designing to "inference < 100 ms" | Inference is one of nine stages; budget the whole path, and say so |
| Fetching features for all 500 candidates remotely | Ad-side state is per-pod; the remote read is user-side only, once |
| Caching the final ad decision | Breaks budgets and frequency caps; cache inputs and intermediates instead |
| Forgetting the negative-downsampling calibration correction | Every price becomes wrong; ordering looks fine |
| Ignoring position/selection bias | The model learns to imitate the previous model |
| User-split A/B for auction changes | Marketplace interference violates SUTVA; use budget-split or switchback |
| Averages instead of histograms | p99 is the SLO; an average can't see it |
| Unbounded metric label cardinality (`campaign_id`) | Prometheus dies; per-campaign goes to the analytics store |
| Treating no-consent traffic as an error path | In the EU it's a large share of traffic; the contextual path is first-class |
| "We'll add monitoring later" | Calibration and degradation-mix monitoring are the two that catch real incidents; design them in |

## 14.7 If asked to go deeper on one thing

Have one prepared depth-dive per area. Suggested picks (each already written up):

- **ML depth** → two-tower training with in-batch negatives + LogQ correction, and why hard
  negatives come from the same request ([03](./03-candidate-generation-and-ranking.md)).
- **Systems depth** → the tail-latency table: arenas, `GOMAXPROCS` from cgroups,
  `intra_op_num_threads=1`, hedged reads, shed-not-queue ([05](./05-low-latency-serving.md)).
- **Distributed-systems depth** → lease-based budgets and the split-brain rule
  ([06](./06-auction-pacing-budgets.md), [11](./11-reliability-active-active.md)).
- **Data depth** → point-in-time correctness and delayed feedback
  ([04](./04-feature-store.md), [09](./09-data-and-training-pipeline.md)).
- **Product/business depth** → the diagnostics funnel screen and why rejection reason codes are
  worth building ([08](./08-frontend.md)).

---
Back to the [index](./README.md).
