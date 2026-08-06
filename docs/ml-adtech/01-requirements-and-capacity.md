# 01 — Requirements, SLOs & Capacity

## 1.1 Functional requirements

| ID | Requirement |
|----|-------------|
| F1 | Given a request context (user, placement, device, geo, time), return the best ad — or nothing — within the latency SLO. |
| F2 | Respect targeting rules: audience, geo, device, daypart, placement type, exclusions. |
| F3 | Run an auction across eligible candidates; charge the winner per the pricing rule (CPM/CPC/CPA). |
| F4 | Never overspend a campaign's budget; pace delivery smoothly across its flight. |
| F5 | Enforce frequency caps (`n` impressions per user per campaign per window). |
| F6 | Log every request/impression/click/conversion, exactly-once *for billing*, at-least-once for ML. |
| F7 | Support online experimentation: traffic splits by model, ranker config, and auction parameters. |
| F8 | Advertiser-facing console: campaign CRUD, budget, live delivery metrics, experiment results. |
| F9 | Honour consent/opt-out; degrade to contextual-only targeting when consent is absent. |

## 1.2 Non-functional requirements & SLOs

| Dimension | Target | Measured how |
|---|---|---|
| Latency | p50 ≤ 15 ms, **p99 ≤ 45 ms**, p99.9 ≤ 80 ms (server-side, ingress→egress) | Histogram per stage, per region, per placement |
| Availability | 99.95% monthly on the ad-decision endpoint | Successful non-5xx / total, per region |
| "No-fill without reason" | < 0.5% of requests return empty when eligible inventory exists | Fill-rate metric split by cause |
| Throughput | 2,400 RPS sustained peak per region, burst to 4,000 | Per-region ingress counter |
| Budget accuracy | ≤ 0.5% overspend per campaign per day | Ledger vs. budget reconciliation job |
| Freshness — user features | ≤ 60 s from event to servable | Event-time → serve-time histogram |
| Freshness — new campaign | ≤ 2 min from activation to servable | Control-plane → pod propagation metric |
| Model rollout safety | Any bad model rolled back within 10 min | Canary guardrail alerting |
| Training→serving skew | < 0.1% of features diverge in shadow comparison | Daily skew job |

**Explicit latency stance.** The role's brief says "inference under 100 ms". This design targets
**~10 ms for inference** and **45 ms end-to-end**, because inference is only one of nine stages.
Designing to the 100 ms number would be designing to the wrong budget — that is a point worth
making out loud in the interview.

## 1.3 Traffic math

```
128,000,000 requests / 86,400 s = 1,481 RPS average
```

Real ad traffic is diurnal. With an EU-centric user base the peak-to-average ratio is ~1.6:

```
peak ≈ 1,481 × 1.6 ≈ 2,370 RPS      (matches the stated "2,000+ RPS")
```

Provisioning rule — **each region must serve the whole peak alone** (active-active means a region
loss is a failover, not a brownout), plus 30% headroom for retries and traffic spikes:

```
per-region design point = 2,370 × 1.3 ≈ 3,100 RPS
burst ceiling (autoscale target)      ≈ 4,000 RPS
```

At steady state each region carries ~1,200 RPS, i.e. runs at ~40% of its own capacity. That is
the cost of active-active and it should be stated as such: **~2× serving cost buys sub-minute
regional failover with no cold start.**

### Downstream volumes

| Flow | Per request | At 2,370 RPS |
|---|---|---|
| Ad requests | 1 | 2,370/s |
| Candidates scored (post-filter) | ~500 | **1.19M scores/s** |
| Online feature reads | 1 multi-get (~40 keys) | 2,370 multi-gets/s ≈ 95k key-reads/s |
| Impressions (fill ≈ 85%, view ≈ 65%) | 0.55 | ~1,300/s |
| Clicks (CTR ≈ 1.2% of impressions) | ~0.007 | ~16/s |
| Log bytes (request log ~1.5 KB compressed) | — | ~3.5 MB/s ≈ 300 GB/day |

**The 1.19M scores/s number is the one that sets the architecture.** It is why ad-side features
live in-process ([04](./04-feature-store.md#42-why-ad-features-are-not-in-the-remote-store)) and why
scoring is one batched call ([05](./05-low-latency-serving.md)).

## 1.4 Compute sizing (ranking is the dominant cost)

Assume the production ranker is an embedding-input MLP: sparse lookups + `[D=256] → 256 → 128 →
64 → 2` dense layers.

```
dense FLOPs/candidate ≈ 2 × (256·256 + 256·128 + 128·64 + 64·2) ≈ 2 × 106k ≈ 212 kFLOP
per request  = 500 × 212k ≈ 106 MFLOP
at 2,370 RPS = 251 GFLOP/s   (per region, at peak)
```

A modern server core with AVX-512 and INT8 GEMM sustains ~60–120 GFLOP/s on well-shaped batched
matmuls; assume **50 GFLOP/s effective** after memory stalls, embedding gathers and framework
overhead — deliberately pessimistic.

```
cores for ranking math ≈ 251 / 50 ≈ 5 cores  (pure math)
× 3 for embedding gather, feature assembly, serialization, headroom ≈ 15–18 cores
+ non-ranking work (I/O, retrieval, auction, logging) ≈ 1.5× → ~40 cores/region at peak
```

Provision **24 pods × 4 vCPU (96 vCPU) per region** — ~2.5× the computed need. That multiple is
not padding for its own sake: it holds p99 down (queueing theory punishes high utilization hard —
at 80% utilization the wait term dominates the service term), and it absorbs the failover case.

Memory per pod:

| In-process state | Size |
|---|---|
| Ad feature table (1M ads × ~400 B) | ~400 MB |
| Ad embeddings (1M × 64 dims, INT8) | ~64 MB |
| HNSW index (M=16, 1M vectors) | ~250 MB |
| Eligibility bitmaps (~2k segments × roaring) | ~150 MB |
| Model weights (INT8) + runtime arenas | ~300 MB |
| **Total** | **~1.2 GB → request 3 GB/pod** |

24 pods × (4 vCPU, 3 GB) ≈ 96 vCPU / 72 GB per region → ~7× `c6i.4xlarge`-class nodes plus
overhead. Two regions ≈ 16 nodes for the serving tier. This is a **small** cluster; the surprising
cost centre at this scale is the *logging and training* side, not serving.

## 1.5 Storage & stream sizing

| Store | Size | Choice |
|---|---|---|
| Online feature store (10M users × ~2 KB) | ~20 GB + replication | Aerospike (or Redis Cluster) |
| Raw event lake (300 GB/day × 400 d, zstd) | ~120 TB | Iceberg on S3, partitioned by hour |
| Training samples (downsampled negatives) | ~2 TB/month | Parquet, Iceberg |
| Model registry artifacts | ~50 GB | S3 + MLflow metadata |
| Kafka retention (7 d hot) | ~2 TB | 24 partitions/topic, RF=3 |

## 1.6 Assumptions being made explicit

These are the numbers to challenge first if the interviewer pushes:

1. ~1M *active* ads/creatives at any time (not total ever created). If it were 100M, the ad-side
   state stops fitting in-process and retrieval must become a remote sharded service — a genuinely
   different architecture. **This is the single most load-bearing assumption.**
2. ~10M users, ~2 KB of features each.
3. Owned/first-party inventory as the primary case; external exchange bidding is additive.
4. EU-only traffic → two EU regions; latency-based routing keeps RTT low without global anycast.
5. Conversions arrive with a long tail (up to 7–30 days) — handled in
   [09](./09-data-and-training-pipeline.md#93-delayed-feedback).

---
Next: [02 — High-level architecture](./02-architecture-hld.md)
