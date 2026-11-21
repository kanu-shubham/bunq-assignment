"""Skill #2: retrieval for RAG.

Splits markdown help docs into sections and ranks them with BM25.
BM25 is a strong, dependency-free baseline - in production you'd typically add
embeddings and combine the two ("hybrid search"), then rerank. The interface
(`search(query, k) -> list[DocChunk]`) stays the same, so swapping is cheap.
"""
import math
import re
from collections import Counter
from pathlib import Path

from . import config
from .models import DocChunk

STOPWORDS = set("a an and are can do does for from how i in is it my of on or the to what when with you your".split())


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [t[:-1] if t.endswith("s") and len(t) > 3 else t for t in tokens if t not in STOPWORDS]


def chunk_markdown(path: Path) -> list[DocChunk]:
    """One chunk per '## ' section; the heading is kept as context."""
    chunks, section, lines = [], None, []
    for line in path.read_text().splitlines():
        if line.startswith("## "):
            if section:
                chunks.append(DocChunk(doc=path.stem, section=section, text=" ".join(lines).strip()))
            section, lines = line[3:].strip(), []
        elif section and line.strip():
            lines.append(line.strip())
    if section:
        chunks.append(DocChunk(doc=path.stem, section=section, text=" ".join(lines).strip()))
    return chunks


class BM25Retriever:
    def __init__(self, chunks: list[DocChunk], k1: float = 1.5, b: float = 0.75):
        self.chunks, self.k1, self.b = chunks, k1, b
        # Heading tokens are counted twice: section titles are strong signals.
        self.docs = [tokenize(f"{c.section} {c.section} {c.text}") for c in chunks]
        self.avgdl = sum(map(len, self.docs)) / max(len(self.docs), 1)
        df = Counter(tok for d in self.docs for tok in set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(d) for d in self.docs]

    @classmethod
    def from_dir(cls, directory: Path = config.HELP_DOCS_DIR) -> "BM25Retriever":
        return cls([c for p in sorted(directory.glob("*.md")) for c in chunk_markdown(p)])

    def score(self, query: list[str], i: int) -> float:
        tf, dl, s = self.tf[i], len(self.docs[i]), 0.0
        for t in query:
            if t in tf:
                num = tf[t] * (self.k1 + 1)
                s += self.idf[t] * num / (tf[t] + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
        return s

    def search(self, query: str, k: int = 3) -> list[DocChunk]:
        q = tokenize(query)
        ranked = sorted(range(len(self.chunks)), key=lambda i: self.score(q, i), reverse=True)
        return [self.chunks[i] for i in ranked[:k] if self.score(q, i) > 0]
