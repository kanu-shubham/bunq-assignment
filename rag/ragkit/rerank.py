"""Re-ranking: bi-encoders vs cross-encoders.

The distinction is the whole point of this stage.

A **bi-encoder** encodes the query and the document *independently* and compares
the two vectors. That independence is what makes it fast enough to index a
corpus — you embed every document once, offline — and it is also exactly what it
cannot do: the document vector was computed without ever seeing the query, so no
term-level interaction is modelled. Re-ranking with a bi-encoder is nearly free
and adds nearly nothing on top of a dense first stage, because it is the same
computation a second time. ``BiEncoderReranker`` is included so you can measure
that rather than take it on faith.

A **cross-encoder** scores the pair jointly: the query and the document go
through the model together, so attention runs across both. It can tell that
"idempotency key reuse returns 409" is answered by a passage where those terms
appear *near each other in that relation*, not merely a passage where each term
appears somewhere. The cost is that it cannot be precomputed — you pay a forward
pass per (query, candidate) pair at query time — which is why it re-ranks 50
candidates instead of scoring 200,000 chunks.

``LexicalCrossEncoder`` is a transparent, dependency-free stand-in for a trained
cross-encoder. It is not a neural model and does not pretend to be. What it does
is compute the *joint* features a trained cross-encoder learns to attend to —
IDF-weighted query coverage, exact phrase containment, term proximity, field of
match, position of first match — and combine them with hand-set weights. The
family of errors it fixes is the same family a trained cross-encoder fixes, so
the shape of the lesson holds; the magnitude would differ with a real model.
Weights were tuned on the dev split only (see ``data/eval/queries.jsonl``) and
all headline numbers are reported on the held-out test split.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable

import numpy as np

from .embeddings import Embedder
from .textutil import sentences, tokenize
from .types import ScoredChunk


@runtime_checkable
class Reranker(Protocol):
    def rerank(self, query: str, candidates: Sequence[ScoredChunk], k: int) -> list[ScoredChunk]: ...


class NoOpReranker:
    """Identity reranker — the baseline every other reranker must beat."""

    name = "none"

    def rerank(self, query: str, candidates: Sequence[ScoredChunk], k: int) -> list[ScoredChunk]:
        return list(candidates[:k])


class BiEncoderReranker:
    """Rescore candidates by query/document cosine, computed independently.

    Included as a control. Against a dense-retrieval first stage this reproduces
    the first stage's own ordering, which is the point: measure it, see that it
    barely moves, and understand why a cross-encoder is a different tool rather
    than a bigger one.
    """

    name = "bi-encoder"

    def __init__(self, embedder: Embedder) -> None:
        self.embedder = embedder

    def rerank(self, query: str, candidates: Sequence[ScoredChunk], k: int) -> list[ScoredChunk]:
        if not candidates:
            return []
        qvec = self.embedder.encode([query])[0]
        doc_matrix = self.embedder.encode([c.chunk.text for c in candidates])
        scores = doc_matrix @ qvec
        out = []
        for cand, score in zip(candidates, scores):
            sc = ScoredChunk(cand.chunk, float(score), dict(cand.components))
            sc.components["rerank_bi"] = float(score)
            out.append(sc)
        out.sort(key=lambda s: -s.score)
        return out[:k]


@dataclass(frozen=True)
class CrossEncoderWeights:
    """Feature weights. Tuned on the dev split; see module docstring.

    ``prior_weight`` deserves a note, because getting it wrong was the single
    biggest error in building this. A reranker that ignores the first stage
    *replaces* retrieval evidence with its own opinion. For features like these
    — which correlate strongly with the lexical index — that means throwing away
    the dense half of the hybrid and re-deciding on lexical grounds alone. On
    paraphrase queries, where dense retrieval was carrying the result, it drops
    the correct document out of the top ten entirely.

    So the final score is an interpolation, both terms min-max normalised within
    the candidate list: ``prior_weight`` on the first-stage rank and the
    remainder on the cross-encoder. Reranking is a refinement of retrieval, not
    a replacement for it. The value below was swept on the dev split.
    """

    coverage: float = 2.0
    phrase: float = 2.6
    proximity: float = 1.2
    title_field: float = 1.3
    first_position: float = 0.5
    fuzzy_coverage: float = 0.9
    late_interaction: float = 2.4
    length_penalty: float = 0.30
    prior_weight: float = 0.4  # in [0, 1]; 0 = ignore retrieval, 1 = ignore the reranker


def _minmax(values: Sequence[float]) -> list[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return [1.0] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def _min_covering_span(positions: list[tuple[int, str]], needed: int) -> int | None:
    """Smallest token window containing ``needed`` distinct query terms."""
    if needed <= 0 or len(positions) < needed:
        return None
    best = None
    counts: Counter[str] = Counter()
    distinct = 0
    left = 0
    for right, (pos_r, term_r) in enumerate(positions):
        counts[term_r] += 1
        if counts[term_r] == 1:
            distinct += 1
        while distinct >= needed:
            span = pos_r - positions[left][0] + 1
            if best is None or span < best:
                best = span
            term_l = positions[left][1]
            counts[term_l] -= 1
            if counts[term_l] == 0:
                distinct -= 1
            left += 1
    return best


def _has_phrase(doc_tokens: Sequence[str], phrase: Sequence[str]) -> bool:
    n = len(phrase)
    if n == 0 or n > len(doc_tokens):
        return False
    first = phrase[0]
    for i in range(len(doc_tokens) - n + 1):
        if doc_tokens[i] == first and list(doc_tokens[i : i + n]) == list(phrase):
            return True
    return False


class LexicalCrossEncoder:
    """Joint (query, passage) scorer built from interaction features."""

    name = "lexical-cross-encoder"

    def __init__(
        self,
        *,
        idf: dict[str, float] | None = None,
        default_idf: float = 3.0,
        weights: CrossEncoderWeights | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self.idf = idf or {}
        self.default_idf = default_idf
        self.w = weights or CrossEncoderWeights()
        self.embedder = embedder
        self._sentence_cache: dict[str, tuple[list[str], np.ndarray]] = {}

    @classmethod
    def from_embedder(cls, embedder: Embedder, **kwargs) -> "LexicalCrossEncoder":
        """Reuse IDF statistics already fitted by a ``HashingEmbedder``.

        The embedder is also kept for the late-interaction feature, which is the
        one feature here that is not a dressed-up lexical match.
        """
        idf = getattr(embedder, "_idf", None)
        default = getattr(embedder, "_default_idf", 3.0)
        kwargs.setdefault("embedder", embedder)
        return cls(idf=dict(idf) if idf else None, default_idf=float(default), **kwargs)

    def _idf(self, term: str) -> float:
        return self.idf.get(term, self.default_idf)

    def _late_interaction(self, qvec: np.ndarray | None, text: str, key: str | None) -> float:
        """Max cosine between the query and any single sentence of the passage.

        This is the cheap cousin of late interaction (the MaxSim in ColBERT).
        A whole-chunk embedding averages the relevant sentence together with
        four irrelevant ones, so a 200-word chunk containing the answer in one
        clause scores *below* a chunk that is vaguely on-topic throughout. That
        dilution is a real and well-known weakness of bi-encoder retrieval, and
        scoring the best sentence instead of the mean is what recovers it —
        which is why this feature contributes something the first stage did not
        already know, unlike the lexical features around it.
        """
        if self.embedder is None or qvec is None:
            return 0.0
        cached = self._sentence_cache.get(key) if key else None
        if cached is None:
            sents = [s for s in sentences(text) if len(s.split()) >= 4] or [text]
            matrix = self.embedder.encode(sents)
            cached = (sents, matrix)
            if key and len(self._sentence_cache) < 20000:
                self._sentence_cache[key] = cached
        sims = cached[1] @ qvec
        return float(np.max(sims)) if len(sims) else 0.0

    def score_pair(
        self,
        query: str,
        text: str,
        prefix: str = "",
        *,
        qvec: np.ndarray | None = None,
        cache_key: str | None = None,
    ) -> tuple[float, dict[str, float]]:
        q_tokens = tokenize(query)
        if not q_tokens:
            return 0.0, {}
        q_unique = list(dict.fromkeys(q_tokens))
        body_tokens = tokenize(text)
        prefix_tokens = set(tokenize(prefix))
        doc_set = set(body_tokens)

        total_idf = sum(self._idf(t) for t in q_unique) or 1.0

        # 1. IDF-weighted coverage: how much of the *informative* query is here.
        matched = [t for t in q_unique if t in doc_set]
        coverage = sum(self._idf(t) for t in matched) / total_idf

        # 2. Fuzzy coverage: unmatched query terms that share a long prefix with
        #    some document term. Catches morphology the exact match misses.
        unmatched = [t for t in q_unique if t not in doc_set]
        fuzzy_hits = 0.0
        for term in unmatched:
            if len(term) < 5:
                continue
            stem = term[: max(4, len(term) - 3)]
            if any(d.startswith(stem) for d in doc_set):
                fuzzy_hits += self._idf(term)
        fuzzy_coverage = fuzzy_hits / total_idf

        # 3. Exact phrase containment, longest first — the strongest single
        #    signal a cross-encoder has and one BM25 cannot express at all.
        phrase = 0.0
        for n in range(min(5, len(q_tokens)), 1, -1):
            grams = [q_tokens[i : i + n] for i in range(len(q_tokens) - n + 1)]
            if any(_has_phrase(body_tokens, g) for g in grams):
                phrase = n / min(5, len(q_tokens))
                break

        # 4. Proximity: are the matched terms close together or scattered?
        positions = [(i, t) for i, t in enumerate(body_tokens) if t in set(matched)]
        span = _min_covering_span(positions, len(matched))
        if span is None or not matched:
            proximity = 0.0
        else:
            ideal = len(matched)
            proximity = ideal / max(ideal, span)

        # 5. Field: a match in the title/heading path is worth more than a match
        #    buried in the body.
        title_hits = sum(self._idf(t) for t in q_unique if t in prefix_tokens)
        title_field = title_hits / total_idf

        # 6. Position of first match — leads beat footnotes.
        first_position = 0.0
        if positions:
            first_position = 1.0 - min(1.0, positions[0][0] / max(1, len(body_tokens)))

        # 7. Late interaction: best single sentence, not the averaged chunk.
        late_interaction = self._late_interaction(qvec, text, cache_key)

        # 8. Length: mild penalty so a long chunk cannot win on surface area.
        length_penalty = math.log1p(len(body_tokens)) / math.log1p(400)

        features = {
            "coverage": coverage,
            "fuzzy_coverage": fuzzy_coverage,
            "phrase": phrase,
            "proximity": proximity,
            "title_field": title_field,
            "first_position": first_position,
            "late_interaction": late_interaction,
            "length_penalty": length_penalty,
        }
        w = self.w
        score = (
            w.coverage * coverage
            + w.fuzzy_coverage * fuzzy_coverage
            + w.phrase * phrase
            + w.proximity * proximity
            + w.title_field * title_field
            + w.first_position * first_position
            + w.late_interaction * late_interaction
            - w.length_penalty * length_penalty
        )
        return score, features

    def rerank(self, query: str, candidates: Sequence[ScoredChunk], k: int) -> list[ScoredChunk]:
        if not candidates:
            return []

        qvec = self.embedder.encode([query])[0] if self.embedder is not None else None
        raw: list[float] = []
        feature_rows: list[dict[str, float]] = []
        for cand in candidates:
            score, features = self.score_pair(
                query,
                cand.chunk.body,
                cand.chunk.context_prefix,
                qvec=qvec,
                cache_key=cand.chunk_id,
            )
            raw.append(score)
            feature_rows.append(features)

        # First-stage rank as a decaying prior, then both signals min-max
        # normalised so the interpolation weight means what it says.
        priors = [1.0 / (1.0 + math.log1p(rank)) for rank in range(1, len(candidates) + 1)]
        ce_norm = _minmax(raw)
        prior_norm = _minmax(priors)
        alpha = self.w.prior_weight

        out: list[ScoredChunk] = []
        for i, cand in enumerate(candidates):
            final = alpha * prior_norm[i] + (1.0 - alpha) * ce_norm[i]
            sc = ScoredChunk(cand.chunk, float(final), dict(cand.components))
            sc.components.update({f"ce_{name}": float(v) for name, v in feature_rows[i].items()})
            sc.components["rerank"] = float(final)
            sc.components["rerank_raw"] = float(raw[i])
            sc.components["first_stage_rank"] = float(i + 1)
            out.append(sc)
        out.sort(key=lambda s: -s.score)
        return out[:k]


class CrossEncoderReranker:  # pragma: no cover - env dependent
    """Trained cross-encoder (e.g. ``cross-encoder/ms-marco-MiniLM-L-6-v2``).

    Drop-in for ``LexicalCrossEncoder``. Requires ``sentence-transformers`` and
    network access to fetch the model, neither of which this repository assumes.
    """

    name = "cross-encoder"

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2", **kwargs) -> None:
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise ImportError(
                "CrossEncoderReranker needs `pip install sentence-transformers`. "
                "Use LexicalCrossEncoder for a dependency-free run."
            ) from exc
        self._model = CrossEncoder(model_name, **kwargs)

    def rerank(self, query: str, candidates: Sequence[ScoredChunk], k: int) -> list[ScoredChunk]:
        if not candidates:
            return []
        pairs = [(query, c.chunk.text) for c in candidates]
        scores = np.asarray(self._model.predict(pairs), dtype=np.float32)
        out = []
        for cand, score in zip(candidates, scores):
            sc = ScoredChunk(cand.chunk, float(score), dict(cand.components))
            sc.components["rerank"] = float(score)
            out.append(sc)
        out.sort(key=lambda s: -s.score)
        return out[:k]
