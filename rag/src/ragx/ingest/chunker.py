"""Structure-aware chunking.

Three properties matter more than the exact window size:

1. **Never split an atomic block.** A markdown table or a fenced code block cut
   in half is worse than useless — it retrieves and then misleads.
2. **Never lose the heading path.** "Approval is required within 30 days" only
   answers a question if you know it sits under *Expenses › Reimbursement*.
   The path is stored as metadata and prepended to the embedded text.
3. **Stable ids.** A chunk id is derived from (doc, heading path, ordinal,
   content hash), so re-ingesting an unchanged document is a no-op and an
   edited paragraph only invalidates its own chunk.

Fixed-size character splitting fails all three, which is why it is the single
biggest quality regression in most first-pass RAG systems.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

from ..config import ChunkingConfig
from ..tokens import estimate_tokens
from ..types import Chunk, Document

BlockKind = Literal["prose", "code", "table", "list", "heading"]

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_TABLE_RE = re.compile(r"^\s*\|")
_LIST_RE = re.compile(r"^\s*([-*+]|\d+[.)])\s+")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[])")


@dataclass(slots=True)
class Block:
    kind: BlockKind
    text: str
    heading_path: tuple[str, ...]
    atomic: bool = False

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


def parse_blocks(text: str) -> list[Block]:
    """Split markdown into blocks, carrying the live heading stack on each."""
    blocks: list[Block] = []
    heading_stack: list[tuple[int, str]] = []
    buffer: list[str] = []
    buffer_kind: BlockKind = "prose"

    def flush() -> None:
        nonlocal buffer, buffer_kind
        body = "\n".join(buffer).strip("\n")
        if body.strip():
            blocks.append(
                Block(
                    kind=buffer_kind,
                    text=body,
                    heading_path=tuple(h for _, h in heading_stack),
                    atomic=buffer_kind in ("code", "table"),
                )
            )
        buffer = []
        buffer_kind = "prose"

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]

        fence = _FENCE_RE.match(line)
        if fence:
            flush()
            marker = fence.group(1)
            code_lines = [line]
            i += 1
            while i < len(lines):
                code_lines.append(lines[i])
                if lines[i].strip().startswith(marker):
                    i += 1
                    break
                i += 1
            blocks.append(
                Block(
                    kind="code",
                    text="\n".join(code_lines),
                    heading_path=tuple(h for _, h in heading_stack),
                    atomic=True,
                )
            )
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            title = heading.group(2)
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            i += 1
            continue

        if _TABLE_RE.match(line):
            flush()
            table_lines: list[str] = []
            while i < len(lines) and _TABLE_RE.match(lines[i]):
                table_lines.append(lines[i])
                i += 1
            blocks.append(
                Block(
                    kind="table",
                    text="\n".join(table_lines),
                    heading_path=tuple(h for _, h in heading_stack),
                    atomic=True,
                )
            )
            continue

        if not line.strip():
            flush()
            i += 1
            continue

        kind: BlockKind = "list" if _LIST_RE.match(line) else "prose"
        if buffer and kind != buffer_kind:
            flush()
        buffer_kind = kind
        buffer.append(line)
        i += 1

    flush()
    return blocks


def _split_oversized(block: Block, max_tokens: int) -> list[Block]:
    """Last resort for a single block above the hard ceiling.

    Prose splits on sentences; code and tables split on lines and keep a header
    row / fence marker so each piece still parses on its own.
    """
    if block.tokens <= max_tokens:
        return [block]

    if block.kind == "table":
        lines = block.text.splitlines()
        header = lines[:2]  # header + separator row
        body = lines[2:] or lines
        return [
            Block(block.kind, "\n".join(header + group), block.heading_path, atomic=True)
            for group in _group_by_tokens(body, max_tokens - estimate_tokens("\n".join(header)))
        ]

    if block.kind == "code":
        lines = block.text.splitlines()
        return [
            Block(block.kind, "\n".join(group), block.heading_path, atomic=True)
            for group in _group_by_tokens(lines, max_tokens)
        ]

    units = _SENTENCE_RE.split(block.text)
    return [
        Block(block.kind, " ".join(group), block.heading_path)
        for group in _group_by_tokens(units, max_tokens)
    ]


def _group_by_tokens(units: list[str], max_tokens: int) -> list[list[str]]:
    groups: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0
    for unit in units:
        unit_tokens = estimate_tokens(unit)
        if current and current_tokens + unit_tokens > max_tokens:
            groups.append(current)
            current, current_tokens = [], 0
        current.append(unit)
        current_tokens += unit_tokens
    if current:
        groups.append(current)
    return groups or [[]]


def _tail_overlap(text: str, overlap_tokens: int) -> str:
    """Take whole trailing sentences up to the overlap budget."""
    if overlap_tokens <= 0:
        return ""
    sentences = _SENTENCE_RE.split(text)
    tail: list[str] = []
    total = 0
    for sentence in reversed(sentences):
        cost = estimate_tokens(sentence)
        if total + cost > overlap_tokens and tail:
            break
        tail.insert(0, sentence)
        total += cost
    return " ".join(tail).strip()


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def make_chunk_id(doc_id: str, heading_path: tuple[str, ...], ordinal: int, content_hash: str) -> str:
    seed = f"{doc_id}\x00{'/'.join(heading_path)}\x00{ordinal}\x00{content_hash}"
    return hashlib.sha256(seed.encode()).hexdigest()[:16]


def chunk_document(doc: Document, config: ChunkingConfig) -> list[Chunk]:
    blocks: list[Block] = []
    for block in parse_blocks(doc.text):
        blocks.extend(_split_oversized(block, config.max_tokens))

    # Pack blocks into windows, never crossing a heading-path boundary: a chunk
    # that spans two sections is a chunk whose heading metadata is a lie.
    windows: list[tuple[tuple[str, ...], list[Block]]] = []
    current: list[Block] = []
    current_path: tuple[str, ...] = ()
    current_tokens = 0

    for block in blocks:
        starts_new_section = bool(current) and block.heading_path != current_path
        would_overflow = current and current_tokens + block.tokens > config.target_tokens
        if starts_new_section or would_overflow:
            windows.append((current_path, current))
            current, current_tokens = [], 0
        if not current:
            current_path = block.heading_path
        current.append(block)
        current_tokens += block.tokens
    if current:
        windows.append((current_path, current))

    windows = _merge_undersized(windows, config)

    chunks: list[Chunk] = []
    previous_text_by_path: dict[tuple[str, ...], str] = {}
    for ordinal, (raw_path, group) in enumerate(windows):
        # Most documents open with an H1 that repeats the title; carrying both
        # gives every citation a stutter ("Expenses › Expenses › Per diem") and
        # doubles that phrase's weight in the embedded text.
        path = raw_path[1:] if raw_path and raw_path[0].strip() == doc.title.strip() else raw_path
        body = "\n\n".join(b.text for b in group).strip()
        if not body:
            continue
        overlap = ""
        if config.overlap_tokens and raw_path in previous_text_by_path:
            # Only overlap within a section — bleeding the tail of "Security"
            # into the head of "Expenses" pollutes both.
            overlap = _tail_overlap(previous_text_by_path[raw_path], config.overlap_tokens)
        text = f"{overlap}\n\n{body}".strip() if overlap else body
        previous_text_by_path[raw_path] = body

        content_hash = _content_hash(text)
        chunk_id = make_chunk_id(doc.doc_id, path, ordinal, content_hash)
        kind = _dominant_kind(group)
        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                doc_id=doc.doc_id,
                tenant_id=doc.tenant_id,
                text=text,
                embed_text=_contextualize(doc, path, text, config),
                title=doc.title,
                uri=doc.uri,
                heading_path=path,
                ordinal=ordinal,
                token_count=estimate_tokens(text),
                content_hash=content_hash,
                source=doc.source,
                visibility=doc.visibility,
                acl_groups=doc.acl_groups,
                updated_at=doc.updated_at,
                kind=kind,
            )
        )
    return chunks


def _merge_undersized(
    windows: list[tuple[tuple[str, ...], list[Block]]], config: ChunkingConfig
) -> list[tuple[tuple[str, ...], list[Block]]]:
    """A 12-token chunk ("See the table above.") is retrieval noise: it has a
    high lexical score on almost nothing and no standalone meaning."""
    merged: list[tuple[tuple[str, ...], list[Block]]] = []
    for path, group in windows:
        tokens = sum(b.tokens for b in group)
        if (
            merged
            and tokens < config.min_tokens
            and merged[-1][0] == path
            and sum(b.tokens for b in merged[-1][1]) + tokens <= config.max_tokens
        ):
            merged[-1] = (path, merged[-1][1] + group)
        else:
            merged.append((path, group))
    return merged


def _dominant_kind(group: list[Block]) -> Literal["prose", "code", "table", "list"]:
    if any(b.kind == "table" for b in group):
        return "table"
    if any(b.kind == "code" for b in group):
        return "code"
    if group and all(b.kind == "list" for b in group):
        return "list"
    return "prose"


def _contextualize(
    doc: Document, path: tuple[str, ...], text: str, config: ChunkingConfig
) -> str:
    if not config.contextualize:
        return text
    header = " › ".join((doc.title, *path))
    return f"{header}\n\n{text}"
