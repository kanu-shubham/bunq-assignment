# 05 — Low-Latency Serving

Target: **~10 ms p99 for scoring 500 candidates**, inside a 45 ms end-to-end budget. This chapter
is about how the inference actually happens and how the tail is controlled.

## 5.1 The core decision: in-process inference

```
Option A: ad-server → gRPC → Triton/TF-Serving pod → model
Option B: ad-server with ONNX Runtime linked in-process        ← chosen
```

| | A: remote server | B: in-process |
|---|---|---|
| Network + serialize (500×256 floats ≈ 512 KB) | +2–4 ms p99, worse under load | 0 |
| Dynamic batching queue wait | +1–5 ms (that's the *point* of it) | 0 |
| GPU utilization | good | n/a |
| Independent model scaling | yes | no — scales with the ad server |
| Ops complexity | two deployables, two autoscalers | one binary |
| Tail predictability | two queueing systems compose | one |

At 500 candidates/request the batch is *already* large — the main benefit of a dynamic-batching
inference server (assembling a big batch from many small requests) doesn't apply. So we pay its
costs for none of its benefits. **In-process wins on the metric that matters here: p99.**

The interface is kept narrow (`Scorer.Score(ctx, batch) → []Prediction`) with two implementations
(in-process ORT, remote Triton) so the decision is reversible.

### When to move to GPU

Move to Triton-on-GPU when any of these becomes true — and not before:

- Ranker FLOPs/candidate grow >10× (e.g. a transformer over user history with hundreds of tokens).
- Candidate count per request grows past ~2,000.
- CPU ranking cost exceeds ~40% of serving spend and INT8 + distillation are exhausted.

The migration path is planned: same ONNX graph, Triton with **shared-memory (CUDA IPC / system
shm) input tensors** to avoid the 512 KB copy, `max_queue_delay_microseconds` set to ~500 µs so
batching never adds more than that, and one Triton pod per node (DaemonSet) so the "network" hop
is loopback.

## 5.2 Model-side latency work, in order of ROI

| Technique | Speedup | Quality cost | Notes |
|---|---|---|---|
| **Batching all candidates into one call** | 5–20× vs. per-candidate | none | The single biggest win. Turns 500 tiny GEMVs into a few large GEMMs. |
| **INT8 post-training quantization** (weights + activations) | 2–3× | AUC −0.0005 typical | Calibrate on 10k real requests; per-channel weight scales. Verify calibration curve after quantizing — quantization can shift probabilities more than it shifts AUC. |
| **Embedding table pruning / hashing** | memory 3–5× | small | Hash trick with 2 hash functions; prune ids below a frequency floor into an OOV bucket. |
| **Operator fusion + graph optimization** (ORT `ORT_ENABLE_ALL`) | 1.2–1.5× | none | Free. Do it first. |
| **Distillation** (big teacher → serving student) | 2–4× | recovers most of teacher's lift | The way to get big-model quality at small-model cost; teacher trains nightly, student ships. |
| **Early-exit / two-pass ranking** | ~2× | small | Cheap model scores 500 → top 100 → full model. Adds complexity; hold in reserve for when candidate counts grow. |
| FP16 on CPU | ~1× | — | Not useful on most CPUs; INT8 is the CPU lever. |

**Order matters:** fusion → batching → quantization → distillation. Measure after each; stop when
the budget is met, because every one of these adds a way for training and serving to diverge.

## 5.3 Tail latency engineering (the part that's actually hard)

p50 is a function of the algorithm; **p99 is a function of the system**. Sources of tail, and the
countermeasure for each:

| Tail source | Countermeasure |
|---|---|
| **Go GC pauses** | `GOGC` tuned + `GOMEMLIMIT` set; per-request **arena** (`sync.Pool` of pre-sized buffers) for the `[500×D]` tensor and candidate slices so the hot path allocates ~nothing. Target: <1 MB allocated per request. |
| **Thread oversubscription** | ORT `intra_op_num_threads=1`, one inference per goroutine, parallelism from request concurrency instead. Prevents N requests × M threads thrashing the scheduler. `GOMAXPROCS` set from the cgroup CPU limit (`automaxprocs`) — not doing this is a classic Kubernetes tail-latency bug. |
| **NUMA / cache misses** | Pin pods to a socket; struct-of-arrays feature layout (see [04](./04-feature-store.md#42-why-ad-features-are-not-in-the-remote-store)) so scoring streams memory instead of chasing pointers. |
| **Slow remote reads** | Hedged requests at p95 + hard timeout + fallback. |
| **Queueing at high utilization** | Keep pods at ≤50% CPU; scale on **p99 latency and in-flight concurrency**, not average CPU. |
| **Head-of-line blocking on the LB** | Envoy `LEAST_REQUEST`, per-pod `max_concurrent_requests`, and *load shedding* (`503` fast) rather than queueing when in-flight exceeds a bound. Shedding 0.1% fast beats making 100% slow. |
| **Cold start after deploy** | Readiness probe only passes after: snapshot loaded, index built, and **N warm-up inferences** run (JIT/arena warm). Argo Rollouts holds traffic until then. |
| **Noisy neighbours** | CPU requests == limits (Guaranteed QoS class), no CPU-throttling surprises. |

**Timeout discipline.** Every stage has a deadline derived from the request deadline, propagated
via `context.WithTimeout`. Deadlines are *budgets*, not constants: if feature fetch took 4 ms of
its 5 ms allowance, the ranker still gets its 10 ms and the auction absorbs the difference by
skipping optional work (e.g. secondary policy checks that can run post-hoc).

## 5.4 Caching strategy

Caching is dangerous in ad serving — a cached ad decision can overspend a budget or violate a
frequency cap. So cache *inputs and intermediates*, never the final decision (with one bounded
exception):

| Cache | Key | TTL | Hit rate | Guards |
|---|---|---|---|---|
| Ad features + embeddings + index | — (full snapshot) | 30–60 s refresh | 100% | Version check, checksum |
| User feature blob | `user_id` | 5 min | 70–85% | Invalidated by Flink "material change" signal |
| User tower embedding | `user_id` + feature hash | 5 min | ~80% | Recomputed on feature-hash change |
| Eligibility result | `(geo, device, placement, segment-bucket)` | 10 s | ~60% | Bucketed segments only; cheap enough that this is optional |
| **Full ad decision** | `(placement, user-bucket, minute)` | ≤ 1 s, **shed-mode only** | — | Only enabled under overload; bypasses pacing accuracy deliberately and is metered |

**What is never cached:** budget state, frequency-cap counters, and per-user auction outcomes.

## 5.5 Load shedding & overload behaviour

A well-behaved ad server under 3× expected load should serve 100% of requests *worse*, not 33% of
requests *well* and time out the rest. The ladder:

```
utilization              behaviour
────────────────────────────────────────────────────────────────────
< 60%                    full path
60–75%                   drop exploration slot, reduce candidates 500 → 300
75–85%                   skip pCVR head (CPA campaigns use prior), candidates → 200
85–95%                   GBDT fallback model, candidates → 100
> 95% or queue > bound   shed: eCPM-sorted popularity from in-process table (~1 ms)
```

Each level is a flag flipped by an in-process controller reading its own p99 and in-flight count —
not a human, and not a global config push (which would arrive too late). Every level is tagged in
the response and the log so the revenue impact of each degradation is measurable, and so degraded
rows can be excluded from training.

## 5.6 Benchmarking method

Latency claims that come from a laptop benchmark are worthless. The verification plan:

1. **Micro:** Go benchmarks for feature assembly, bitmap intersection, ANN search, ORT `Run` —
   each with allocation counts (`-benchmem`), asserted in CI with a regression threshold.
2. **Component:** load-generate against a single pod with production-shaped requests (replayed
   from logged traffic), report full histograms, not averages. Find the knee of the
   throughput/latency curve — that's the autoscaler's target, and it must be measured, not guessed.
3. **System:** shadow-replay a full day of real traffic at 1×, 2×, 3× speed against the staging
   region; verify SLOs and the degradation ladder actually engages in order.
4. **Continuous:** a nightly load test in CI; p99 regression > 10% fails the build.

Reported as: p50 / p90 / p99 / p99.9 / max, per stage, **per region**, with the number of samples.

---
Next: [06 — Auction, pacing & budgets](./06-auction-pacing-budgets.md)
