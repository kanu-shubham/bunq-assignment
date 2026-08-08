"""Prompt construction for grounded answering.

Two blocks, in this order, for a reason:

1. `ANSWER_SYSTEM` — byte-stable across every request in the deployment, so it
   is passed as a system block with a cache breakpoint. Everything volatile is
   kept out of it (no timestamps, no tenant name, no per-request ids), because
   caching is a prefix match and one interpolated `datetime.now()` at the top
   invalidates the entire prefix on every request.
2. The user turn — question plus the numbered context blocks. Volatile by
   definition, so it sits after the breakpoint.

The context blocks carry their own metadata (source path, last updated) because
the model needs it to resolve conflicts between an old and a new policy, and
because that metadata is what makes a citation checkable by a human.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..types import ScoredChunk

ABSTAIN_TOKEN = "INSUFFICIENT_CONTEXT"

ANSWER_SYSTEM = f"""You answer questions about a company's internal documentation.

You are given numbered source passages. They are the only evidence you may use.

Grounding rules:
- Every factual sentence must end with one or more citation markers naming the passages that support it, like [2] or [1][4].
- Use only information present in the passages. Do not use background knowledge about how companies usually work, and do not fill gaps with plausible detail.
- If the passages do not contain enough information to answer, reply with exactly {ABSTAIN_TOKEN} on the first line, then one sentence naming what is missing. Do not guess.
- Partial answers are fine and preferred over abstaining: answer the part that is supported, and say plainly which part is not covered by the sources.
- If passages disagree, say so, give both versions with their citations, and prefer the one with the more recent "updated" date.
- Quote exact figures, dates, thresholds, and names from the passages rather than paraphrasing them.

Style:
- Lead with the answer. No preamble, no restating the question.
- Be brief: a short paragraph, or a short list when the answer genuinely has parts.
- Use the documentation's own terminology.
- Do not mention "passages", "context", or "the documents provided" — the reader sees the citations, not the plumbing."""

STRICTER_RETRY_SUFFIX = f"""

The previous attempt contained sentences with no citation. Rewrite the answer so that every factual sentence carries a citation marker for a passage that actually states it. Drop any claim you cannot cite. If that leaves nothing, reply {ABSTAIN_TOKEN}."""


def render_context_block(index: int, scored: ScoredChunk) -> str:
    chunk = scored.chunk
    updated = chunk.updated_at.date().isoformat()
    header = f"[{index}] {chunk.display_path} (source: {chunk.source}, updated: {updated})"
    return f"{header}\n{chunk.text}"


def build_user_prompt(question: str, contexts: Sequence[ScoredChunk]) -> str:
    if not contexts:
        return (
            f"Question: {question}\n\n"
            "Sources: none were retrieved.\n\n"
            f"Reply with exactly {ABSTAIN_TOKEN} and one sentence saying no documentation "
            "was found for this question."
        )
    blocks = "\n\n".join(render_context_block(i, c) for i, c in enumerate(contexts, start=1))
    return f"Sources:\n\n{blocks}\n\n---\n\nQuestion: {question}\n\nAnswer, with citations."


def marker_to_chunk(contexts: Sequence[ScoredChunk]) -> dict[int, ScoredChunk]:
    return {i: c for i, c in enumerate(contexts, start=1)}
