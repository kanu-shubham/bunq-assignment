"""Loading a documentation dump off disk.

Markdown with YAML-ish frontmatter is what you actually get when you export
Notion, Confluence, or a pile of service READMEs. The frontmatter parser here is
deliberately a small subset (scalars, inline lists) rather than a PyYAML
dependency — the metadata schema is ours, and a 30-line parser that fails loudly
on anything unexpected is easier to trust than a general one that silently
coerces ``updated: 2026-05-02`` into a ``datetime``.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any, Iterable

from .types import Document

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_LIST_RE = re.compile(r"\A\[(.*)\]\Z")


def _parse_scalar(raw: str) -> Any:
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] in "\"'" and raw[-1] == raw[0] and len(raw) > 1:
        return raw[1:-1]
    m = _LIST_RE.match(raw)
    if m:
        inner = m.group(1).strip()
        if not inner:
            return []
        return [_parse_scalar(part) for part in inner.split(",")]
    return raw


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split ``(metadata, body)``. No frontmatter yields ``({}, text)``."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    meta: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        line = line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"malformed frontmatter line: {line!r}")
        key, _, value = line.partition(":")
        meta[key.strip()] = _parse_scalar(value)
    return meta, text[match.end() :]


def load_corpus(root: str | pathlib.Path, *, pattern: str = "*.md") -> list[Document]:
    """Load every markdown file under ``root`` into a ``Document``.

    ``doc_id`` is the filename stem, which keeps evaluation labels readable and
    stable — an id derived from a content hash is tidier right up until you have
    to hand-label 60 queries against it.
    """
    root = pathlib.Path(root)
    if not root.exists():
        raise FileNotFoundError(f"corpus root does not exist: {root}")

    docs: list[Document] = []
    for path in sorted(root.rglob(pattern)):
        raw = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(raw)
        title = str(meta.get("title") or _first_heading(body) or path.stem)
        meta = dict(meta)
        meta.setdefault("tags", [])
        docs.append(
            Document(
                doc_id=path.stem,
                title=title,
                text=body.strip(),
                path=str(path),
                metadata=meta,
            )
        )
    if not docs:
        raise ValueError(f"no documents matched {pattern!r} under {root}")
    return docs


def _first_heading(body: str) -> str | None:
    for line in body.splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return None


def corpus_stats(docs: Iterable[Document]) -> dict[str, Any]:
    docs = list(docs)
    by_source: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for d in docs:
        by_source[str(d.metadata.get("source", "?"))] = by_source.get(str(d.metadata.get("source", "?")), 0) + 1
        by_type[str(d.metadata.get("doc_type", "?"))] = by_type.get(str(d.metadata.get("doc_type", "?")), 0) + 1
    return {
        "documents": len(docs),
        "characters": sum(len(d.text) for d in docs),
        "by_source": dict(sorted(by_source.items())),
        "by_doc_type": dict(sorted(by_type.items())),
    }
