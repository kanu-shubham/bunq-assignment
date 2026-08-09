"""Tokenisation and text normalisation.

The tokeniser is the single most under-examined component in most RAG systems.
A naive ``re.findall(r"\\w+")`` shreds ``ERR_CURRENCY_MISMATCH`` into three
common words and makes the one query that should be trivially answerable —
"what is ERR_CURRENCY_MISMATCH" — indistinguishable from every document that
happens to mention currency. This tokeniser keeps the identifier *and* emits its
parts, so exact-identifier queries stay sharp without losing partial matches.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Iterator

# Runs of letters/digits, optionally glued by . _ - / into a single identifier.
_IDENT_RE = re.compile(r"[A-Za-z0-9]+(?:[._\-/][A-Za-z0-9]+)*")
_SPLIT_RE = re.compile(r"[._\-/]")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# Deliberately small. An aggressive stoplist hurts phrase-ish queries ("how to
# roll back a deploy") more than it helps, and BM25's IDF already discounts
# common terms. These are the ones that appear in nearly every document.
STOPWORDS = frozenset(
    """
    a an the and or but if then than that this these those of in on at to for
    from by with as is are was were be been being it its do does did not no
    """.split()
)

SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def normalize(text: str) -> str:
    """NFKD-fold, strip accents, lowercase. Stable across the whole pipeline."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower()


def tokenize(text: str, *, drop_stopwords: bool = True) -> list[str]:
    """Tokenise into lexical units.

    A dotted/underscored identifier yields the whole identifier plus each of its
    parts, and CamelCase is split as well. ``payments.sepa_instant.enabled``
    becomes ``['payments.sepa_instant.enabled', 'payments', 'sepa', 'instant',
    'enabled']`` — the compound survives for exact matching while the parts stay
    reachable for partial matching.
    """
    out: list[str] = []
    for match in _IDENT_RE.finditer(normalize(text)):
        token = match.group(0)
        parts = [p for p in _SPLIT_RE.split(token) if p]
        # Split camelCase inside each part (source is lowercased, so this only
        # fires on text normalised elsewhere; kept for callers that skip it).
        expanded: list[str] = []
        for part in parts:
            expanded.extend(p.lower() for p in _CAMEL_RE.split(part) if p)
        if len(expanded) > 1:
            out.append(token)  # the compound identifier itself
            out.extend(expanded)
        else:
            out.extend(expanded)
    if drop_stopwords:
        out = [t for t in out if t not in STOPWORDS and len(t) > 1]
    return out


def char_ngrams(word: str, sizes: Iterable[int] = (3, 4, 5)) -> Iterator[str]:
    """Boundary-marked character n-grams of a single word.

    Boundary markers matter: without them ``#lag#`` and ``flagged`` share the
    trigram ``lag`` and look related. With them they do not.
    """
    padded = f"#{word}#"
    for n in sizes:
        if len(padded) < n:
            continue
        for i in range(len(padded) - n + 1):
            yield padded[i : i + n]


def sentences(text: str) -> list[str]:
    """Cheap sentence split — good enough for extractive citation spans."""
    parts = [s.strip() for s in SENTENCE_RE.split(text.replace("\n", " ")) if s.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def word_count(text: str) -> int:
    return len(_IDENT_RE.findall(text))
