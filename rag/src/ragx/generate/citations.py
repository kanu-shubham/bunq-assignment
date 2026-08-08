"""Citation extraction and verification.

An LLM asked to cite will produce markers that *look* right. Three failure
modes show up in practice, and all three are cheap to catch deterministically:

1. **Hallucinated markers** — `[9]` when only 8 passages were supplied. Dropped.
2. **Uncited claims** — factual sentences with no marker at all. Counted; their
   ratio is the grounding score.
3. **Mis-attribution** — a real marker on a sentence the cited passage does not
   support. Caught here only in its crude lexical form (near-zero content-word
   overlap between the sentence and the cited chunk). The semantic version of
   this check is the LLM judge in `eval/judge.py`; this one is fast enough to
   run inline on every request.

This is the layer that turns "the model said it cited things" into a number the
service can act on — degrade the response, retry once, or abstain.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from ..index.bm25 import tokenize
from ..types import Citation, ScoredChunk

_MARKER_RE = re.compile(r"\[(\d{1,2})\]")
# A sentence runs up to its terminator *and keeps any citation markers that
# trail it* — "…within 30 days. [1]" is one cited sentence, not a sentence
# followed by an orphan marker. Splitting naively on the period is the reason
# most home-grown citation checkers report zero grounding on correct answers.
_SENTENCE_RE = re.compile(r"[^.!?\n]+(?:[.!?]+|\n|$)(?:[ \t]*\[\d{1,2}\])*")

# Sentences that carry no factual claim do not need a citation.
_NON_CLAIM_RE = re.compile(
    r"^\s*(?:[-*•]\s*)?(?:$|#|```)"  # blank, bullet marker alone, heading, fence
)
_HEDGE_PREFIXES = (
    "in short",
    "in summary",
    "note that this",
    "let me know",
    "i don't",
    "i do not",
)


@dataclass(frozen=True, slots=True)
class GroundingReport:
    citations: tuple[Citation, ...]
    grounding_score: float
    uncited_sentences: tuple[str, ...]
    invalid_markers: tuple[int, ...]
    weakly_supported: tuple[str, ...]

    @property
    def is_grounded(self) -> bool:
        return self.grounding_score >= 1.0 and not self.invalid_markers


def split_sentences(text: str) -> list[str]:
    return [match.group(0).strip() for match in _SENTENCE_RE.finditer(text) if match.group(0).strip()]


def is_claim_sentence(sentence: str) -> bool:
    stripped = sentence.strip()
    if len(stripped) < 25:  # "Yes.", "See below." — nothing to attribute
        return False
    if _NON_CLAIM_RE.match(stripped):
        return False
    lowered = stripped.lower()
    if lowered.startswith(_HEDGE_PREFIXES):
        return False
    return True


def lexical_support(sentence: str, chunk_text: str, threshold: float = 0.25) -> bool:
    """Does the cited chunk share enough content words with the sentence?

    Deliberately permissive: this exists to catch citations pointing at an
    unrelated passage, not to adjudicate paraphrase. Over-tightening it turns
    correct abstractive answers into false alarms.
    """
    sentence_terms = {t for t in tokenize(sentence) if len(t) > 3}
    if not sentence_terms:
        return True
    chunk_terms = set(tokenize(chunk_text))
    overlap = len(sentence_terms & chunk_terms) / len(sentence_terms)
    return overlap >= threshold


def verify(
    answer_text: str,
    contexts: Sequence[ScoredChunk],
    check_support: bool = True,
) -> GroundingReport:
    marker_map = {i: c for i, c in enumerate(contexts, start=1)}
    citations: dict[tuple[int, str], Citation] = {}
    invalid: list[int] = []
    uncited: list[str] = []
    weak: list[str] = []

    claim_sentences = 0
    cited_sentences = 0

    for sentence in split_sentences(answer_text):
        markers = [int(m) for m in _MARKER_RE.findall(sentence)]
        valid = [m for m in markers if m in marker_map]
        invalid.extend(m for m in markers if m not in marker_map)

        if not is_claim_sentence(sentence):
            # Still register any citations it carries; just don't score it.
            for marker in valid:
                _register(citations, marker, marker_map[marker], sentence)
            continue

        claim_sentences += 1
        if not valid:
            uncited.append(sentence)
            continue

        cited_sentences += 1
        supported = False
        for marker in valid:
            scored = marker_map[marker]
            _register(citations, marker, scored, sentence)
            if not check_support or lexical_support(sentence, scored.chunk.text):
                supported = True
        if not supported:
            weak.append(sentence)

    score = 1.0 if claim_sentences == 0 else cited_sentences / claim_sentences
    return GroundingReport(
        citations=tuple(citations.values()),
        grounding_score=score,
        uncited_sentences=tuple(uncited),
        invalid_markers=tuple(sorted(set(invalid))),
        weakly_supported=tuple(weak),
    )


def _register(
    citations: dict[tuple[int, str], Citation],
    marker: int,
    scored: ScoredChunk,
    sentence: str,
) -> None:
    key = (marker, scored.chunk.chunk_id)
    if key in citations:
        return
    chunk = scored.chunk
    citations[key] = Citation(
        marker=marker,
        chunk_id=chunk.chunk_id,
        doc_id=chunk.doc_id,
        title=chunk.title,
        uri=chunk.uri,
        display_path=chunk.display_path,
        quote=_best_quote(sentence, chunk.text),
    )


def _best_quote(sentence: str, chunk_text: str, max_len: int = 240) -> str | None:
    """Pick the chunk sentence with the highest term overlap, for the UI to
    highlight. A citation a reader cannot verify in one click is decoration."""
    sentence_terms = {t for t in tokenize(sentence) if len(t) > 3}
    if not sentence_terms:
        return None
    best: tuple[float, str] | None = None
    for candidate in split_sentences(chunk_text):
        terms = set(tokenize(candidate))
        if not terms:
            continue
        overlap = len(sentence_terms & terms) / len(sentence_terms)
        if best is None or overlap > best[0]:
            best = (overlap, candidate)
    if best is None or best[0] < 0.2:
        return None
    return best[1][:max_len]


def strip_markers(text: str) -> str:
    return _MARKER_RE.sub("", text)
