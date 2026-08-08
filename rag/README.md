# ragx — RAG over company documentation

A production-shaped retrieval-augmented answering system: structure-aware
chunking, hybrid retrieval, LLM reranking, verified citations, abstention, and
an evaluation harness that gates CI.

* **[DESIGN.md](./DESIGN.md)** — the design, the trade-offs, and the numbers.
* **[INTERVIEW.md](./INTERVIEW.md)** — the same content condensed for a
  whiteboard, plus the questions this design invites and how they're answered.

Answer synthesis, reranking, and the eval judge run on **Claude Opus 5**.
Embeddings are provider-agnostic behind an `Embedder` protocol (Anthropic
doesn't serve embeddings); a hosted Voyage adapter and a deterministic offline
one both ship here.

## Run it

```bash
pip install -e '.[dev]'      # or: pip install anthropic fastapi uvicorn pytest httpx

python scripts/demo.py       # offline: no API key, no network
python -m pytest             # 74 tests, all offline
python scripts/run_eval.py   # golden-set eval + threshold report
```

CI (`.github/workflows/rag-eval.yml`) runs lint, tests, and
`run_eval.py --strict --gate retrieval`: recall, nDCG, MRR, and permission leaks
fail the build offline; generation metrics are reported but need `--live
--judge`, so they run on a schedule rather than burning tokens on every PR.

**Offline mode** swaps in a hashing embedder, a lexical reranker, and a scripted
model, so the entire path — chunking, hybrid retrieval, ACL filtering, citation
verification, grounding scoring, the eval harness — runs and is assertable with
no credentials. Answer *quality* in offline mode is meaningless; answer *shape*
and every retrieval number are real.

With `ANTHROPIC_API_KEY` set (or after `ant auth login`):

```bash
python scripts/demo.py --live "How long do I have to submit an expense claim?"
python scripts/run_eval.py --live --judge --strict
uvicorn ragx.service.app:app --app-dir src --reload
```

## The service

```bash
curl -s localhost:8000/ask \
  -H 'content-type: application/json' \
  -H 'x-tenant-id: acme' -H 'x-groups: engineering' \
  -d '{"question":"Who do I page for a Sev-1 at 3am?"}'
```

| Endpoint | Purpose |
| --- | --- |
| `POST /ask` | answer + validated citations + grounding score + stage timings |
| `POST /ask/stream` | SSE: `context` → `token`* → `done` (the `done` event carries the verified result) |
| `POST /ingest` | index a directory; incremental — unchanged chunks are not re-embedded |
| `DELETE /documents` | purge documents from both indices |
| `GET /healthz` | index size, models in use |
| `GET /metrics` | Prometheus exposition |

The principal (tenant, groups, visibility ceiling) comes from headers here for
demo purposes only. In production it comes from a verified session — a
header-trusted tenant id is a cross-tenant leak with extra steps.

## Layout

```
src/ragx/
├── types.py            frozen domain types: Chunk, ScoredChunk, Answer, Principal
├── config.py           every quality-affecting knob, snapshotted into eval reports
├── tokens.py           local token estimator (calibrated against count_tokens)
├── llm.py              Claude access: caching, refusals+fallbacks, streaming, a test fake
├── ingest/
│   ├── loaders.py      documents + ACL + updated_at out of a source
│   ├── chunker.py      heading-aware, atomic tables/code, deterministic ids
│   └── pipeline.py     content-hash diff → embed only what changed
├── embed/              Embedder protocol, caching wrapper, hashing + Voyage impls
├── index/
│   ├── vector_store.py dense search + AccessFilter (applied *before* top-k)
│   ├── bm25.py         BM25 Okapi, identifier-aware tokenizer
│   └── hybrid.py       RRF / weighted fusion, per-doc cap, MMR
├── retriever.py        the retrieval stage
├── rerank/             Reranker protocol; LLM listwise reranker (batched, fail-open)
├── generate/
│   ├── prompt.py       grounded prompt, cache-stable system block
│   ├── answerer.py     synthesis, one corrective retry, abstention, streaming
│   └── citations.py    marker validation, grounding score, quote extraction
├── eval/               golden set, retrieval metrics, LLM judge, CI-gating runner
├── obs/                JSON traces, per-stage timings, metrics + cost accounting
└── service/app.py      FastAPI
corpus/                 six sample company documents (one confidential)
evalset/golden.jsonl    16 cases: single-hop, multi-hop, unanswerable, permission-scoped
```

## Current numbers (offline, sample corpus)

```
recall@candidates  1.00      grounding          0.60   ← offline stub "model"
recall@final       1.00      abstention_recall  0.00   ← offline stub "model"
ndcg@final         0.94      permission_leaks   0
mrr                0.92      latency p95        2.7 ms (no model calls)
```

Retrieval is measured for real and passes its thresholds. The two generation
metrics are red by construction: the offline stand-in model never abstains and
quotes a whole block as one "sentence". They are the metrics `--live --judge`
exists to measure, and leaving them visibly failing is more useful than a green
board that means nothing.

## The four decisions worth defending

1. **Structure-aware chunking over fixed-size splitting.** Heading path kept and
   prepended before embedding; tables and code never split; chunk ids derived
   from content so re-ingest is incremental.
2. **Hybrid retrieval fused with RRF.** Dense misses error codes and internal
   jargon; BM25 misses paraphrase. RRF needs no score calibration, which is what
   makes it survive a corpus or model change.
3. **Citations verified in code, not requested in the prompt.** Hallucinated
   markers dropped, uncited claims scored, one corrective retry, then abstain.
4. **An eval harness that gates CI, with abstention and permission leaks as
   first-class metrics.** Prompt, chunk-size, and model changes all regress
   silently otherwise.
