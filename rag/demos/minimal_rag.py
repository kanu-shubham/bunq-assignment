#!/usr/bin/env python3
"""A complete RAG retrieval pipeline in one file. No framework, numpy only.

    python demos/minimal_rag.py

`ragkit/` is the production-shaped version: swappable backends, config objects,
protocols, an evaluation harness. Good for measuring a system, too much surface
area for understanding one.

This is the same pipeline distilled to the smallest honest implementation —
every stage from raw documents to a cited answer, in the order it executes, with
nothing you could delete and still have RAG. Read it top to bottom; each section
is one concept.

  1. documents        the input
  2. chunking         documents -> retrievable pieces
  3. tokenising       text -> words
  4. BM25             the sparse index (exact word matching)
  5. embeddings       the dense index (approximate matching)
  6. fusion           two ranked lists -> one
  7. re-ranking       shortlist -> reordered, using joint features
  8. answering        top chunks -> cited answer, quotes verified
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict

import numpy as np

# ============================================================== 1. DOCUMENTS

DOCS = [
    ("DOC-A", "Deploy and rollback", """## Manual rollback
Run `kubectl argo rollouts undo` to restore the previous release. Rollback takes
about ninety seconds.
## What rollback does not cover
Rollback does not undo database migrations. Every migration must be
backwards-compatible with the previous release."""),
    ("DOC-B", "Database migrations", """## Writing a migration
Split every schema change into an additive step and a later removal step. This
is called expand and contract.
## Reverting a migration
A migration rollback is a new forward migration, never an edit."""),
    ("DOC-C", "On-call rotation", """## The rotation
Rotations are weekly, with a primary and a secondary engineer.
## Response expectations
The primary is paged first and acknowledges within five minutes. The secondary
is paged automatically after ten minutes."""),
]

# ================================================================ 2. CHUNKING
# Split on headings, not on character counts. A chunk must make sense alone,
# and it keeps its heading path so a query matching only the heading can
# still find it.


def chunk(docs):
    """-> list of (chunk_id, doc_id, heading_path, body)"""
    out = []
    for doc_id, title, text in docs:
        heading, buf = "", []

        def flush():
            if buf and " ".join(buf).strip():
                out.append((f"{doc_id}#{len(out):03d}", doc_id,
                            f"{title} > {heading}".strip(" >"), " ".join(buf).strip()))

        for line in text.splitlines():
            if line.startswith("#"):
                flush()
                buf.clear()
                heading = line.lstrip("# ").strip()
            elif line.strip():
                buf.append(line.strip())
        flush()
    return out


# ============================================================== 3. TOKENISING
# Keep compound identifiers AND their parts: ERR_CURRENCY_MISMATCH must survive
# as one token, or the one query that should be trivial becomes impossible.

WORD = re.compile(r"[A-Za-z0-9]+(?:[._\-/][A-Za-z0-9]+)*")
STOP = set("a an the and or but if then of in on at to for from by with as is "
           "are was were be do does did it its not no this that".split())


def tokenize(text):
    out = []
    for m in WORD.finditer(text.lower()):
        tok = m.group(0)
        parts = [p for p in re.split(r"[._\-/]", tok) if p]
        if len(parts) > 1:
            out.append(tok)          # the compound, for exact matching
            out.extend(parts)        # and the parts, for partial matching
        else:
            out.extend(parts)
    return [t for t in out if t not in STOP and len(t) > 1]


# =================================================================== 4. BM25
# The sparse index. An inverted index (word -> which chunks, and how often)
# plus Okapi BM25 scoring: tally x rarity, with two corrections.


class BM25:
    def __init__(self, corpus, k1=1.5, b=0.75, prefix_weight=2):
        # Heading words are indexed twice: a match in a heading is a better
        # clue than a match buried in a paragraph.
        self.docs = [tokenize(body) + tokenize(prefix) * prefix_weight
                     for _, _, prefix, body in corpus]
        self.k1, self.b = k1, b
        self.n = len(self.docs)
        self.dl = np.array([len(d) for d in self.docs], dtype=np.float32)
        self.avgdl = float(self.dl.mean())

        self.postings = defaultdict(list)      # word -> [(chunk, tally), ...]
        df = Counter()
        for i, d in enumerate(self.docs):
            for term, tf in Counter(d).items():
                self.postings[term].append((i, tf))
                df[term] += 1
        # IDF: the rarity bonus. In every chunk -> ~0. In one chunk -> large.
        self.idf = {t: math.log(1 + (self.n - c + 0.5) / (c + 0.5))
                    for t, c in df.items()}

    def scores(self, query):
        s = np.zeros(self.n, dtype=np.float32)
        for term in tokenize(query):
            if term not in self.postings:
                continue                        # unknown word contributes nothing
            for i, tf in self.postings[term]:
                # tf in numerator AND denominator -> diminishing returns (k1)
                # dl/avgdl -> long chunks discounted (b)
                denom = tf + self.k1 * (1 - self.b + self.b * self.dl[i] / self.avgdl)
                s[i] += self.idf[term] * tf * (self.k1 + 1) / denom
        return s


# ============================================================= 5. EMBEDDINGS
# The dense index. Every chunk becomes a point; similar chunks land near each
# other. Built from character n-grams, so 'rollbacks' lands next to 'rollback'
# even though as strings they never match.
#
# A trained bi-encoder (all-MiniLM-L6-v2) also learns meaning, so it would
# additionally place 'PTO' near 'annual leave'. This one cannot. Same idea,
# better map.

DIM = 512


def _hash(feature):
    h = hashlib.blake2b(feature.encode(), digest_size=9).digest()
    idx = int.from_bytes(h[:8], "big") % DIM
    sign = 1.0 if h[8] & 1 else -1.0          # signed, so collisions cancel
    return idx, sign


def ngrams(word, sizes=(3, 4, 5)):
    padded = f"#{word}#"                       # boundary marks: 'lag' != 'flagged'
    return [padded[i:i + n] for n in sizes for i in range(len(padded) - n + 1)]


class Embedder:
    def __init__(self, idf):
        self.idf = idf
        self.default = max(idf.values(), default=1.0)

    def encode(self, text):
        v = np.zeros(DIM, dtype=np.float32)
        for term, tf in Counter(tokenize(text)).items():
            w = (1 + math.log(tf)) * self.idf.get(term, self.default)
            i, s = _hash(f"w:{term}")
            v[i] += s * w                       # the whole word
            grams = ngrams(term)
            for g in grams:                     # and its pieces
                gi, gs = _hash(f"c:{g}")
                v[gi] += gs * w * 0.8 / math.sqrt(len(grams))
        norm = np.linalg.norm(v)
        return v / norm if norm else v          # unit length -> cosine == dot


# ================================================================= 6. FUSION
# Reciprocal Rank Fusion. BM25 is unbounded, cosine is [-1,1]; adding them is
# meaningless. So discard the scores and keep only positions.
#
# The +60 flattens position differences, which makes appearing in BOTH lists
# matter more than topping one. That is the point: agreement between two
# independent retrievers beats one retriever's confidence.


def rrf(rankings, k=60.0):
    fused = defaultdict(float)
    for ranking in rankings:
        for rank, chunk_idx in enumerate(ranking, start=1):
            fused[chunk_idx] += 1.0 / (k + rank)
    return sorted(fused, key=lambda i: -fused[i])


# ============================================================== 7. RE-RANKING
# The joint stage. BM25 sums each query word independently, so it cannot tell
# 'idempotency key reuse' as a phrase from those words scattered across three
# paragraphs. This looks at query and chunk together.
#
# A real cross-encoder concatenates them into one model input and reads a single
# relevance number off the [CLS] slot. These hand-computed features approximate
# what such a model learns to attend to.


def rerank(query, candidates, corpus, idf, alpha=0.4):
    q = tokenize(query)
    q_uniq = list(dict.fromkeys(q))
    total_idf = sum(idf.get(t, 3.0) for t in q_uniq) or 1.0

    raw = []
    for idx in candidates:
        _, _, prefix, body = corpus[idx]
        doc = tokenize(body)
        doc_set, pre_set = set(doc), set(tokenize(prefix))

        matched = [t for t in q_uniq if t in doc_set]
        coverage = sum(idf.get(t, 3.0) for t in matched) / total_idf
        title = sum(idf.get(t, 3.0) for t in q_uniq if t in pre_set) / total_idf

        phrase = 0.0                            # is the exact phrase present?
        for n in range(min(4, len(q)), 1, -1):
            if any(doc[i:i + n] == q[j:j + n]
                   for j in range(len(q) - n + 1)
                   for i in range(len(doc) - n + 1)):
                phrase = n / min(4, len(q))
                break

        pos = [i for i, t in enumerate(doc) if t in set(matched)]
        prox = len(matched) / max(len(matched), pos[-1] - pos[0] + 1) if pos else 0.0

        raw.append(2.0 * coverage + 2.6 * phrase + 1.2 * prox + 1.3 * title)

    # Blend with the first-stage rank. Sorting on these features ALONE throws
    # away everything the dense index knew, and drops correct documents out of
    # the top ten on paraphrase queries. Re-ranking refines retrieval; it does
    # not overrule it.
    def norm(xs):
        lo, hi = min(xs), max(xs)
        return [1.0] * len(xs) if hi - lo < 1e-9 else [(x - lo) / (hi - lo) for x in xs]

    prior = norm([1.0 / (1 + math.log1p(r)) for r in range(1, len(candidates) + 1)])
    blended = [alpha * p + (1 - alpha) * c for p, c in zip(prior, norm(raw))]
    order = sorted(range(len(candidates)), key=lambda i: -blended[i])
    return [candidates[i] for i in order]


# =============================================================== 8. ANSWERING
# Retrieval only ever SORTS documents — no stage above produces text. This is
# where an answer appears.
#
# Offline here: pick the best-matching sentences and cite them, so the answer
# cannot contain a claim the corpus does not make. Swap in an LLM prompt and the
# verification step below is what keeps it honest.

SENT = re.compile(r"(?<=[.!?])\s+")


def answer(query, ranked, corpus, idf, top=3, floor=0.35):
    q = set(tokenize(query))

    # Normalise by the query terms that EXIST in the corpus, not by all of them.
    # Dividing by all of them punishes a sentence for words nothing contains —
    # 'how', 'roll' and 'back' are absent here, so with the naive denominator no
    # sentence could ever clear the floor and every query abstained.
    total = sum(idf[t] for t in q if t in idf) or 1.0

    scored = []
    for n, idx in enumerate(ranked[:top], start=1):
        pre_terms = set(tokenize(corpus[idx][2]))
        for sent in SENT.split(corpus[idx][3]):
            sent_terms = set(tokenize(sent))
            hit = sum(idf[t] for t in q if t in idf and t in sent_terms)
            # Credit the chunk's heading at half weight: 'deploy' lives only in
            # the heading here, and a sentence under that heading is on-topic
            # even when the sentence itself never repeats the word.
            hit += 0.5 * sum(idf[t] for t in q
                             if t in idf and t in pre_terms and t not in sent_terms)
            if hit > 0:
                scored.append((hit / total, n, idx, sent.strip()))
    scored.sort(reverse=True)

    if not scored or scored[0][0] < floor:
        return "I don't have that documented.", []      # abstain, don't guess

    picked = [s for s in scored[:3] if s[0] >= scored[0][0] * 0.6]
    text = " ".join(f"{s[3]} [{s[1]}]" for s in sorted(picked, key=lambda s: s[1]))

    # Verify every quote against the chunk it claims. A citation that does not
    # verify is dropped — this is a check, not a request to the model.
    cites = []
    for _, n, idx, sent in sorted(picked, key=lambda s: s[1]):
        ok = sent.lower().replace("\n", " ") in corpus[idx][3].lower().replace("\n", " ")
        cites.append((n, corpus[idx][1], ok))
    return text, cites


# ==================================================================== RUN IT


def main():
    corpus = chunk(DOCS)
    bm25 = BM25(corpus)
    embedder = Embedder(bm25.idf)
    matrix = np.vstack([embedder.encode(f"{p} {b}") for _, _, p, b in corpus])

    print(f"{len(DOCS)} documents -> {len(corpus)} chunks, "
          f"{len(bm25.postings)} unique terms, vectors {matrix.shape}")

    for query in ["how do I roll back a deploy",
                  "deploymnt rollbacks",
                  "who is paged at night"]:
        print(f"\n{'=' * 70}\nQUERY: {query!r}\n{'=' * 70}")

        # --- retrieve: both indexes, independently -----------------------
        lex = bm25.scores(query)
        lex_rank = [i for i in np.argsort(-lex) if lex[i] > 0][:10]

        vec = matrix @ embedder.encode(query)
        vec_rank = [i for i in np.argsort(-vec) if vec[i] > 0][:10]

        print(f"  BM25  : {[corpus[i][0] for i in lex_rank] or 'NOTHING'}")
        print(f"  dense : {[corpus[i][0] for i in vec_rank]}")

        # --- fuse --------------------------------------------------------
        fused = rrf([lex_rank, vec_rank])
        print(f"  fused : {[corpus[i][0] for i in fused[:5]]}")

        # --- re-rank -----------------------------------------------------
        final = rerank(query, fused[:10], corpus, bm25.idf)
        print(f"  final : {[corpus[i][0] for i in final[:3]]}")

        # --- answer ------------------------------------------------------
        text, cites = answer(query, final, corpus, bm25.idf)
        print(f"\n  ANSWER: {text}")
        for n, doc_id, ok in cites:
            print(f"    [{n}] {doc_id}  {'verified' if ok else 'UNVERIFIED'}")

    print(f"\n{'=' * 70}")
    print("Two things in that output are the whole lesson:")
    print()
    print("QUERY 2 — BM25 returns NOTHING. Both tokens are absent from the corpus,")
    print("so no lookup matched and scoring never ran. Dense recovers the right")
    print("chunk from shared character n-grams. That is the case for hybrid.")
    print()
    print("QUERY 2 also ABSTAINS at the answer step, even though retrieval found")
    print("the right chunk. That is honest, not broken: this answerer works by")
    print("word overlap, and NO query word exists in the corpus, so it cannot")
    print("verify that any sentence answers the question. An LLM generator would")
    print("handle it — it knows 'deploymnt' means 'deployment'. Which is the real")
    print("division of labour: retrieval tolerates typos, extraction does not,")
    print("and generation is what bridges the gap.")


if __name__ == "__main__":
    main()
