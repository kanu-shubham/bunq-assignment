# ragkit — a production-shaped RAG stack, built to be measured

A hybrid retrieval system over a documentation dump, with tool use, parallel tool
execution, re-ranking, query rewriting, semantic caching, and grounded answering
with citations. Every stage is behind a flag, because the assignment's actual
instruction was *measure recall@k before and after each addition — that number is
the whole lesson*.

The number turned out to be a more interesting lesson than expected. **Two of the
three additions did not pay off on the held-out split.** That result is reported
here rather than tuned away, along with the reason and what I would do about it.

```bash
cd rag
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python scripts/run_ablation.py --split test      # the headline measurement
python scripts/ask.py --demo                     # grounded answering + abstention
python -m pytest tests/ -q                       # 97 tests
for d in demos/demo*.py; do python "$d"; done    # the six prototypes
```

No API key, no model download, no network. See [Limitations](#limitations) for
what that costs and where it changes a conclusion.

---

## The measurement

90 documents, 339 chunks, 62 labelled queries split into **19 dev** (all tuning
happened here) and **43 test** (untouched until the end). Each rung of the ladder
changes exactly one thing from the rung above it.

**Held-out test split:**

| configuration | recall@1 | recall@3 | recall@5 | recall@10 | MRR | nDCG@10 | p50 |
|---|---|---|---|---|---|---|---|
| 1. BM25 only | 0.802 | 0.907 | 0.907 | 0.919 | 0.907 | 0.905 | 0.8 ms |
| 2. Dense only | 0.639 | 0.872 | 0.907 | 0.954 | 0.822 | 0.847 | 0.5 ms |
| 3. Hybrid (RRF) | 0.756 | 0.907 | **0.930** | 0.954 | 0.890 | 0.899 | 1.0 ms |
| 4. Hybrid + rerank | 0.698 | 0.907 | 0.907 | 0.930 | 0.853 | 0.863 | 15.4 ms |
| 5. Hybrid + rerank + rewrite | 0.756 | 0.907 | 0.907 | 0.930 | 0.888 | 0.886 | 13.0 ms |
| 6. Hybrid + rewrite (no rerank) | 0.767 | 0.872 | **0.930** | 0.954 | 0.901 | 0.901 | 2.1 ms |

Rung 6 is off the main ladder deliberately: with rewriting and re-ranking only
ever measured together, neither effect is attributable on its own.

**A point estimate on 43 queries is not evidence**, so every delta gets a paired
bootstrap (5000 resamples, 95% CI):

| change | Δ recall@5 | Δ MRR | verdict |
|---|---|---|---|
| BM25 → Dense | +0.000 [-0.070, +0.070] | **-0.085 [-0.164, -0.008]** | dense alone is significantly worse |
| Dense → Hybrid | +0.023 [+0.000, +0.070] | **+0.069 [+0.019, +0.127]** | fusion significantly better |
| Hybrid → + rerank | -0.023 [-0.070, +0.000] | -0.038 [-0.111, +0.020] | noise |
| + rerank → + rewrite | +0.000 [+0.000, +0.000] | +0.035 [+0.000, +0.081] | noise |
| Hybrid → + rewrite only | +0.023 [+0.000, +0.070] | +0.013 [-0.025, +0.057] | noise |

**Only hybrid fusion clears the bar.** The reranker and the rewriter are both
indistinguishable from noise on a 43-query set — and the reranker costs 15× the
first stage's latency to achieve it.

### Where the aggregate hides the story

| configuration | compositional | definition | filtered | lexical | morphology | paraphrase |
|---|---|---|---|---|---|---|
| BM25 only | 1.000 | 0.750 | 1.000 | 1.000 | 0.857 | 0.800 |
| Dense only | 1.000 | 0.750 | 1.000 | 1.000 | **1.000** | 0.700 |
| Hybrid (RRF) | 1.000 | 0.750 | 1.000 | 1.000 | **1.000** | **0.800** |

Dense retrieval buys exactly one thing here: **morphology**, 0.857 → 1.000.
Typos and inflected forms (`reconciliaton`, `deploymnt`, `consumers lagging`)
are out-of-vocabulary for BM25 and near-neighbours for a character-n-gram
embedder. It pays for that by losing paraphrase queries, where BM25's exact
matching on the few shared terms does better than a blurry vector. Fusion keeps
both. That is the entire argument for hybrid search, visible in one row.

---

## The three findings worth your time

### 1. A reranker that ignores retrieval evidence destroys it

The first implementation scored candidates on joint (query, passage) features
and sorted by that score. It **dropped correct documents out of the top ten
entirely** on paraphrase queries — rank 1 → not-in-top-10 for *"why do we use the
same database technology everywhere"*.

The cause is structural, not a bug, and one query shows it exactly. For *"why do
we use the same database technology everywhere"* the first stage ranks the
correct ADR first, and the cross-encoder scores it **lowest of the top four**:

| first-stage rank | document | ce score | coverage | phrase | proximity |
|---|---|---|---|---|---|
| 1 | `adr-0015-postgres-over-document-store` **(gold)** | **1.49** | 0.30 | 0.00 | 0.11 |
| 2 | `readme-audit-trail` | 3.27 | 0.38 | 0.40 | 0.57 |
| 3 | `readme-statement-service` | 3.00 | 0.38 | 0.40 | 0.57 |
| 4 | `readme-support-inbox` | 3.27 | 0.38 | 0.40 | 0.57 |

The distractors are service READMEs containing the literal string "state lives in
PostgreSQL" — so they score on phrase match and term proximity. The ADR *argues
about the decision* in different words ("datastore", "four database
technologies", "default"), so it matches on none of them. Lexically-derived
features correlate with BM25, so re-deciding on them alone throws away the dense
half of the hybrid precisely where dense was carrying the result.

Re-ranking therefore has to be **a refinement of retrieval, not a replacement for
it**: the final score interpolates the first-stage rank with the cross-encoder
score, both min-max normalised within the candidate list
(`CrossEncoderWeights.prior_weight`, swept on dev).

**That reduced the aggregate damage but did not fix this query.** At the shipped
`prior_weight=0.4` the gold document is still outside the top ten; only at 1.0 —
which is to say, ignoring the reranker entirely — does it come back to rank 1.
Which is finding 3: on this corpus the reranker is net-negative, and this is one
of the queries paying for it.

### 2. Dev-set gains are not gains

The reranker, tuned on dev:

| split | recall@3 | recall@10 | MRR |
|---|---|---|---|
| dev (19 queries, tuned here) | 0.868 → 0.895 | 0.947 → **1.000** | 0.767 → **0.795** |
| test (43 queries, held out) | 0.907 → 0.907 | 0.954 → **0.930** | 0.890 → **0.853** |

A clean win on dev, a loss on test. A 243-point grid search over the feature
weights spanned only 6% in objective, which is the signal that 19 queries cannot
distinguish the configurations being compared. Reporting the dev number as the
result would have been straightforwardly wrong, and would have shipped a
component that costs 15ms and returns nothing.

### 3. The reranker's budget is set by candidate depth, not by the reranker

| depth | recall |
|---|---|
| recall@1 | 0.756 |
| recall@5 | 0.930 |
| recall@10 | 0.954 |
| recall@30 | 0.977 |

A gold document is in the top 30 for 97.7% of queries but at rank 1 for 75.6%.
That 0.221 gap is the **entire** budget available to re-ranking — no reranker can
exceed the first stage's recall at its candidate depth. On this corpus the first
stage has already done nearly all the work by rank 10, which is why there is
almost nothing left to reorder. `candidate_k` is the more important knob and it
gets a fraction of the attention.

**What I would not conclude:** that rerankers do not work. A hand-built
feature scorer on a 90-document corpus is not a trained cross-encoder on a
million-document one. What generalises is the method: measure it, on a held-out
split, with an interval, before it goes in the request path.

---

## Grounded answering, and why abstention is not a threshold

`scripts/calibrate_abstention.py` scores 62 answerable questions against 24 the
corpus cannot answer:

```
answerable    n=62  min=0.99  p25=2.66  median=3.57
unanswerable  n=24  median=2.05  max=4.62
```

A threshold that rejects every unanswerable probe must sit at 4.97 — where **only
27% of genuinely answerable questions get an answer**. The highest-scoring
unanswerable probe is *"what is the interest rate on savings pots"* (4.62): the
corpus has a savings-goals README that discusses pots at length and never
mentions interest.

That is not a calibration failure. Relevance scoring answers *"is this passage
about this topic"*; abstention needs *"does this passage answer this question"*.
No scalar derived from the first decides the second. So the guarantee is built
from layers that are not thresholds:

1. **Relevance gate** — cheap, weak, documented as insufficient. Catches "how do
   I bake sourdough".
2. **Entailment** — the generator returns a structured `answerable: bool`. A
   model can tell that "27 days of annual leave" does not support "unlimited".
3. **Quote verification** — every claim carries a verbatim span, checked by
   string containment against the cited chunk *after* generation. An answer where
   no citation verifies becomes an abstention. This runs on every answer and
   cannot be prompted away, which is the point: it is the only layer that does
   not depend on the model cooperating.
4. **Coverage** — the fraction of sentences carrying a verified citation, so a
   caller can degrade rather than render as fact.

`python scripts/ask.py --demo` shows this working *and* failing: "unlimited
vacation" correctly abstains, "what is the capital of France" does not — it
reaches "capital statements" in the tax-reporting README, and every sentence it
returns verifies against its source while the answer remains useless. The demo
says so.

---

## The six prototypes

| | demo | what it shows |
|---|---|---|
| 1 | `demo1_tool_schemas.py` | A tool definition before and after review, linted; strict validation rejecting six classes of bad argument with messages written to be handed back to the model |
| 2 | `demo2_parallel_tools.py` | Seven tool calls in one turn: concurrent execution (780ms → 416ms), retries with jittered backoff, a side-effecting tool serialised, failures returned as `is_error` rather than dropped, all results in **one** user message |
| 3 | `demo3_hybrid_search.py` | Queries where BM25 wins and where dense wins; RRF vs normalised fusion; pre-filtering returning 5 results where post-filtering returns 2 |
| 4 | `demo4_reranking.py` | The headroom, the joint features, bi-encoder vs cross-encoder measured side by side with bootstrap intervals |
| 5 | `demo5_query_rewriting.py` | What each strategy produces, what it costs per variant, and per-category recall showing the aggregate hiding the effect |
| 6 | `demo6_semantic_cache.py` | Exact vs semantic hits, scope isolation, and the false-hit curve |

Demo 6's punchline is in the miss column: *"how do I roll back a database
migration"* sits at **0.84** similarity to *"how do I roll back a deployment"* —
a near-miss at the shipped 0.90 threshold and a confident hit at 0.80. Those
questions have different answers, and the deploy runbook says explicitly that
rolling back a deploy does **not** roll back migrations. At threshold 0.30 the
sweep shows **40% of cache hits serving an answer about a different document**.

---

## Layout

```
rag/
├── ragkit/
│   ├── types.py           Document, Chunk, ScoredChunk, EvalQuery
│   ├── textutil.py        tokenisation that keeps ERR_CURRENCY_MISMATCH intact
│   ├── corpus.py          frontmatter + loading
│   ├── chunking.py        heading-aware; never splits a table or code fence
│   ├── embeddings.py      HashingEmbedder (offline) | SentenceTransformerEmbedder
│   ├── lexical.py         BM25 via rank_bm25, with a pure-Python fallback
│   ├── dense.py           exhaustive cosine index
│   ├── filters.py         metadata filters, applied before top-k
│   ├── hybrid.py          RRF and normalised fusion, per-document diversity cap
│   ├── rerank.py          NoOp | BiEncoder | LexicalCrossEncoder | CrossEncoder
│   ├── query_rewrite.py   rule-based strategies | Claude-backed
│   ├── cache.py           semantic cache + false-hit measurement
│   ├── tools.py           schemas, linting, strict validation, registry
│   ├── parallel.py        concurrent tool execution and the result contract
│   ├── answer.py          citations, verification, abstention
│   ├── pipeline.py        the composed pipeline, every stage behind a flag
│   └── evaluation.py      recall@k, MRR, nDCG, ablation, paired bootstrap
├── data/corpus/           90 markdown docs (READMEs, runbooks, postmortems, ADRs, policies)
├── data/eval/queries.jsonl  62 labelled queries, dev/test split, 6 categories
├── demos/                 the six prototypes
├── scripts/               run_ablation.py · ask.py · calibrate_abstention.py
├── tests/                 97 tests
└── docs/SYSTEM_DESIGN.md  RAG over 10M docs, no hallucinations
```

### Metric definitions

Stated because they vary between libraries and an undefined "recall" is not
comparable to anything. **recall@k**: take the top-k *chunks*, map to documents,
score `|gold ∩ those documents| / |gold|`. Documents, not chunks, because that is
what reaches the model's context — and k chunks may come from fewer than k
documents, which is a real cost of chunking that a chunk-level metric hides.

---

## Design decisions

**Tokenisation keeps compound identifiers.** `payments.sepa_instant.enabled`
yields the whole identifier *and* its parts. A naive `\w+` split shreds
`ERR_CURRENCY_MISMATCH` into three common words and makes the one query that
should be trivial indistinguishable from every document mentioning currency.

**Chunking is heading-aware and never splits a table or a code fence.** Tested,
because the chunk is what the model eventually reads.

**Filters are applied before top-k.** Post-filtering — retrieve k, then discard —
silently returns fewer than k results exactly when the filter is selective. The
failure presents as "the retriever found nothing", which sends you debugging the
wrong component. There is a test asserting the difference.

**Fusion is rank-based by default.** BM25 is unbounded, cosine is in [-1, 1];
adding them means whichever is larger today wins. RRF needs no per-corpus tuning.

**The cache key includes the filter scope**, and matching across scopes is
refused regardless of similarity. In a multi-tenant system that is a data-leak
bug, not a tuning issue.

**Side-effecting tools are not parallel-safe**, enforced in `ToolSpec.__post_init__`
rather than left to the caller.

**Bad tool arguments are never retried.** A schema violation does not become
valid on the second attempt; the validation message goes back to the model, which
fixes its own call.

---

## Limitations

Stated plainly, because a result whose caveats are buried is not a result.

**The embedder is a hashing encoder, not a trained bi-encoder.** HuggingFace is
unreachable from the environment this was built in (403 through the proxy), so
`SentenceTransformerEmbedder` and `CrossEncoderReranker` are implemented and
wired but not exercised in any reported number. The hashing embedder is IDF-
weighted word + character-n-gram hashing: it genuinely handles morphology and
typos, which is where the dense/lexical complementarity in the results comes
from, but it **cannot** learn that "PTO" and "annual leave" mean the same thing.
That gap is why paraphrase is the weakest category and why the semantic cache
mostly fires on near-duplicates. With a real bi-encoder, dense-only and the cache
both improve; I would expect the hybrid conclusion to hold and the paraphrase row
to change most.

**The cross-encoder is a feature scorer, not a neural model.** It computes the
joint features a trained cross-encoder learns to attend to — IDF-weighted
coverage, phrase containment, proximity, field of match, and a MaxSim-style
late-interaction term over sentence embeddings. The family of errors it addresses
is the right one; the magnitude is not a proxy for a trained model's.

**The eval set is too small to detect small effects,** and the bootstrap
intervals say so rather than leaving it implied. 43 test queries cannot resolve a
two-point change. This is the correct conclusion to draw about the *evidence*,
not a hedge about the code.

**90 documents is a small corpus.** First-stage recall@10 is already 0.954, so
the ceiling for anything downstream is low by construction. The corpus was grown
from 36 to 90 during development for exactly this reason — the added documents
are structurally similar neighbours (twenty more services with READMEs and
runbooks), which is what real dumps are mostly made of and what makes retrieval
hard. It was not grown further because hand-labelling gold documents is the
binding constraint, not generating text.

**The corpus is synthetic.** Written to be realistic — a payments platform's
READMEs, Confluence runbooks and postmortems, ADRs, and Notion policy pages, with
deliberate vocabulary overlap between related documents — but it is not a real
export. Gold labels were assigned by hand and are checked by a test that every
referenced document exists.

---

## Claude integration

Optional throughout; nothing above requires it.

* `ClaudeQueryRewriter` — structured outputs (`output_config.format` with a JSON
  schema) so the response is a validated object rather than prose parsed by
  regex; `effort: low`, since rewriting is a cheap task; system prompt cached
  with a `cache_control` breakpoint since it is byte-identical per call.
* `ClaudeAnswerer` — structured outputs carrying `answerable`, the answer, and
  per-claim verbatim quotes; `stop_reason == "refusal"` handled before reading
  content; every returned quote passed through `verify_citations` regardless of
  what the model claimed.
* `tools.py` emits the Messages API wire format with `strict: true` and
  `additionalProperties: false`.
* `parallel.py` implements the tool-result contract: all results for one
  assistant turn in a single user message, failures as `is_error` rather than
  omitted.

Model: `claude-opus-5`.

```bash
export ANTHROPIC_API_KEY=...     # or: ant auth login
python scripts/ask.py "how do I roll back a deploy" --claude
```
