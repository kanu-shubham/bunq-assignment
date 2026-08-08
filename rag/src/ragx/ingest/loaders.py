"""Loaders turn a source into `Document`s.

Only Markdown-with-front-matter and plain text are implemented here; real
connectors (Confluence, Drive, Notion, GitHub) plug in behind the same
`load()` signature and are responsible for two things this layer already
models: a stable `doc_id` and the document's ACL.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from ..types import Document, Visibility

_FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """A deliberately tiny YAML subset: `key: value` pairs, no nesting.

    Keeping this dependency-free means ingestion has no YAML parser in its
    trust boundary for documents that may come from untrusted authors.
    """
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        if _:
            meta[key.strip().lower()] = value.strip().strip("\"'")
    return meta, text[match.end() :]


def stable_doc_id(tenant_id: str, uri: str) -> str:
    return hashlib.sha256(f"{tenant_id}\x00{uri}".encode()).hexdigest()[:16]


def _parse_groups(raw: str | None) -> frozenset[str]:
    if not raw:
        return frozenset()
    return frozenset(g.strip() for g in raw.split(",") if g.strip())


def _parse_updated_at(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def load_markdown_file(path: Path, tenant_id: str, source: str = "corpus") -> Document:
    raw = path.read_text(encoding="utf-8")
    meta, body = parse_front_matter(raw)
    uri = meta.get("uri") or f"file://{path.as_posix()}"
    title = meta.get("title") or _first_heading(body) or path.stem.replace("-", " ").title()
    visibility = Visibility(meta.get("visibility", Visibility.INTERNAL.value))
    return Document(
        doc_id=meta.get("doc_id") or stable_doc_id(tenant_id, uri),
        tenant_id=tenant_id,
        title=title,
        text=body.strip("\n"),
        uri=uri,
        source=meta.get("source", source),
        visibility=visibility,
        acl_groups=_parse_groups(meta.get("acl_groups")),
        updated_at=_parse_updated_at(meta.get("updated_at")),
        version=meta.get("version"),
        extra={k: v for k, v in meta.items() if k.startswith("x_")},
    )


def load_directory(
    root: Path, tenant_id: str, pattern: str = "**/*.md", source: str = "corpus"
) -> Iterator[Document]:
    for path in sorted(root.glob(pattern)):
        if path.is_file():
            yield load_markdown_file(path, tenant_id=tenant_id, source=source)


def _first_heading(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None
