# Real-Time Ad Ranking & Serving Platform — System Design

Design for an ad-tech / recommendation serving platform at the scale named in the role:
**~128M ad requests/day, 2,000+ RPS peak, 25–45 ms end-to-end p99**, deployed **active-active**
across two EU regions.

This is a *design-first* deliverable: architecture, contracts, capacity math, and the trade-offs
behind each choice. Nothing here is hand-wavy — every component has a latency budget, a failure
mode, and a fallback. Implementation is a follow-up; §[15](#build-order) sketches the build order.

---

## Documents

| # | Doc | What it answers |
|---|-----|-----------------|
| 01 | [Requirements & capacity](./01-requirements-and-capacity.md) | SLOs, traffic math, machine count, cost envelope |
| 02 | [High-level architecture](./02-architecture-hld.md) | The boxes, the arrows, the request lifecycle |
| 03 | [Candidate generation & ranking](./03-candidate-generation-and-ranking.md) | Retrieval → ranking funnel, model choices, calibration |
| 04 | [Feature store](./04-feature-store.md) | Online/offline stores, point-in-time correctness, skew |
| 05 | [Low-latency serving](./05-low-latency-serving.md) | ONNX/Triton, quantization, batching, caching, GC/tail control |
| 06 | [Auction, pacing & budgets](./06-auction-pacing-budgets.md) | eCPM, GSP vs first-price, PID pacing, distributed budget |
| 07 | [Backend LLD](./07-backend-lld.md) | Services, gRPC/HTTP contracts, data structures, concurrency |
| 08 | [Frontend](./08-frontend.md) | Ad SDK (viewability, beacons) + React ops console |
| 09 | [Data & training pipeline](./09-data-and-training-pipeline.md) | Event log → labels → training, delayed conversions |
| 10 | [MLOps & CI/CD](./10-mlops-cicd.md) | Repo layout, pipelines, registry, shadow/canary/rollback |
| 11 | [Reliability & active-active](./11-reliability-active-active.md) | Failover, state, split-brain, degradation ladder |
| 12 | [Observability & experimentation](./12-observability-and-experimentation.md) | Metrics, tracing, drift, A/B + interleaving |
| 13 | [Privacy & compliance](./13-privacy-and-compliance.md) | GDPR/DSA posture for a regulated (bank) context |
| 14 | [Interview cheat sheet](./14-interview-cheatsheet.md) | The 10-minute whiteboard script + likely follow-ups |

---

## TL;DR — the design in one page

**The funnel.** Every request walks the same four stages, each cutting the candidate set by ~10×:

```
100k eligible ads
   │  targeting + eligibility filters (bitmap index, in-process)   ~2 ms
   ▼
~5k
   │  retrieval: two-tower ANN (HNSW) + rule-based sources         ~4 ms
   ▼
~500
   │  ranking: multi-task DNN → pCTR, pCVR, calibrated             ~10 ms
   ▼
~20
   │  auction: eCPM sort, pacing, budget, frequency, policy        ~3 ms
   ▼
1 winner
```

**The three ideas that make 45 ms p99 achievable:**

1. **Move ad-side state into the process.** ~1M active ads is only a few hundred MB. The
   eligibility bitmaps, ad features, and ANN index are *local* to every serving pod, refreshed
   from a change stream every 30–60 s. Zero network hops for the 500-candidate side of the
   request. The only remote read is the user's feature vector — **one** multi-get, not 500.
2. **One batched inference call, not N.** All ~500 candidates are scored as a single
   `[500, D]` tensor in one ONNX Runtime call, in-process, INT8-quantized. No per-candidate
   RPC, no cross-process hop, no dynamic-batching queue wait on the critical path.
3. **A degradation ladder, not a binary up/down.** Feature store slow → serve with cached/default
   user features. Model slow → serve the previous model. Ranker down → eCPM-sorted popularity.
   Everything down → house ads. An ad request never returns an error; it returns *something*
   with a quality tag, and the tag is a first-class metric.

**Latency budget (p99, 45 ms SLO, ~5 ms unallocated slack):**

| Stage | p50 | p99 | Notes |
|---|---|---|---|
| Edge TLS + LB + ingress | 1 | 2 | Terminate at the region edge, keep-alive to pods |
| Parse, validate, consent check | 0.3 | 1 | Protobuf, no JSON on the hot path |
| User features (remote multi-get) | 1.5 | 5 | Aerospike/Redis, hedged at p95, in parallel with ① |
| ① Eligibility filter (in-process bitmaps) | 0.8 | 2 | Roaring bitmap intersection |
| ② Retrieval / ANN | 2 | 4 | HNSW, `ef=64`, in-process |
| ③ Feature assembly (500 × D) | 1.5 | 4 | Pre-laid-out arena, zero-copy |
| ④ Ranking inference | 5 | 10 | ONNX Runtime, INT8, batch=500 |
| ⑤ Auction + pacing + budget | 1 | 3 | Local token buckets, async global reconcile |
| Response build + async log enqueue | 0.5 | 2 | Logging never blocks the response |
| Network back to client (intra-EU) | 3 | 7 | Outside our control; measured separately |
| **Total (server-side, ⑦ excluded)** | **~14** | **~33** | SLO 45 ms with ~12 ms headroom |

**Serving stack:** Go ad-server (predictable GC, cheap goroutines) with ONNX Runtime linked
in-process for ranking; Triton on GPU only if/when the ranker outgrows CPU (see
[05](./05-low-latency-serving.md#when-to-move-to-gpu)). Kafka → Flink for streaming features,
Iceberg-on-S3 for the offline lake, Aerospike for the online store, Kubernetes + Argo Rollouts
for delivery.

**Active-active:** both regions serve live traffic behind latency-based DNS/anycast. Each is
provisioned to absorb 100% of peak alone. State is partitioned by *tolerance*: budgets get
lease-based sub-allocation (hard constraint, must not overspend), frequency caps replicate
asynchronously (soft constraint, ~1% over-delivery accepted), model artifacts are immutable and
region-local.

---

## Non-goals

Explicitly out of scope so the design stays honest about what it does *not* solve:

- Full DSP/SSP header-bidding integration with external exchanges (OpenRTB is sketched in
  [06](./06-auction-pacing-budgets.md) but the design assumes a first-party/owned-inventory
  system as the primary case).
- Brand-safety ML (classification of publisher content) — assumed a bought service.
- Billing/invoicing correctness beyond the impression ledger described in [06](./06-auction-pacing-budgets.md).

---

## Build order

If this were being built rather than whiteboarded, the order that de-risks fastest:

1. **Skeleton + contracts** — `AdRequest`/`AdResponse` protobuf, ad-server that returns random
   eligible ads, event logging to Kafka, ops console showing live QPS. *Proves the pipes.*
2. **Offline lake + labels** — Kafka → Iceberg, impression/click joiner, first training set.
   *Proves the data is trustworthy before any model exists.*
3. **v0 ranker** — GBDT on ~30 features, shadow-scored only, logged and compared to random.
   *Proves the loop, not the accuracy.*
4. **Auction + pacing + budgets** — the money path, with the ledger and reconciliation.
5. **Feature store + retrieval** — online store, streaming features, two-tower ANN.
6. **DNN ranker + calibration + INT8** — the latency work starts here, against a real baseline.
7. **Second region, active-active** — only once single-region SLOs are met and stable.

---

*Design docs are written to be read in order but each stands alone; cross-links are inline.*
