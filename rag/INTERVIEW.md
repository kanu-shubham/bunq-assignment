# Staff-level RAG interview — the condensed version

Everything here is backed by [DESIGN.md](./DESIGN.md) and by running code in
`src/ragx/`. This file is the whiteboard script and the answers to the questions
this design invites.

---

## 1. The 90-second opening

> "Two pipelines. **Ingest** is async: connectors give me documents plus their
> ACL and last-modified time; a structure-aware chunker splits on headings and
> never splits a table or a code block; chunk ids are derived from content
> hashes, so re-ingest only embeds what changed. **Query** is sync: hybrid
> retrieval — dense top-50 and BM25 top-50, both with the ACL applied *before*
> top-k — fused with RRF, capped per document to top-40, LLM-reranked to top-8,
> then a grounded prompt with numbered sources. The model cites per sentence,
> and I verify those citations in code: hallucinated markers dropped, uncited
> claims scored, one corrective retry, then abstain. The whole thing is gated by
> an eval harness in CI that measures each stage separately, including
> abstention and permission leaks."

Then stop and let them pick a thread. Do not narrate all six sections.

## 2. Draw this

```
docs → chunk(heading-aware, atomic tables/code, content-hash ids)
     → embed(only changed) → [vector | BM25]

query + principal
     → dense top-50 ─┐  ACL PRE-filter on both
     → BM25  top-50 ─┴→ RRF → per-doc cap → top-40
     → LLM rerank (batched, fail-open) → top-8
     → grounded prompt (cached system prefix) → Claude Opus 5 (stream)
     → verify citations → answer | low-confidence | abstain
```

The three arrows to emphasise while drawing: **ACL before top-k**, **fusion of
ranks not scores**, **verification after generation**.

## 3. The numbers to have ready

| | |
| --- | --- |
| Chunk target / overlap | 512 tokens / 64, overlap within a section only |
| Candidates → final | 50+50 → RRF → 40 → rerank → 8 |
| RRF k | 60 (insensitive; that's the point) |
| BM25 | k1=1.2, b=0.75 |
| Cost/query, Opus 5 rerank + answer | ~$0.106 — **reranking is 2/3 of it** |
| Same with Haiku 4.5 reranking | ~$0.05 |
| Same with a cross-encoder | ~$0.037, and −2 s latency |
| Latency p95 | TTFT ~3.6 s (rerank is ~2 s of it) |
| Corpus assumed | 40k docs / 600k chunks / 8k questions per day |
| Prompt cache | system block cached; Opus 5 minimum is 512 tokens |
| Model pricing | Opus 5 $5/$25 per MTok; cache reads ~0.1× input |

Volunteering "reranking is two thirds of my cost and I'd move it to Haiku or a
cross-encoder, gated on the eval" is worth more than any other single sentence
in this interview: it shows the design has been costed, not just drawn.

## 4. Questions they will ask

**"Why not just fixed-size chunks?"**
Because a split table is worse than no table — it retrieves and then misinforms
— and because a chunk that lost its heading is unretrievable: "it must be
approved within 30 days" matches nothing. I keep the heading path as metadata
*and* prepend it to the embedded text.

**"Why hybrid? Isn't a good embedding model enough?"**
Not for this corpus. `ERR_ACCT_4032`, ticket ids, internal product names, and
anything coined after the model's cutoff are exact-match problems; BM25 solves
them for free. Dense handles the paraphrase queries BM25 can't. The failure
modes barely overlap, which is exactly when fusion pays.

**"Why RRF instead of tuning weights?"**
BM25 scores are unbounded and corpus-dependent, cosine sits in a narrow band.
Normalising them means re-tuning whenever the corpus or the embedding model
changes. RRF fuses ranks, has one insensitive parameter, and needs no labelled
data. Once the eval set is large enough to tune on, a weighted blend beats it by
a few nDCG points — that path is in the code behind a flag.

**"Do you really need a reranker?"**
Retrieval optimises recall; reranking optimises precision. Different objectives,
which is why there are two stages: the reranker is too expensive to run over
600k chunks, the retriever too blunt to order the head. The ablation is one
flag (`--no-rerank`) and the eval prints nDCG@8 with and without.

**"How do you stop it hallucinating?"**
Four layers, and only the first is prompting. (1) Instruct per-sentence
citation with an explicit abstention token. (2) Verify markers in code —
hallucinated ids dropped, uncited claim sentences counted; that fraction is the
grounding score. (3) One stricter retry below threshold, then degrade to
low-confidence or abstain. (4) An LLM judge per claim in the eval loop, against
the *cited* passages only. And the fast path: if nothing retrievable is
permitted, abstain without paying for generation.

**"How do you evaluate this?"**
Per stage, not end-to-end. recall@40 is the ceiling — if the doc isn't in the
candidate set nothing downstream recovers it. nDCG@8 and MRR judge the
reranker. Grounding score is deterministic and runs in production too.
Faithfulness and correctness come from an LLM judge. Plus abstention recall and
false-abstention rate as a pair, and permission leaks gated at zero. Golden set
in git, labels at *document* granularity so they survive a chunker change, CI
fails on a threshold miss.

**"How do you know the judge is any good?"**
I don't, until it's checked. `agreement_with_humans()` on a 50-case hand-labelled
subset; below ~80% agreement the judge is measuring its own taste and any
threshold built on it is theatre. Judging per claim rather than rating the whole
answer 1–5 is what makes the number move at all. Same-family judge and answerer
is a self-preference risk I mitigate with that check plus spot audits.

**"How do permissions work?"**
The filter runs *inside* both index scans, before top-k, and `Principal` is a
required argument to retrieval so there's no path that skips it. Post-filtering
silently truncates context for the most restricted users and leaves a
confidential chunk one refactor from a prompt. The eval re-implements the access
check independently — an eval that uses the system's own filter can't catch a
regression in that filter — and there's a test that monkeypatches the filter
away and asserts the eval goes red.

**"What happens when the reranker is down?"**
Fusion order, top-8, metric incremented, answer still served. Every dependency
degrades rather than 500s; the mode is configurable because a compliance-grade
deployment might prefer to fail.

**"Prompt injection?"**
Retrieved documents are untrusted input — anyone who can edit a wiki page can
write "ignore your instructions". Context is delimited and labelled as evidence,
and critically the answer path has no tools and no side effects, so a successful
injection produces a bad answer, not an action. Add tool use and that analysis
has to be redone, not patched.

**"Freshness?"**
Content-hash diff at chunk granularity: unchanged chunks aren't re-embedded,
edited ones get new ids, removed sections are deleted, deleted documents are
purged from both indices. Webhooks where the source supports them, polling where
it doesn't, nightly reconciliation. That's what makes a 15-minute freshness
target affordable.

**"Scale to 10×?"**
In-memory → pgvector/HNSW → partitioned managed store (6M chunks × 1024 dims ≈
24 GB); BM25 → OpenSearch; ingest → queue with per-source backpressure, since
embedding is the bottleneck, not chunking. One caveat worth raising unprompted:
ANN indices filter poorly, so a selective ACL pre-filter forces a wider search —
mitigation is partitioning by tenant and visibility tier.

## 5. Things to say unprompted

* "Chunking is the highest-leverage decision and the least glamorous."
* "Abstention is a feature with a metric, not a failure."
* "Recall@candidates is the ceiling — that's the first number I look at."
* "Reranking is two thirds of my cost; here's the ablation that decides it."
* "Labels at document granularity, so they survive a chunker change."
* "Post-filtering ACLs is a correctness bug, not an optimisation."

## 6. Trade-offs to concede honestly

| I chose | Cost | When I'd switch |
| --- | --- | --- |
| LLM reranker | ~2 s and ~$0.07/query | Cross-encoder as soon as volume is real and the eval shows parity |
| RRF | Leaves a few nDCG points on the table | Weighted fusion once the eval set is big enough to tune on |
| Heading-path contextualisation | Weaker than LLM-generated per-chunk context | Contextual retrieval proper — affordable *because* ingest is incremental |
| Local token estimator | Approximate | It's calibrated against `count_tokens` and biased to overcount; a network call per chunk boundary isn't viable |
| Lexical mis-attribution check | Catches unrelated citations, not paraphrase errors | It's deliberately permissive — the semantic version is the judge, in eval |
| Post-hoc verification when streaming | A wrong sentence can be on screen before it's flagged | Deliberate: TTFT beats a 6-second blank screen, and the final event reconciles |

## 7. If they ask you to code something live

Most likely asks, all already implemented and testable here:

* **RRF** — `index/hybrid.py::reciprocal_rank_fusion` (~10 lines)
* **BM25 scoring** — `index/bm25.py::search` (idf, tf saturation, length norm)
* **Citation verification** — `generate/citations.py::verify`; the subtle bug is
  the sentence splitter: `"...within 30 days. [1]"` naively splits into a
  sentence plus an orphan marker, and every correct answer scores zero grounding
* **recall@k / nDCG@k / MRR** — `eval/retrieval_metrics.py`
* **MMR** — `index/hybrid.py::mmr`
* **Incremental upsert** — `ingest/pipeline.py::_ingest_one` (diff by chunk id)
