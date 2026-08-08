# RAG over company documentation — design

A question-answering system over internal documentation (Confluence, Drive,
Notion, GitHub, a handbook repo). Employees ask in natural language; the system
answers with citations they can click, or says it does not know.

This document is the design. `README.md` is how to run the implementation in
this repository, and `INTERVIEW.md` is the condensed version for a whiteboard.

---

## 1. What the system is actually for

| | |
| --- | --- |
| Users | ~2,000 employees, mixed technical and non-technical |
| Corpus | ~40,000 documents, ~600k chunks, growing ~2% per week |
| Volume | ~8,000 questions/day, peak ~5 QPS, bursty around Monday morning and policy changes |
| Latency target | first token < 2.5 s p95, complete answer < 8 s p95 |
| Freshness target | an edited document is answerable within 15 minutes |
| Cost ceiling | < $0.05 per answered question at steady state |

### The requirements that actually shape the design

1. **A wrong answer is worse than no answer.** This is an internal assistant for
   expense rules, security procedure, and on-call policy. A confident wrong
   answer about a 30-day claim deadline creates work; "I don't know, here are
   two related pages" does not. Abstention is a feature with its own metric.
2. **Every claim must be traceable.** Users must be able to verify without
   trusting the model. Citations are validated by the system, not just
   requested from the model.
3. **Permissions are not advisory.** Compensation bands, unreleased roadmaps,
   and incident postmortems have narrower audiences than the average document.
   The retrieval layer is the enforcement point.
4. **Documentation is wrong, stale, and self-contradictory.** The system must
   surface conflicts and prefer recency rather than silently picking one.

Explicit non-goals for v1: multi-turn conversational memory, agentic tool use
(ticket creation, writes to source systems), and answering from anything other
than the indexed corpus.

---

## 2. Architecture

```
INGEST (async, per source)                     QUERY (sync, per request)
──────────────────────────                     ─────────────────────────────
connectors                                     question + principal
  │ documents + ACL + updated_at                 │
  ▼                                              ▼
structure-aware chunker                        ┌──────────────┐   ┌───────────┐
  │ heading path, atomic blocks                │ dense (ANN)  │   │ BM25      │
  │ deterministic chunk ids                    │ top-50       │   │ top-50    │
  ▼                                            └──────┬───────┘   └─────┬─────┘
content-hash diff ── unchanged? stop                  │  ACL pre-filter │
  │ new/edited only                                   └────────┬────────┘
  ▼                                                            ▼
embed (batched, cached)                              RRF fusion + per-doc cap
  │                                                            │ top-40
  ├──────────────► vector index (HNSW)                         ▼
  └──────────────► lexical index (BM25)                LLM reranker (batched)
                                                               │ top-8
                                                               ▼
                                                    grounded prompt (cached prefix)
                                                               │
                                                               ▼
                                                    Claude Opus 5 (streaming)
                                                               │
                                                               ▼
                                                    citation verification
                                                     ├─ valid → answer
                                                     └─ ungrounded → 1 retry → abstain
```

Both indices are written in the same transaction-shaped call, because dense and
lexical disagreeing about what exists shows up as citations that cannot be
resolved at answer time.

---

## 3. Chunking

**Decision: structure-aware chunking with contextualisation, ~512 tokens target,
64-token overlap within a section, atomic tables and code blocks.**

Chunking is the highest-leverage and least glamorous decision in the system. A
retriever cannot recover from a chunk that lost its heading or split a table in
half.

Four rules, each earning its complexity:

1. **Never cross a heading boundary.** A chunk that spans *Security* and
   *Expenses* has metadata that lies, and it dilutes both topics in the
   embedding.
2. **Never split an atomic block.** Half a markdown table retrieves well and
   then misinforms: the header row is in one chunk and the numbers in another.
   Same for fenced code.
3. **Contextualise before embedding.** The stored `embed_text` is
   `"{title} › {heading path}\n\n{body}"`. "It must be approved within 30 days"
   is unretrievable on its own; prefixed with *Expense Policy › Submitting a
   claim* it is trivially retrievable. This is a cheap approximation of
   Anthropic's contextual retrieval; the expensive version generates a
   one-sentence LLM summary of each chunk's role in its document, which is
   measurably better and costs one call per chunk at ingest (worth it above a
   certain corpus value — see §12).
4. **Deterministic chunk ids** = `sha256(doc_id, heading_path, ordinal,
   content_hash)`. This is what makes incremental indexing possible: an
   unchanged chunk keeps its id and is skipped; an edited paragraph gets a new
   id, and the old one is deleted.

**Sizes.** 512 tokens is a compromise: large enough that a chunk usually
contains a complete answer, small enough that eight of them fit in a prompt
with room for the answer, and small enough that the embedding is about one
topic. 64-token overlap only within a section recovers answers that straddle a
paragraph break without bleeding one section's content into another's metadata.

**Rejected: fixed-size character splitting** (simplest, and destroys structure),
**per-sentence chunks** (retrieval becomes a needle hunt; the model gets
fragments with no context), and **whole-document chunks** (blows the context
budget, and dilutes embeddings so badly that recall collapses on large docs).

**Known limitation.** Token counts come from a local estimator, not Claude's
tokenizer — a network call per candidate boundary is not viable. The estimator
is calibrated offline against `messages.count_tokens` and biased to overcount,
so budgets hold.

---

## 4. Retrieval

**Decision: hybrid dense + BM25, fused with Reciprocal Rank Fusion, ACL applied
as a pre-filter.**

### Why hybrid is not optional here

Dense retrieval fails on exactly the queries an internal corpus attracts most:
error codes (`ERR_ACCT_4032`), ticket ids, internal product names, acronyms,
and anything coined after the embedding model's training cutoff. Those are
exact-match problems and BM25 solves them for nothing. Conversely BM25 fails on
"how long do I have to file this" → "claims must be submitted within 30 days",
where there is no term overlap at all. The two failure modes barely intersect,
which is what makes fusion pay.

### Why RRF over a tuned weighted blend

BM25 scores are unbounded and corpus-dependent; cosine similarities cluster in
a narrow band. Normalising them against each other requires re-tuning whenever
the corpus grows or the embedding model changes. RRF combines *ranks*, has one
parameter (k=60, famously insensitive), and needs no calibration. A tuned
weighted blend beats RRF by a few points of nDCG **once you have enough labelled
data to tune on** — so RRF ships first and the weighted path stays in the code
behind a config flag for when the eval set is big enough to justify it.

### ACL is a pre-filter, and that is a load-bearing detail

The filter is applied inside both index scans, before top-k, never as a
post-filter on results. Post-filtering silently truncates context for exactly
the users with the narrowest permissions (their top-50 becomes a top-3), and it
puts a confidential chunk one refactor away from reaching a prompt. `Principal`
is a required argument to retrieval, so there is no code path that retrieves
without one. The eval harness re-implements the access check independently, so
a regression in the filter cannot pass its own test.

**Cost of the pre-filter:** ANN indices filter poorly. With HNSW, a highly
selective filter forces a much wider search to fill top-k. Mitigation at scale
is partitioning: one index per tenant, and coarse visibility tiers as separate
partitions, so the common case is an unfiltered scan of a smaller index.

### Diversity

A per-document cap (3 chunks) stops one verbose policy page from occupying the
whole context window. MMR is implemented and off by default: it helps on
"what changed" and survey questions and hurts on precise lookups, so it is a
per-query-class decision, not a global one.

---

## 5. Reranking

**Decision: LLM listwise reranking, batches of 20, over the top-40 → top-8,
fail-open.**

Retrieval optimises recall; reranking optimises precision. They are different
objectives, and that is the whole reason for two stages: the reranker is far too
expensive to run over 600k chunks, and the retriever is too blunt to order the
head of the list. Empirically this is the largest single quality win after
hybrid retrieval, because the answering model treats position as importance and
seven distractors around one correct chunk measurably degrade answers.

Implementation details that matter:

* **Listwise in batches, not pointwise.** Pointwise scoring gives poorly
  separated scores and costs one call per candidate. Fully listwise over 40
  candidates degrades in the middle of the list and blows the snippet budget.
* **Scores, not an ordering.** Orderings cannot be merged across batches.
  Scores are z-normalised within each batch before merging, because a batch the
  model happened to score generously would otherwise sweep the top.
* **Fail open.** A reranker timeout returns fusion order with a metric
  incremented. A slightly worse answer beats a 500. This is configurable, and
  the only reason it is configurable is that a compliance-grade deployment might
  legitimately prefer to fail.
* **Cache on (query, candidate id set).** Repeated questions are the norm.

**Alternative: a hosted cross-encoder** (Cohere Rerank, Voyage rerank, or a
self-hosted bge-reranker). ~50 ms instead of ~2 s, and roughly 100× cheaper. It
is the right call once volume is real; the LLM reranker's advantage is that it
handles instruction-shaped relevance ("prefer the current policy over the
superseded one") that a cross-encoder cannot express. The honest framing: the
LLM reranker is the quality ceiling and the reference implementation, the
cross-encoder is what you ship at 8k questions/day. See §8.

---

## 6. Grounding and citations

**Decision: numbered context blocks, model instructed to cite per sentence,
citations verified in code, one corrective retry, then abstention.**

Asking the model to cite is not the same as it citing correctly. Three failure
modes are caught deterministically on every request:

| Failure | Detection | Response |
| --- | --- | --- |
| Hallucinated marker (`[9]` of 8 sources) | id not in the map | drop the citation, count it |
| Uncited claim | claim sentence with no marker | lowers grounding score |
| Mis-attribution | near-zero content overlap with the cited chunk | flagged weak |

The **grounding score** is the fraction of claim sentences carrying a valid
citation. Below 0.6 the system re-asks once with a stricter instruction and
keeps the better of the two; if that fails it degrades the answer to
low-confidence rather than presenting it as verified. Sentence classification is
deliberately conservative — short sentences, list markers, and closing lines do
not need citations, because demanding a citation on "Yes." teaches nothing.

The lexical mis-attribution check is intentionally permissive (25% content-word
overlap). It exists to catch citations pointing at unrelated passages, not to
adjudicate paraphrase; tightening it turns correct abstractive answers into
false alarms. The semantic version of the same check is the LLM judge, which
runs in eval rather than in the request path.

**Abstention** is a first-class outcome with its own token (`INSUFFICIENT_
CONTEXT`), its own metric, and a fast path: if retrieval returns nothing the
user may see, the system abstains without paying for a generation call.

**Streaming.** Verification is necessarily post-hoc — you cannot check a
half-written sentence. Tokens stream optimistically; the final SSE event carries
the validated citation list and grounding score, and the client marks the text
unverified if it failed. That is a deliberate UX trade: perceived latency beats
a 6-second blank screen, and the reconciliation event closes the correctness gap.

**Prompt caching.** The system prompt is byte-stable and carries a cache
breakpoint; question and context go in the user turn, after it. Caching is a
prefix match, so a single interpolated timestamp at the top would silently
invalidate every request — this is why nothing volatile is allowed in the system
block. At Opus 5's 512-token minimum, the ~700-token instruction block caches,
cutting roughly 10% off input cost and a little off TTFT.

---

## 7. Evaluation

Nothing else in this document is defensible without this section. Prompt
changes, chunk-size changes, and model upgrades all look fine in a spot check
and all regress silently.

### The golden set

~16 cases in this repository, 200–500 in a real deployment, versioned in git
next to the code, deliberately mixing four kinds:

* **single-hop** — the fact exists in one place
* **multi-hop** — needs two documents combined
* **unanswerable** — the corpus genuinely does not say; correct behaviour is
  abstention, and this is the *only* way to measure hallucination pressure
* **permission-scoped** — answerable for one group, must abstain for another

Labels are at **document** granularity. Chunk-level labels break the moment the
chunker changes, which is exactly when you most need the numbers to stay
comparable.

### Metrics, per stage

| Stage | Metric | Why this one |
| --- | --- | --- |
| Retrieval | recall@40 (candidates) | The ceiling. If the document is not here, nothing downstream can recover it. Look at this first when answers are wrong. |
| Rerank | nDCG@8, MRR | Whether the right chunk reached a position the model will use. |
| Generation | grounding score (deterministic) | Citation discipline, no LLM required, runs in prod too. |
| Generation | faithfulness (LLM judge, per claim) | Semantic entailment against *cited* passages only. |
| Generation | correctness (LLM judge vs reference) | Whether the answer is actually right. |
| Behaviour | abstention recall, false-abstention rate | The two sides of "knows what it doesn't know". A system that abstains on everything scores perfectly on one and terribly on the other. |
| Safety | permission leaks | Hard gate at zero. |
| Ops | p50/p95 latency, tokens, USD/query | Regressions here are as real as quality regressions. |

### On the LLM judge

The judge is a measuring instrument and gets treated like one:

* **Per claim, not per answer.** "Rate faithfulness 1-5" returns 4 for
  everything. Splitting into claims and asking supported/unsupported/contradicted
  produces a number that moves when quality moves.
* **The judge sees only the cited passages.** Give it the full retrieved set and
  it credits claims to passages the answer never cited — mis-citation becomes
  invisible.
* **Structured output**, so parse failures do not correlate with hard cases.
* **Validate against humans before trusting it.** `agreement_with_humans()` on a
  50-case hand-labelled subset; below ~80% agreement the judge is measuring its
  own preferences and no threshold built on it means anything.
* Judge and answerer being the same model family is a real self-preference risk.
  Mitigation: the human-agreement check above, plus periodic spot audits.

### Gating

`scripts/run_eval.py --strict` exits non-zero on threshold failure and runs in
CI on every change to a prompt, a chunking parameter, a fusion weight, or a
model id. Thresholds start at the measured baseline and ratchet — a threshold
that has never failed is not protecting anything. Ablation flags (`--no-rerank`,
`--fusion weighted`, `--final-k`) exist because the only way to justify a stage
is to show the numbers without it.

### Online, not just offline

Offline eval on 300 cases does not tell you what 8,000 daily questions do.
Production adds: thumbs up/down joined to trace ids, citation click-through
(a citation nobody clicks is decoration), abstention rate by topic (a spike is
usually a broken connector, not a model regression), and question clustering to
find the recurring questions the corpus cannot answer — which is a documentation
backlog, and one of the more valuable outputs of the whole system.

---

## 8. Cost and latency

Per answered question, at the default config (8 contexts × ~500 tokens, ~700
token cached system prompt, ~350 token answer), Claude Opus 5 list price
($5/$25 per MTok):

| Stage | Tokens in / out | Cost |
| --- | --- | --- |
| Rerank (40 candidates, 2 batches, Opus 5, effort low) | ~11,000 / ~600 | ~$0.070 |
| Answer (Opus 5, cached prefix) | ~5,500 / ~350 | ~$0.036 |
| Embedding (1 query) | — | ~$0.00001 |
| **Total** | | **~$0.106** |

That is **2× the $0.05 ceiling, and reranking is two thirds of it.** Three
levers, in the order I would pull them:

1. **Move reranking to Claude Haiku 4.5** ($1/$5 per MTok): ~$0.014 instead of
   ~$0.070, total ~$0.05. Reranking is a bounded judgement task, which is the
   profile Haiku handles well — but this is an eval question, not an opinion,
   and the ablation is one flag.
2. **Move reranking to a hosted cross-encoder**: ~$0.0005 and ~50 ms instead of
   ~2 s. Loses instruction-shaped relevance; measure before committing.
3. **Semantic caching** of question → answer. Internal corpora have a heavy
   head: the same twenty policy questions are a large share of traffic. Keyed on
   normalised question + principal's access profile (never a shared cache across
   permission scopes) with invalidation on any cited document changing.

Latency budget, p95:

| Stage | Budget |
| --- | --- |
| Embed query | 40 ms |
| ANN + BM25 (parallel) | 60 ms |
| Fusion, cap, MMR | 5 ms |
| LLM rerank (2 batches, parallel) | 2,000 ms |
| Answer TTFT | 1,500 ms |
| Answer completion | 4,000 ms |

First token ~3.6 s p95 — over the 2.5 s target, and the reranker is again the
culprit. A cross-encoder brings it to ~1.7 s. The alternative, streaming before
reranking finishes, is not available: the reranker decides what goes in the
prompt.

---

## 9. Failure modes and degradation

The system is designed so that every dependency failure degrades rather than
500s, and every degradation is visible in metrics.

| Failure | Behaviour | Signal |
| --- | --- | --- |
| Reranker timeout / error | fusion order, top-8 | `rerank_failures_total` |
| Dense index unavailable | BM25-only answers | recall drop, channel counter |
| BM25 unavailable | dense-only; exact-code queries degrade | recall drop |
| Embedding provider down | queries fail on dense, ingest queues | ingest backlog age |
| Model refusal (`stop_reason: refusal`) | server-side fallback model, then a clean user-facing message | `llm_refusals_total` |
| Model returns ungrounded text | one stricter retry, then low-confidence or abstain | `answers_retried_total`, grounding histogram |
| Nothing retrievable for this principal | abstain without a generation call | `retrieval_empty_total` |
| Connector delivers a corrupt document | that document is skipped, the sync continues | `ingest_errors_total` |

The two that would actually page someone: a spike in `retrieval_empty_total`
(usually a broken connector or an ACL change, not a model problem) and a drop in
the grounding histogram (usually a prompt or model change).

---

## 10. Security and multi-tenancy

* **Tenant id comes from the verified session, never from a client-supplied
  field.** Header-trusted tenant ids are a cross-tenant leak with extra steps;
  the header form in `service/app.py` is demo scaffolding and is labelled as
  such.
* **Prompt injection.** Retrieved documents are untrusted input — anyone who can
  edit a wiki page can put "ignore your instructions" in it. Mitigations here:
  context is delimited and labelled as source material, the system prompt states
  that sources are evidence and not instructions, and the answer path has no
  tools and no side effects, so the blast radius of a successful injection is a
  bad answer rather than an action. If tool use is added later, that changes and
  the injection surface needs re-analysis, not a patch.
* **Deletion.** A document deleted at source must leave the index. Deletion is
  as cheap and reliable as insertion, and reconciliation sweeps catch what
  webhooks miss — a deleted document still answering questions is a compliance
  incident, not a quality issue.
* **PII and logging.** Questions are logged; question *text* is the sensitive
  part (people ask about their own pay, leave, and disputes). Trace ids
  everywhere, question bodies behind a retention policy and access control.
* **The confused-deputy risk of citations.** Citations must resolve to documents
  the *asking user* can open. Citing a URL that 403s tells the user the document
  exists and roughly what it says — a small but real leak. Citations are drawn
  only from the already-filtered context set.

---

## 11. Scaling

| Dimension | Now | At 10× | What changes |
| --- | --- | --- | --- |
| Chunks | 600k | 6M | In-memory → pgvector/HNSW, then a partitioned managed store. 6M × 1024 dims × 4 B ≈ 24 GB, so it stops fitting comfortably in one process. |
| Lexical | in-process BM25 | OpenSearch | Same interface, same tuning constants. |
| Ingest | synchronous | queue + workers, partitioned by source | Per-source rate limits and backpressure; embedding is the bottleneck, not chunking. |
| Query | one process | stateless, horizontally scaled | State is the indices and the embedding cache. |
| Reranking | LLM | cross-encoder | The one change that alters both the cost and the latency budget materially. |

Freshness at scale: webhooks where the source supports them, polling where it
does not, plus a nightly reconciliation pass. The content-hash diff means the
nightly pass is cheap even at 6M chunks — the expensive part (embedding) only
touches what actually changed.

---

## 12. What I would build next, in order

1. **Cross-encoder reranking**, behind the existing `Reranker` protocol, gated
   on the eval showing parity. Fixes both the cost and the latency miss.
2. **Query understanding.** Acronym and alias expansion from a curated glossary,
   and decomposition for multi-hop questions ("what do I need before booking a
   conference" → two retrievals). Cheapest remaining recall win.
3. **Contextual retrieval proper** — an LLM-generated one-line context per chunk
   at ingest. Published results put it well ahead of the heading-path
   approximation used here; it costs one call per chunk, which is affordable at
   40k documents precisely *because* ingestion is incremental.
4. **Semantic answer cache**, scoped per access profile.
5. **Freshness weighting in fusion.** Recency currently only reaches the model
   as metadata; a mild recency prior in the fusion score would resolve
   superseded-policy conflicts before they reach the prompt.
6. **Feedback loop into the golden set.** Thumbs-down with a human-written
   correct answer is the highest-quality eval data available, and it is free.
