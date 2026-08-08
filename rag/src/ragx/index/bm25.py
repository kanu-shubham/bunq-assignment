"""Lexical retrieval: BM25 Okapi, implemented directly.

The dense side cannot be trusted alone on the queries company docs actually
get: error codes (`ERR_ACCT_4032`), ticket ids, product names, policy numbers,
acronyms, and anything coined after the embedding model was trained. Those are
exact-match problems, and BM25 solves them for free.

In production this is OpenSearch/Elasticsearch or Postgres full-text; the
interface is the same and the tuning constants below are the ones you would
set there anyway.
"""

from __future__ import annotations

import heapq
import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from ..types import Chunk
from .vector_store import AccessFilter

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")

# Kept tiny on purpose: aggressive stopword lists break exact-phrase queries
# like "who is on call" and "right to be forgotten".
_STOPWORDS = frozenset(
    "a an and are as at be by for from in is it of on or that the to was were with".split()
)


def tokenize(text: str) -> list[str]:
    """Lowercase, keep dotted/underscored identifiers intact, then also emit
    their sub-parts so `err_acct_4032` matches a query for `acct 4032`."""
    tokens: list[str] = []
    for match in _TOKEN_RE.findall(text.lower()):
        if match in _STOPWORDS:
            continue
        tokens.append(match)
        if any(sep in match for sep in "._-"):
            tokens.extend(p for p in re.split(r"[._-]", match) if p and p not in _STOPWORDS)
    return tokens


@dataclass
class BM25Index:
    k1: float = 1.2
    b: float = 0.75
    _postings: dict[str, dict[str, int]] = field(default_factory=dict)  # term -> chunk -> tf
    _lengths: dict[str, int] = field(default_factory=dict)
    _chunks: dict[str, Chunk] = field(default_factory=dict)
    _terms_by_chunk: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def avg_length(self) -> float:
        if not self._lengths:
            return 0.0
        return sum(self._lengths.values()) / len(self._lengths)

    def upsert(self, chunks: Sequence[Chunk]) -> None:
        for chunk in chunks:
            if chunk.chunk_id in self._chunks:
                self.delete([chunk.chunk_id])
            tokens = tokenize(chunk.embed_text)
            counts = Counter(tokens)
            for term, tf in counts.items():
                self._postings.setdefault(term, {})[chunk.chunk_id] = tf
            self._lengths[chunk.chunk_id] = len(tokens)
            self._chunks[chunk.chunk_id] = chunk
            self._terms_by_chunk[chunk.chunk_id] = tuple(counts)

    def delete(self, chunk_ids: Iterable[str]) -> int:
        removed = 0
        for chunk_id in list(chunk_ids):
            terms = self._terms_by_chunk.pop(chunk_id, ())
            for term in terms:
                postings = self._postings.get(term)
                if postings is not None:
                    postings.pop(chunk_id, None)
                    if not postings:
                        del self._postings[term]
            if self._chunks.pop(chunk_id, None) is not None:
                removed += 1
            self._lengths.pop(chunk_id, None)
        return removed

    def _idf(self, term: str) -> float:
        n_docs = len(self._chunks)
        df = len(self._postings.get(term, {}))
        if df == 0:
            return 0.0
        # Okapi IDF with the +1 guard that keeps very common terms non-negative.
        return math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))

    def search(self, query: str, k: int, access: AccessFilter) -> list[tuple[str, float]]:
        if not self._chunks:
            return []
        avgdl = self.avg_length or 1.0
        scores: dict[str, float] = {}
        for term in set(tokenize(query)):
            idf = self._idf(term)
            if idf <= 0.0:
                continue
            for chunk_id, tf in self._postings.get(term, {}).items():
                chunk = self._chunks[chunk_id]
                if not access.matches(chunk):  # pre-filter, same rule as dense
                    continue
                length_norm = 1 - self.b + self.b * (self._lengths[chunk_id] / avgdl)
                scores[chunk_id] = scores.get(chunk_id, 0.0) + idf * (
                    tf * (self.k1 + 1) / (tf + self.k1 * length_norm)
                )
        return heapq.nlargest(k, scores.items(), key=lambda pair: (pair[1], pair[0]))

    def get(self, chunk_id: str) -> Chunk | None:
        return self._chunks.get(chunk_id)

    def __len__(self) -> int:
        return len(self._chunks)
