"""Embedding backends.

Two implementations behind one protocol:

``HashingEmbedder``
    Deterministic, dependency-free, offline. Hashes IDF-weighted word tokens
    *and* boundary-marked character n-grams into a fixed-width signed vector.
    It is not a neural encoder and it will not discover that "PTO" and
    "annual leave" mean the same thing — no hashing scheme can. What it *does*
    give you is genuine complementarity with BM25: morphological variants
    ("reconciling" / "reconciliation"), typos, and partial identifiers all land
    near their target because they share character n-grams, while BM25 sees
    nothing but an out-of-vocabulary term. That is precisely the axis on which
    dense retrieval earns its place in a hybrid, so the lesson survives the
    substitution.

``SentenceTransformerEmbedder``
    A drop-in for a real bi-encoder. Swap it in and every number in the
    evaluation moves; nothing else in the pipeline changes. That is the point of
    the protocol.

The reason the default is the hashing embedder is environmental, and worth
stating plainly: this repository is built to run with no model downloads and no
network. Where a real bi-encoder would change a conclusion, the README says so.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from typing import Iterable, Protocol, Sequence, runtime_checkable

import numpy as np

from .textutil import char_ngrams, tokenize


@runtime_checkable
class Embedder(Protocol):
    """Anything that turns text into L2-normalised row vectors."""

    dim: int

    def encode(self, texts: Sequence[str]) -> np.ndarray: ...


def _hash_index(feature: str, dim: int) -> tuple[int, float]:
    """Signed feature hashing: index plus a +1/-1 sign.

    The sign is what keeps collisions unbiased — two colliding features cancel
    half the time instead of always reinforcing each other.
    """
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=9).digest()
    idx = int.from_bytes(digest[:8], "big") % dim
    sign = 1.0 if digest[8] & 1 else -1.0
    return idx, sign


class HashingEmbedder:
    """IDF-weighted word + character n-gram hashing encoder."""

    def __init__(
        self,
        dim: int = 1024,
        *,
        ngram_sizes: Sequence[int] = (3, 4, 5),
        word_weight: float = 1.0,
        char_weight: float = 0.8,
        cache_size: int = 8192,
    ) -> None:
        self.dim = dim
        self.ngram_sizes = tuple(ngram_sizes)
        self.word_weight = word_weight
        self.char_weight = char_weight
        self._idf: dict[str, float] = {}
        self._default_idf = 1.0
        self._fitted = False
        self._cache: dict[str, np.ndarray] = {}
        self._cache_size = cache_size

    # ------------------------------------------------------------------ fit

    def fit(self, texts: Iterable[str]) -> "HashingEmbedder":
        """Learn IDF weights from the corpus.

        Encoding works unfitted (every term gets weight 1.0), but unfitted
        vectors are dominated by whichever common words happen to be frequent,
        and retrieval quality drops noticeably. Fit.
        """
        df: Counter[str] = Counter()
        n_docs = 0
        for text in texts:
            n_docs += 1
            df.update(set(tokenize(text)))
        if n_docs == 0:
            raise ValueError("cannot fit an embedder on an empty corpus")
        # Smoothed IDF, floored at a small positive value so that a term
        # appearing in every document still contributes a little.
        self._idf = {
            term: max(0.05, math.log((n_docs + 1) / (count + 0.5)))
            for term, count in df.items()
        }
        self._default_idf = math.log((n_docs + 1) / 0.5)  # unseen term = maximally rare
        self._fitted = True
        self._cache.clear()
        return self

    @property
    def fitted(self) -> bool:
        return self._fitted

    def idf(self, term: str) -> float:
        return self._idf.get(term, self._default_idf) if self._fitted else 1.0

    # --------------------------------------------------------------- encode

    def _encode_one(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        counts = Counter(tokenize(text))
        if not counts:
            return vec
        for token, tf in counts.items():
            weight = (1.0 + math.log(tf)) * self.idf(token)

            idx, sign = _hash_index(f"w:{token}", self.dim)
            vec[idx] += sign * weight * self.word_weight

            grams = list(char_ngrams(token, self.ngram_sizes))
            if not grams:
                continue
            # Normalise by sqrt(count) so a long identifier does not simply
            # outweigh a short one by virtue of having more n-grams.
            gram_weight = weight * self.char_weight / math.sqrt(len(grams))
            for gram in grams:
                gidx, gsign = _hash_index(f"c:{gram}", self.dim)
                vec[gidx] += gsign * gram_weight

        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec /= norm
        return vec

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            cached = self._cache.get(text)
            if cached is None:
                cached = self._encode_one(text)
                if len(self._cache) < self._cache_size:
                    self._cache[text] = cached
            out[i] = cached
        return out


class SentenceTransformerEmbedder:
    """Real bi-encoder backend. Requires ``sentence-transformers`` and a model.

    Not exercised in this repository's reported numbers: the sandbox this was
    built in cannot reach the model hub. It is here because the swap should be
    one line, and because a claim that it *would* be one line is worth less than
    the code that makes it so.
    """

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2", **kwargs) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - env dependent
            raise ImportError(
                "SentenceTransformerEmbedder needs `pip install sentence-transformers`. "
                "Use HashingEmbedder for a dependency-free run."
            ) from exc
        self._model = SentenceTransformer(model_name, **kwargs)
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def encode(self, texts: Sequence[str]) -> np.ndarray:  # pragma: no cover - env dependent
        return np.asarray(
            self._model.encode(list(texts), normalize_embeddings=True, show_progress_bar=False),
            dtype=np.float32,
        )


def cosine_matrix(queries: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Cosine similarity for pre-normalised rows — a plain dot product."""
    if queries.ndim == 1:
        queries = queries[None, :]
    return queries @ matrix.T
