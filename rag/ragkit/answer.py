"""Grounded answering with citations, and the machinery that stops it lying.

Prompting a model to "only use the provided context" reduces hallucination. It
does not eliminate it, and — more importantly — it gives you no way to *know*
when it failed. Everything here is built on the opposite premise: assume the
generator will occasionally assert something the context does not support, and
make that mechanically detectable.

Three enforcement layers, in order of how much they actually buy you:

1. **Abstention as a first-class outcome.** If retrieval did not clear a
   relevance floor, the correct answer is "I don't have that documented" and the
   generator is never asked. Most hallucinations in production RAG are not
   generation failures at all — they are retrieval failures that the generator
   was then obliged to paper over.
2. **Quote-level verification.** Every claim must carry a verbatim span from a
   numbered context block. After generation the span is checked against the
   source by string containment. A citation whose quote is not in the chunk it
   points at is dropped and the answer is flagged. This is a check, not a
   suggestion: it runs on every answer and cannot be prompted away.
3. **Coverage check.** An answer whose sentences are mostly uncited is reported
   as partially unsupported, so a caller can degrade the response rather than
   render it as fact.

``ExtractiveAnswerer`` needs no model at all: it selects and cites sentences
from the retrieved chunks. It cannot paraphrase, so it also cannot fabricate —
a genuinely useful baseline and the right fallback when the model is unavailable
or the question is high-stakes.
"""

from __future__ import annotations

import enum
import json
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from .rerank import LexicalCrossEncoder
from .textutil import normalize, sentences, tokenize
from .types import ScoredChunk


class AbstainReason(str, enum.Enum):
    NONE = "none"
    NO_RESULTS = "no_results"
    LOW_RELEVANCE = "low_relevance"
    MODEL_DECLINED = "model_declined"
    UNSUPPORTED = "unsupported_after_verification"


@dataclass
class Citation:
    index: int  # the [n] marker in the answer
    doc_id: str
    chunk_id: str
    quote: str
    verified: bool = False
    note: str = ""


@dataclass
class Answer:
    text: str
    citations: list[Citation] = field(default_factory=list)
    abstained: bool = False
    abstain_reason: AbstainReason = AbstainReason.NONE
    trace: dict[str, Any] = field(default_factory=dict)

    @property
    def verified_citations(self) -> list[Citation]:
        return [c for c in self.citations if c.verified]

    @property
    def fully_supported(self) -> bool:
        return bool(self.citations) and all(c.verified for c in self.citations)

    def render(self) -> str:
        if self.abstained:
            return self.text
        lines = [self.text, ""]
        for c in self.citations:
            mark = "" if c.verified else "  [UNVERIFIED]"
            lines.append(f"[{c.index}] {c.doc_id} ({c.chunk_id}){mark}")
        return "\n".join(lines)


def build_context(chunks: Sequence[ScoredChunk], *, max_chars: int = 12000) -> tuple[str, list[ScoredChunk]]:
    """Render retrieved chunks as numbered blocks, respecting a char budget.

    Numbering is 1-based and stable, and each block repeats its document id.
    Both matter: the model cites by number, and a human reading the transcript
    needs to resolve the number to a document without a lookup table.
    """
    parts: list[str] = []
    used: list[ScoredChunk] = []
    total = 0
    for i, sc in enumerate(chunks, start=1):
        block = (
            f"[{i}] doc_id={sc.doc_id} | {sc.chunk.context_prefix}\n{sc.chunk.body}"
        )
        if total + len(block) > max_chars and used:
            break
        parts.append(block)
        used.append(sc)
        total += len(block)
    return "\n\n".join(parts), used


_WS_RE = re.compile(r"\s+")


def _canonical(text: str) -> str:
    return _WS_RE.sub(" ", normalize(text)).strip()


def verify_citations(
    citations: Sequence[Citation],
    context_chunks: Sequence[ScoredChunk],
    *,
    min_quote_words: int = 4,
) -> list[Citation]:
    """Check every quote against the chunk it claims to come from.

    Containment is checked on whitespace- and case-normalised text, because a
    model that reflows a quote across a line break has not fabricated it. A
    quote shorter than ``min_quote_words`` is rejected regardless: a three-word
    span appears in half the corpus and verifies nothing.
    """
    by_index = {i: sc for i, sc in enumerate(context_chunks, start=1)}
    out: list[Citation] = []
    for citation in citations:
        source = by_index.get(citation.index)
        quote = _canonical(citation.quote)
        if source is None:
            out.append(
                Citation(citation.index, citation.doc_id, citation.chunk_id, citation.quote,
                         False, f"citation [{citation.index}] is not in the provided context")
            )
            continue
        if len(quote.split()) < min_quote_words:
            out.append(
                Citation(citation.index, source.doc_id, source.chunk_id, citation.quote,
                         False, "quote too short to verify")
            )
            continue
        haystack = _canonical(source.chunk.body)
        verified = quote in haystack
        out.append(
            Citation(
                citation.index,
                source.doc_id,
                source.chunk_id,
                citation.quote,
                verified,
                "" if verified else "quote not found verbatim in the cited chunk",
            )
        )
    return out


def coverage(answer_text: str, citations: Sequence[Citation]) -> float:
    """Fraction of answer sentences that carry at least one citation marker."""
    sents = sentences(answer_text)
    if not sents:
        return 0.0
    valid = {c.index for c in citations if c.verified}
    cited = 0
    for sent in sents:
        markers = {int(m) for m in re.findall(r"\[(\d+)\]", sent)}
        if markers & valid:
            cited += 1
    return cited / len(sents)


# --------------------------------------------------------------------------


class ExtractiveAnswerer:
    """Selects and cites sentences. Cannot paraphrase, so cannot fabricate."""

    name = "extractive"

    def __init__(
        self,
        scorer: LexicalCrossEncoder | None = None,
        *,
        min_relevance: float = 2.5,
        min_relative: float = 0.75,
        max_sentences: int = 4,
    ) -> None:
        """
        ``min_relevance`` is a **cheap first gate, not the safety guarantee**,
        and its value is calibrated rather than guessed — see
        ``scripts/calibrate_abstention.py``, which scores questions the corpus
        can answer against questions it cannot.

        That script also shows why the gate cannot be the guarantee: relevance
        scoring answers "is this passage about this topic", while abstention
        needs "does this passage answer this question". A question like "do we
        offer unlimited vacation" retrieves the leave policy and scores like a
        hit. Pushing the threshold high enough to reject it rejects most real
        questions too. The mechanisms that actually catch it are downstream —
        the entailment decision (``ClaudeAnswerer``'s ``answerable`` field) and
        quote verification, which a fabricated premise cannot survive.

        ``min_relative`` drops any sentence scoring below this fraction of the
        best one. Without it the answer pads itself with the fourth-best
        sentence in the context regardless of whether that sentence is about
        the question at all, which reads as authoritative and is not.
        """
        self.scorer = scorer or LexicalCrossEncoder()
        self.min_relevance = min_relevance
        self.min_relative = min_relative
        self.max_sentences = max_sentences

    def answer(self, query: str, chunks: Sequence[ScoredChunk]) -> Answer:
        if not chunks:
            return Answer(
                "I don't have anything documented about that.",
                abstained=True,
                abstain_reason=AbstainReason.NO_RESULTS,
            )
        context, used = build_context(chunks)

        qvec = (
            self.scorer.embedder.encode([query])[0]
            if getattr(self.scorer, "embedder", None) is not None
            else None
        )
        scored: list[tuple[float, int, str]] = []
        for idx, sc in enumerate(used, start=1):
            for sent in sentences(sc.chunk.body):
                if len(tokenize(sent)) < 4:
                    continue
                score, _ = self.scorer.score_pair(
                    query, sent, sc.chunk.context_prefix, qvec=qvec, cache_key=None
                )
                scored.append((score, idx, sent))
        scored.sort(key=lambda t: -t[0])

        if not scored or scored[0][0] < self.min_relevance:
            return Answer(
                "I don't have that documented. The closest material I found does not "
                "actually answer the question, so I would rather say so than guess.",
                abstained=True,
                abstain_reason=AbstainReason.LOW_RELEVANCE,
                trace={"best_score": round(scored[0][0], 3) if scored else None,
                       "threshold": self.min_relevance},
            )

        floor = scored[0][0] * self.min_relative
        picked = [row for row in scored[: self.max_sentences] if row[0] >= floor]
        # Restore document order so the extract reads as prose, not as a ranking.
        picked.sort(key=lambda t: (t[1], t[2]))

        lines = [f"{sent.strip()} [{idx}]" for _, idx, sent in picked]
        citations = [
            Citation(idx, used[idx - 1].doc_id, used[idx - 1].chunk_id, sent)
            for _, idx, sent in picked
        ]
        citations = verify_citations(citations, used)
        text = " ".join(lines)
        return Answer(
            text=text,
            citations=citations,
            trace={
                "n_context_chunks": len(used),
                "context_chars": len(context),
                "best_score": round(picked[0][0], 3),
                "coverage": round(coverage(text, citations), 3),
            },
        )


ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answerable": {
            "type": "boolean",
            "description": "True only if the numbered context contains the answer. False otherwise.",
        },
        "answer": {
            "type": "string",
            "description": (
                "The answer, with a [n] marker after every sentence that draws on "
                "context block n. Empty string when answerable is false."
            ),
        },
        "citations": {
            "type": "array",
            "description": "One entry per [n] marker used in the answer.",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "The context block number."},
                    "quote": {
                        "type": "string",
                        "description": (
                            "A span copied verbatim from that block, at least one full "
                            "clause, that supports the claim. Copy it exactly — it is "
                            "checked against the source."
                        ),
                    },
                },
                "required": ["index", "quote"],
                "additionalProperties": False,
            },
        },
        "missing": {
            "type": "string",
            "description": "When answerable is false, what documentation would be needed.",
        },
    },
    "required": ["answerable", "answer", "citations", "missing"],
    "additionalProperties": False,
}

_ANSWER_SYSTEM = """You answer questions about internal engineering documentation \
using only the numbered context blocks provided in the user turn.

Rules:
- Use only the context. If it does not contain the answer, set answerable to \
false and say what is missing. Partial information is not an answer.
- Put a [n] marker after every sentence, naming the block it came from.
- Every citation carries a span copied verbatim from that block. The span is \
checked against the source after you respond, so paraphrasing it will be \
detected as an unsupported claim.
- Prefer the more specific document when two blocks disagree, and say that they \
disagree rather than silently picking one.
- Be brief. Answer the question asked, not the surrounding topic."""


class ClaudeAnswerer:  # pragma: no cover - requires network + credentials
    """Model-backed grounded answering with mechanical citation verification."""

    name = "claude"

    def __init__(
        self,
        model: str = "claude-opus-5",
        *,
        effort: str = "medium",
        max_tokens: int = 4096,
        min_first_score: float = 0.0,
        client=None,
    ) -> None:
        if client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise ImportError(
                    "ClaudeAnswerer needs `pip install anthropic`. "
                    "Use ExtractiveAnswerer for an offline run."
                ) from exc
            client = anthropic.Anthropic()
        self._client = client
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.min_first_score = min_first_score

    def answer(self, query: str, chunks: Sequence[ScoredChunk]) -> Answer:
        if not chunks:
            return Answer(
                "I don't have anything documented about that.",
                abstained=True,
                abstain_reason=AbstainReason.NO_RESULTS,
            )
        if chunks[0].score < self.min_first_score:
            return Answer(
                "I don't have that documented well enough to answer reliably.",
                abstained=True,
                abstain_reason=AbstainReason.LOW_RELEVANCE,
                trace={"top_score": chunks[0].score, "threshold": self.min_first_score},
            )

        context, used = build_context(chunks)
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[
                # Stable prefix first so the cache survives across questions;
                # the volatile context goes in the user turn, after it.
                {"type": "text", "text": _ANSWER_SYSTEM, "cache_control": {"type": "ephemeral"}}
            ],
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": ANSWER_SCHEMA},
            },
            messages=[
                {
                    "role": "user",
                    "content": f"Context:\n\n{context}\n\nQuestion: {query}",
                }
            ],
        )

        if response.stop_reason == "refusal":
            return Answer(
                "I can't answer that.",
                abstained=True,
                abstain_reason=AbstainReason.MODEL_DECLINED,
                trace={"stop_reason": "refusal"},
            )

        payload = json.loads(
            "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
        )
        if not payload.get("answerable"):
            return Answer(
                payload.get("missing") or "I don't have that documented.",
                abstained=True,
                abstain_reason=AbstainReason.LOW_RELEVANCE,
                trace={"n_context_chunks": len(used)},
            )

        raw = [
            Citation(int(c["index"]), "", "", str(c["quote"]))
            for c in payload.get("citations", [])
        ]
        citations = verify_citations(raw, used)
        text = str(payload.get("answer", "")).strip()
        cov = coverage(text, citations)

        # Verification failure is not a warning to log and move past. If nothing
        # the model asserted can be traced to the context, the answer is not
        # grounded and must not be presented as if it were.
        if citations and not any(c.verified for c in citations):
            return Answer(
                "I found related documentation but could not verify an answer against it.",
                citations=citations,
                abstained=True,
                abstain_reason=AbstainReason.UNSUPPORTED,
                trace={"coverage": cov, "n_context_chunks": len(used)},
            )

        return Answer(
            text=text,
            citations=citations,
            trace={
                "n_context_chunks": len(used),
                "context_chars": len(context),
                "coverage": round(cov, 3),
                "unverified_citations": sum(1 for c in citations if not c.verified),
                "usage": {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0),
                },
            },
        )
