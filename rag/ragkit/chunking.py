"""Heading-aware markdown chunking.

Fixed-size character chunking is the default in most tutorials and it is the
single cheapest thing to get wrong: it splits tables down the middle, separates
a heading from the paragraph it introduces, and produces chunks that are
unreadable on their own — which matters, because the chunk is what the model
eventually reads.

This chunker walks the document structure, keeps the heading path with each
chunk, never splits a table or a fenced code block, and overlaps by whole
sentences so a fact that straddles a boundary survives in both halves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .textutil import sentences, word_count
from .types import Chunk, Document

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE_RE = re.compile(r"^\s*```")


@dataclass(frozen=True)
class ChunkConfig:
    target_words: int = 120
    max_words: int = 220
    overlap_words: int = 35
    min_words: int = 20  # below this a chunk is merged into its neighbour


@dataclass
class _Block:
    """A markdown block: a paragraph, a list, a table, or a code fence."""

    text: str
    kind: str  # paragraph | table | code | list
    headings: tuple[str, ...]

    @property
    def words(self) -> int:
        return word_count(self.text)

    @property
    def atomic(self) -> bool:
        """Tables and code fences are never split — halving them destroys them."""
        return self.kind in {"table", "code"}


def _blocks(text: str) -> list[_Block]:
    headings: list[str] = []
    blocks: list[_Block] = []
    buf: list[str] = []
    kind = "paragraph"
    in_fence = False

    def flush() -> None:
        nonlocal buf, kind
        joined = "\n".join(buf).strip()
        if joined:
            blocks.append(_Block(joined, kind, tuple(headings)))
        buf = []
        kind = "paragraph"

    for line in text.splitlines():
        if _FENCE_RE.match(line):
            if in_fence:
                buf.append(line)
                in_fence = False
                flush()
            else:
                flush()
                in_fence = True
                kind = "code"
                buf.append(line)
            continue
        if in_fence:
            buf.append(line)
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            del headings[level - 1 :]
            headings.append(heading.group(2).strip())
            continue

        if not line.strip():
            flush()
            continue

        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            if kind != "table":
                flush()
                kind = "table"
        elif stripped[:2] in {"* ", "- "} or re.match(r"^\d+\.\s", stripped):
            if kind not in {"list", "table"}:
                flush()
                kind = "list"
        elif line.startswith("    ") and kind == "paragraph" and not buf:
            kind = "code"  # indented code block
        buf.append(line)

    in_fence = False
    flush()
    return blocks


def _tail_overlap(text: str, overlap_words: int) -> str:
    """Whole trailing sentences up to ``overlap_words``."""
    if overlap_words <= 0:
        return ""
    picked: list[str] = []
    total = 0
    for sent in reversed(sentences(text)):
        w = word_count(sent)
        if total + w > overlap_words and picked:
            break
        picked.insert(0, sent)
        total += w
    return " ".join(picked)


def chunk_document(doc: Document, config: ChunkConfig | None = None) -> list[Chunk]:
    config = config or ChunkConfig()
    blocks = _blocks(doc.text)
    if not blocks:
        return []

    groups: list[tuple[tuple[str, ...], list[str]]] = []
    current: list[str] = []
    current_headings: tuple[str, ...] = ()
    current_words = 0

    def close() -> None:
        nonlocal current, current_words
        if current:
            groups.append((current_headings, current))
        current = []
        current_words = 0

    for block in blocks:
        # A heading change is a natural boundary; respect it unless the chunk so
        # far is too small to stand alone.
        if block.headings != current_headings and current_words >= config.min_words:
            close()
        if not current:
            current_headings = block.headings

        if block.atomic and current_words + block.words > config.max_words:
            close()
            current_headings = block.headings

        if current_words + block.words > config.max_words and current:
            close()
            current_headings = block.headings
            overlap = _tail_overlap("\n".join(groups[-1][1]), config.overlap_words) if groups else ""
            if overlap:
                current.append(overlap)
                current_words += word_count(overlap)

        current.append(block.text)
        current_words += block.words

        if current_words >= config.target_words and not block.atomic:
            close()

    close()

    # Merge any runt tail into its predecessor rather than emitting a 6-word chunk.
    if len(groups) > 1 and word_count("\n".join(groups[-1][1])) < config.min_words:
        headings, body = groups.pop()
        groups[-1][1].extend(body)

    chunks: list[Chunk] = []
    for ordinal, (headings, parts) in enumerate(groups):
        body = "\n\n".join(p.strip() for p in parts if p.strip())
        if not body.strip():
            continue
        prefix = " > ".join((doc.title,) + headings)
        chunks.append(
            Chunk(
                chunk_id=f"{doc.doc_id}#{ordinal:03d}",
                doc_id=doc.doc_id,
                ordinal=ordinal,
                body=body,
                context_prefix=prefix,
                metadata=dict(doc.metadata, title=doc.title, path=doc.path),
            )
        )
    return chunks


def chunk_corpus(docs: Iterable[Document], config: ChunkConfig | None = None) -> list[Chunk]:
    out: list[Chunk] = []
    for doc in docs:
        out.extend(chunk_document(doc, config))
    return out
