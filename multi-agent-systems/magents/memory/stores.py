"""Long-term stores: episodic, semantic, procedural.

No vector DB dependency on purpose. Retrieval here is BM25-ish lexical scoring
plus recency and tag filters, which is:

  * enough to demonstrate the architecture end to end,
  * genuinely competitive with dense retrieval on short, jargon-heavy text like
    incident titles and runbook steps (exact-term matching is a feature when the
    query is "OOMKilled" or "checkout-api"), and
  * swappable — `SemanticStore.search` is the only method a pgvector/Qdrant
    backend would need to reimplement.

The interview point: **retrieval quality is a scoring problem, not a storage
problem.** Recency, scope, confidence, and pinning matter as much as similarity,
and a pure cosine top-k with none of those is the most common broken RAG design.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from collections import Counter as _Counter
from dataclasses import asdict
from typing import Iterable, Sequence

from .types import MemoryRecord, Provenance, Scope, Tier

_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class EpisodicStore:
    """Append-only event log. What happened, in order, with provenance.

    Never mutated: correcting an episode means appending a correction. That is
    what makes it safe to replay a thread and to audit an agent's decisions after
    the fact — the two things an ops team will actually ask you for.
    """

    def __init__(self) -> None:
        self._records: list[MemoryRecord] = []

    def append(self, record: MemoryRecord) -> MemoryRecord:
        record.tier = Tier.EPISODIC
        self._records.append(record)
        return record

    def log(
        self,
        content: str,
        *,
        author: str,
        source: str = "agent",
        scope: Scope = Scope.THREAD,
        tags: Sequence[str] = (),
        **metadata,
    ) -> MemoryRecord:
        return self.append(
            MemoryRecord(
                content=content,
                tier=Tier.EPISODIC,
                scope=scope,
                tags=list(tags),
                metadata=dict(metadata),
                provenance=Provenance(author=author, source=source),
            )
        )

    def recent(
        self, n: int = 10, tags: Sequence[str] = (), scope: Scope | None = None
    ) -> list[MemoryRecord]:
        out = [
            r
            for r in reversed(self._records)
            if (not tags or set(tags) & set(r.tags))
            and (scope is None or r.scope == scope)
            and not r.expired()
        ]
        return out[:n]

    def search(self, query: str, k: int = 5, tags: Sequence[str] = ()) -> list[MemoryRecord]:
        candidates = [r for r in self._records if not tags or set(tags) & set(r.tags)]
        return _rank(query, candidates, k, recency_weight=0.35)

    def __len__(self) -> int:
        return len(self._records)


class SemanticStore:
    """Distilled facts, keyed for upsert.

    The `key` field is what stops the classic failure where an agent writes
    "the checkout service runs 12 replicas" every single run and the store fills
    with 400 near-duplicate rows that then dominate retrieval. Upsert by key,
    keep a version count, and the store stays small and current.
    """

    def __init__(self) -> None:
        self._by_key: dict[str, MemoryRecord] = {}
        self._df: _Counter[str] = _Counter()  # document frequency for IDF
        self._n_docs = 0

    def upsert(self, record: MemoryRecord) -> MemoryRecord:
        record.tier = Tier.SEMANTIC
        key = record.key or record.content_hash
        record.key = key
        existing = self._by_key.get(key)
        if existing is not None:
            if existing.content == record.content:
                existing.touch()
                return existing
            self._unindex(existing)
            record.metadata.setdefault("version", existing.metadata.get("version", 1) + 1)
            record.metadata.setdefault("supersedes", existing.id)
        self._by_key[key] = record
        self._index(record)
        return record

    def get(self, key: str) -> MemoryRecord | None:
        rec = self._by_key.get(key)
        if rec and rec.expired():
            self.forget(key)
            return None
        if rec:
            rec.touch()
        return rec

    def forget(self, key: str) -> bool:
        rec = self._by_key.pop(key, None)
        if rec is None:
            return False
        self._unindex(rec)
        return True

    def search(
        self, query: str, k: int = 5, scopes: Sequence[Scope] = (), min_confidence: float = 0.0
    ) -> list[MemoryRecord]:
        now = time.time()
        candidates = [
            r
            for r in self._by_key.values()
            if not r.expired(now)
            and (not scopes or r.scope in scopes)
            and (r.provenance.confidence if r.provenance else 1.0) >= min_confidence
        ]
        hits = _rank(query, candidates, k, idf=self._idf, recency_weight=0.10)
        for hit in hits:
            hit.touch(now)
        return hits

    def all(self) -> list[MemoryRecord]:
        return list(self._by_key.values())

    def _index(self, record: MemoryRecord) -> None:
        self._n_docs += 1
        for term in set(tokenize(record.content + " " + " ".join(record.tags))):
            self._df[term] += 1

    def _unindex(self, record: MemoryRecord) -> None:
        self._n_docs = max(0, self._n_docs - 1)
        for term in set(tokenize(record.content + " " + " ".join(record.tags))):
            if self._df[term] <= 1:
                del self._df[term]
            else:
                self._df[term] -= 1

    def _idf(self, term: str) -> float:
        return math.log(1 + (self._n_docs + 1) / (1 + self._df.get(term, 0)))

    def __len__(self) -> int:
        return len(self._by_key)


class ProceduralStore:
    """How to act: runbooks, prompt fragments, learned action preferences.

    Versioned and explicitly promoted, never silently overwritten by an agent
    mid-run. An agent that can rewrite its own operating procedure without review
    is one bad generation away from a self-inflicted outage — so writes land as
    *candidates* and a separate promotion step (human, or an eval gate) activates
    them.
    """

    def __init__(self) -> None:
        self._active: dict[str, MemoryRecord] = {}
        self._candidates: dict[str, list[MemoryRecord]] = {}

    def propose(self, name: str, content: str, author: str, rationale: str = "") -> MemoryRecord:
        rec = MemoryRecord(
            content=content,
            tier=Tier.PROCEDURAL,
            scope=Scope.GLOBAL,
            key=name,
            metadata={"status": "candidate", "rationale": rationale},
            provenance=Provenance(author=author, source="reflection", confidence=0.5),
        )
        self._candidates.setdefault(name, []).append(rec)
        return rec

    def promote(self, name: str, record_id: str, approver: str) -> MemoryRecord:
        for rec in self._candidates.get(name, []):
            if rec.id == record_id:
                rec.metadata["status"] = "active"
                rec.metadata["approved_by"] = approver
                rec.metadata["version"] = len(self._active) and (
                    self._active.get(name, rec).metadata.get("version", 0) + 1
                ) or 1
                self._active[name] = rec
                return rec
        raise KeyError(f"no candidate {record_id!r} for procedure {name!r}")

    def get(self, name: str) -> MemoryRecord | None:
        return self._active.get(name)

    def candidates(self, name: str) -> list[MemoryRecord]:
        return list(self._candidates.get(name, []))

    def search(self, query: str, k: int = 3) -> list[MemoryRecord]:
        return _rank(query, list(self._active.values()), k, recency_weight=0.0)


class SqliteSemanticStore(SemanticStore):
    """Durable variant. Same interface; retrieval still happens in Python so the
    scoring logic stays one implementation. Real deployments push scoring into
    the database (pgvector + `ts_rank`, or a hybrid BM25/dense reranker)."""

    def __init__(self, path: str = ":memory:"):
        super().__init__()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS memories ("
            "key TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at REAL NOT NULL)"
        )
        self.conn.commit()
        self._load()

    def upsert(self, record: MemoryRecord) -> MemoryRecord:
        rec = super().upsert(record)
        payload = json.dumps(_to_dict(rec))
        self.conn.execute(
            "INSERT INTO memories(key, payload, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
            (rec.key, payload, time.time()),
        )
        self.conn.commit()
        return rec

    def forget(self, key: str) -> bool:
        removed = super().forget(key)
        if removed:
            self.conn.execute("DELETE FROM memories WHERE key=?", (key,))
            self.conn.commit()
        return removed

    def _load(self) -> None:
        for (payload,) in self.conn.execute("SELECT payload FROM memories"):
            super().upsert(_from_dict(json.loads(payload)))


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
def _rank(
    query: str,
    candidates: Iterable[MemoryRecord],
    k: int,
    idf=None,
    recency_weight: float = 0.2,
    half_life_hours: float = 72.0,
) -> list[MemoryRecord]:
    """Hybrid score = lexical overlap + recency + confidence.

    Weighting is deliberately explicit and tunable rather than hidden inside a
    vector index — when retrieval regresses, you need to be able to say which
    term caused it.
    """
    terms = tokenize(query)
    if not terms:
        return []
    now = time.time()
    scored: list[tuple[float, MemoryRecord]] = []
    for rec in candidates:
        doc = tokenize(rec.content + " " + " ".join(rec.tags))
        if not doc:
            continue
        counts = _Counter(doc)
        lexical = 0.0
        for term in terms:
            tf = counts.get(term, 0)
            if not tf:
                continue
            weight = idf(term) if idf else 1.0
            # Saturating tf, like BM25 — a term appearing 50 times is not 50x
            # more relevant than appearing once.
            lexical += weight * (tf / (tf + 1.2))
        if lexical <= 0:
            continue
        lexical /= math.sqrt(len(terms))
        age_h = rec.age_seconds(now) / 3600
        recency = 0.5 ** (age_h / half_life_hours)
        confidence = rec.provenance.confidence if rec.provenance else 1.0
        score = lexical * (1 - recency_weight) + recency * recency_weight
        scored.append((score * confidence, rec))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [rec for _, rec in scored[:k]]


def _to_dict(rec: MemoryRecord) -> dict:
    data = asdict(rec)
    data["tier"] = rec.tier.value
    data["scope"] = rec.scope.value
    return data


def _from_dict(data: dict) -> MemoryRecord:
    prov = data.pop("provenance", None)
    rec = MemoryRecord(
        **{
            **data,
            "tier": Tier(data["tier"]),
            "scope": Scope(data["scope"]),
            "provenance": Provenance(**prov) if prov else None,
        }
    )
    return rec
