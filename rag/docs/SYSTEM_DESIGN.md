# System design: RAG over 10M documents, with no hallucinations

The prototype in this repository indexes 90 documents. This note is about what
changes at 10 million, and about the second half of the title — which is the
harder half, because "no hallucinations" is not a model problem.

Where a claim here is a measured result from the prototype, it says so. Where it
is an engineering estimate, it says that too.

---

## 1. Scale: what actually breaks

Start from numbers, because the architecture follows from them.

| Quantity | Estimate |
|---|---|
| Documents | 10,000,000 |
| Chunks at ~4 per document | 40,000,000 |
| Embedding dimension | 1024 (float32) → 4 KB per vector |
| Raw vector storage | ~160 GB |
| Vectors after int8 quantisation | ~40 GB |
| Chunk text (~600 bytes each) | ~24 GB |
| BM25 inverted index | ~15–25 GB |

Two things fall out immediately. The flat numpy index in `dense.py` is finished
at roughly one to two million vectors: a brute-force scan of 40M × 1024 floats
is ~160 GB of memory traffic per query, which is seconds, not milliseconds.
And 160 GB does not fit in one machine's RAM at a price anyone will approve.

**What does not break:** BM25. A sharded inverted index at 40M documents is a
solved problem and has been since the 1990s. This matters more than it sounds,
because — see §4 — the measured result on the prototype is that BM25 is doing
most of the work.

### The index

**Vector search: HNSW with int8 quantisation, sharded by tenant.**

* HNSW gives sub-10ms search at this scale with recall in the 0.95–0.99 range
  against exact search, tuned via `efSearch`. **That lost recall is real and it
  is invisible unless you measure it** — which is the same discipline this
  repository's ablation applies to the ranking stages. Build a golden set of a
  few thousand queries, compute exact top-k offline with a brute-force scan, and
  track ANN-recall-vs-exact as a first-class metric with an alert on regression.
  An ANN index silently losing 3% recall after a rebuild looks exactly like
  nothing at all.
* Int8 quantisation cuts memory 4× for ~1–2% recall loss. Re-rank the top ~200
  ANN hits against full-precision vectors to recover most of it: read amplification
  is 200 vectors, not 40M.
* Shard by tenant first, not by hash. Tenant is the dominant filter in almost
  every enterprise deployment (§3), and sharding along it turns the most
  selective filter into a routing decision rather than a scan predicate.

**Lexical search: sharded BM25 (OpenSearch/Vespa), same shard key.** Co-locating
shards means fusion happens per-shard-group rather than after a cross-cluster
gather.

**Fusion across shards.** Reciprocal rank fusion is order-dependent, so ranks
must be computed globally, not per shard. Take top-k from each shard, merge, and
fuse on merged ranks. With per-shard k of 100 and 20 shards that is 2,000
candidates to merge — cheap, and it preserves the property that made RRF
attractive in the first place: no score calibration between shards.

### Latency budget

A p95 of 800ms for the full path is achievable and is what I would commit to:

| Stage | Budget | Note |
|---|---|---|
| Semantic cache lookup | 5 ms | Returns immediately on hit; everything below is skipped |
| Query rewrite | 0 ms rules / 400 ms model | Model-backed rewriting must be conditional — see §5 |
| ANN search | 15 ms | Parallel across shards |
| BM25 search | 25 ms | Parallel with ANN, not after it |
| Fusion + filter | 5 ms | |
| Cross-encoder rerank (50 candidates) | 60 ms | Batched, on GPU; this is the number that forces batching |
| Generation | 500–3000 ms | Dominates, and is why caching pays |

The ordering constraint that matters: **BM25 and ANN run concurrently.** They
are independent, and running them in sequence adds 25ms to every request for no
reason. This is the same class of mistake as executing parallel tool calls
serially — see `parallel.py` and demo 2 for the version of it that shows up one
layer up.

### Ingestion

At 10M documents with a typical enterprise change rate, expect ~1% daily churn:
100k documents, 400k chunks re-embedded per day. That is a steady ~5 chunks/sec
— trivial. The hard parts are not throughput:

* **Deletion must be synchronous with the source.** A document deleted in Notion
  that survives in the index is a compliance incident, not a stale cache. Drive
  ingestion from a change feed with tombstones, and reconcile nightly by
  comparing document id sets — the reconciliation job in the prototype's corpus
  exists for the same reason its real counterpart does.
* **Re-embedding the whole corpus is a scheduled event, not an emergency.**
  Changing the embedding model invalidates every vector. 40M embeddings at
  ~1000/sec/GPU is 11 GPU-hours — fine, but you cannot serve from a half-migrated
  index because the vectors are not comparable. Build into a new index, verify
  ANN-recall-vs-exact and end-to-end eval on the new index, then flip an alias.
  The search-indexer postmortem in the corpus is this exact failure: the alias
  was flipped on job exit rather than on verified document count.
* **Chunking changes are as invalidating as model changes** and are easier to do
  by accident, because chunking looks like application code.

---

## 2. No hallucinations: the part that is not about the model

"No hallucinations" cannot be promised by prompting, and a system that promises
it by prompting has simply moved the failure somewhere it cannot be observed.
What *can* be promised is: **every claim in an answer is traceable to a source
span, and claims that are not traceable do not ship.** That is a mechanical
property, and this repository implements it.

The measured finding behind this design, from `scripts/calibrate_abstention.py`:

```
answerable    n=62  min=0.99  p25=2.66  median=3.57
unanswerable  n=24  median=2.05  max=4.62
```

A relevance threshold that rejects every unanswerable probe must sit at 4.97,
and at that threshold **only 27% of genuinely answerable questions get an
answer**. That is not a tuning failure. Relevance scoring answers "is this
passage about this topic"; abstention needs "does this passage answer this
question". The highest-scoring unanswerable probe was *"what is the interest
rate on savings pots"* (4.62) — the corpus has a savings-goals service README
that discusses pots at length and never mentions interest rates. No scalar
derived from topical similarity separates those two cases.

So the guarantee is built from four layers, none of which is a threshold:

**Layer 1 — Retrieval gate (cheap, weak).** A relevance floor that rejects the
obviously-unrelated before spending a generation call. Catches "how do I bake
sourdough". Will never catch "do we offer unlimited vacation". Implemented as
`ExtractiveAnswerer.min_relevance`; calibrated, and documented as insufficient.

**Layer 2 — Entailment (the real gate).** The generator returns a structured
`answerable: bool` alongside the answer, and is instructed that partial
information is not an answer. A model can tell that a passage saying "27 days of
annual leave" does not support "unlimited". This is a judgement, so it belongs to
a model, not to a scalar. Implemented in `ClaudeAnswerer` via structured outputs
— which matters because a boolean in a schema-validated field is machine-readable,
whereas "I'm not sure, but..." in prose is not.

**Layer 3 — Quote verification (mechanical, non-negotiable).** Every claim
carries a verbatim span from a numbered context block, and every span is checked
by string containment against the chunk it points at *after* generation.
Whitespace and case are normalised, because a reflowed quote is not a
fabrication; a quote shorter than four words is rejected, because a three-word
span verifies nothing. **An answer where no citation verifies is converted to an
abstention** — not logged and returned. This is `verify_citations`, it runs on
every answer, and it cannot be prompted away, which is the entire point: it is
the only layer whose behaviour does not depend on the model cooperating.

**Layer 4 — Coverage.** The fraction of answer sentences carrying a verified
citation. Below a threshold the answer is surfaced as partially unsupported so a
caller can degrade it rather than render it as fact.

### What this does not catch

Being explicit, because a guarantee with unstated limits is worse than none:

* **Correct quotes, wrong conclusion.** Every span verifies and the synthesis
  across them is still wrong. Layer 3 does not touch this; only layer 2 does,
  imperfectly. This is the residual risk and it should be stated to users.
* **Stale sources.** The quote verifies against a document that is out of date.
  Freshness is an ingestion property, not a generation one — surface `updated`
  in the citation and let the reader judge.
* **Contradictory sources.** Two documents disagree; the model quotes one. The
  prompt instructs it to surface disagreement, but detecting contradiction
  reliably is a research problem, not a prompt.
* **The question nobody asked.** Retrieval returns nothing relevant and the
  system abstains — correctly — but the user needed an answer. Abstention is a
  cost, and the cost is measured in §4's answer rate, not hidden.

### Guardrails around it

* **Log every abstention with its reason.** Abstentions are the highest-signal
  data you have: they are precisely the questions your corpus should answer and
  doesn't. They drive the documentation backlog and the synonym lexicon.
* **Sample and grade.** Human-grade a stratified sample weekly. Automated
  citation verification catches unsupported claims; only a human catches
  *supported but wrong*.
* **Never let the model cite a document the user cannot read.** Enforce ACLs at
  retrieval, in the filter, before the chunk enters the context. A citation is
  an information leak even when the answer text is anodyne.

---

## 3. Multi-tenancy and access control

The single largest correctness risk at scale, and the one most likely to be
retrofitted badly.

**Permissions are a pre-filter, never a post-filter.** This is the same
architectural point `filters.py` makes about metadata, with a much worse failure
mode: post-filtering a permission returns fewer results (bad) *after* the
unauthorised chunks have already been ranked, logged, and possibly traced (much
worse). The prototype's `test_prefilter_returns_k_results_where_postfilter_would_not`
demonstrates the mechanism on metadata; the permission case is identical except
that the bug is a breach.

**Shard by tenant.** Cross-tenant leakage stops being a query-correctness
question and becomes a routing question — which is a far easier thing to audit.

**The cache key includes the tenant and the full filter set.** `SemanticCache`
scopes entries and refuses to match across scopes regardless of similarity, and
this is tested (`test_scope_isolation`). A semantic cache that ignores scope is
a cross-tenant data leak with a 90% hit rate.

---

## 4. What the measurement actually says

The prototype's ablation, on the held-out test split (43 queries, 90 documents,
339 chunks). Full detail and the paired-bootstrap intervals are in the README:

| configuration | recall@1 | recall@5 | MRR | p50 |
|---|---|---|---|---|
| BM25 only | 0.802 | 0.907 | 0.907 | 0.6 ms |
| Dense only | 0.639 | 0.907 | 0.822 | 0.5 ms |
| Hybrid (RRF) | 0.756 | 0.930 | 0.890 | 1.0 ms |
| Hybrid + rerank | 0.698 | 0.907 | 0.853 | 14.3 ms |
| Hybrid + rerank + rewrite | 0.756 | 0.907 | 0.888 | 11.5 ms |

**Only two deltas survive a 95% paired bootstrap.** Dense-only is significantly
worse than BM25 on MRR; hybrid is significantly better than dense-only. The
reranker and the rewriter are both within noise on a 43-query set.

The design consequences are not the ones the architecture diagram suggests:

1. **Invest in the first stage.** Hybrid retrieval is where the measurable win
   is, and it costs 0.4ms. At 10M documents this is where the engineering should
   go: shard layout, ANN recall, filter pushdown.
2. **Treat the reranker as unproven until it is proven on your corpus.** It costs
   roughly 15× the first stage's latency here and returns nothing detectable.
   That result will not generalise — a trained cross-encoder on a harder corpus
   is a different experiment — but the *method* generalises: measure it, on a
   held-out split, with an interval, before it goes in the request path.
3. **Candidate depth beats reranker quality.** First-stage recall@1 is 0.756
   while recall@30 is 0.977. The reranker's entire budget is that 0.22 gap, and
   no reranker can exceed the first stage's recall at its candidate depth. If
   recall@50 is 0.90, a perfect reranker still tops out at 0.90.

### On the eval set itself

43 test queries cannot detect a two-point change; the bootstrap intervals show
this directly. At 10M documents the eval set is the deliverable that matters
most and the one that gets skipped:

* **Thousands of queries, not dozens**, mined from real logs and stratified by
  the failure modes that actually occur (this repository stratifies by lexical /
  morphology / paraphrase / compositional / filtered / definition, and the
  per-category table is where the story is — the aggregate hides it).
* **Labelled from click-through and human review**, not from the system's own
  output, which bakes in today's failures as tomorrow's ground truth.
* **Split into dev and test, and keep test held out.** The prototype's reranker
  gained +0.055 MRR on dev and lost 0.038 on test. That gap is the entire reason
  the split exists, and it is a live example rather than a hypothetical.

---

## 5. Cost, and where it goes

At 1M queries/day, generation dominates everything else by an order of
magnitude. The levers, in order of effect:

1. **Semantic caching.** On a support-style workload the same questions recur
   constantly. The threshold is a correctness decision, not a hit-rate knob —
   demo 6 shows a measured curve where a threshold chosen for hit rate serves
   40% of its hits from the wrong document. The concrete near-miss in that demo:
   *"how do I roll back a database migration"* sits at 0.84 similarity to *"how
   do I roll back a deployment"*, and the deploy runbook says explicitly that
   rolling back a deploy does **not** roll back migrations. A cache tuned to 0.80
   answers the first question with the second question's answer, instantly and
   with no error anywhere.
2. **Prompt caching on the system prompt and tool definitions.** These are
   byte-identical across requests, so they belong at the front of the prefix
   with a `cache_control` breakpoint, and everything volatile belongs after it.
   Implemented in `ClaudeQueryRewriter` and `ClaudeAnswerer`.
3. **Conditional rewriting.** Rewriting multiplies first-stage cost by the number
   of variants, and demo 5 shows it does nothing for queries that already
   retrieve well — a bare error code needs no rewrite and gets none. Route it:
   rewrite when the first attempt's top score is low, not unconditionally.
4. **Right-size the model per stage.** Query rewriting is a cheap structured task
   and should run at low effort. Grounded answering is not.

---

## 6. What I would build first

In order, and the ordering is the argument:

1. **The eval set.** Thousands of labelled queries, stratified, split. Everything
   else is unmeasurable without it, and every decision below is a guess without it.
2. **Ingestion with deletion and reconciliation.** Correctness of the corpus
   precedes quality of retrieval over it.
3. **Hybrid retrieval with pre-filtered ACLs.** The measured win, and the
   security boundary.
4. **Abstention and citation verification.** Before a single answer reaches a
   user, because retrofitting a grounding guarantee onto a shipped system means
   arguing about how many wrong answers are acceptable.
5. **Semantic caching**, with the false-hit curve measured before the threshold
   is chosen.
6. **Re-ranking and model-backed rewriting** — last, and only if the ablation on
   the real corpus says they pay. On this corpus they did not.

The sequencing is the point. Steps 5 and 6 are the ones that appear first in
most architecture diagrams, and they are the two whose value this repository
could not demonstrate.
