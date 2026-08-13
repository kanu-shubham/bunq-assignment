"""Memory records and the taxonomy every agent memory design starts from.

The four-tier split below is the standard interview answer, borrowed from
cognitive psychology because the failure modes line up:

  working    — what is in the context window *right now*. Fast, tiny, volatile.
  episodic   — what happened. Timestamped, append-only, per-thread or per-entity.
  semantic   — what is true. Distilled facts, runbooks, entity profiles.
  procedural — how to act. Prompts, policies, learned action preferences.

The mistake to avoid is treating "memory" as one vector store. Each tier has a
different write path, retention, retrieval key, and consistency requirement:

  tier         write             retention   retrieval        consistency
  ----------   ---------------   ---------   --------------   -------------
  working      every turn        seconds     positional       strong (single writer)
  episodic     end of turn       months      recency + filter append-only
  semantic     on reflection     indefinite  similarity       eventually consistent
  procedural   on eval/promote   versioned   exact by name    strongly versioned
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Tier(str, Enum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


class Scope(str, Enum):
    """Who can read a record. Getting this wrong is a privacy incident, not a
    quality bug — in a shared blackboard, agent A's tenant data must not surface
    in agent B's context."""

    AGENT = "agent"  # private to one agent
    THREAD = "thread"  # shared within one task/conversation
    TENANT = "tenant"  # shared across tasks for one customer
    GLOBAL = "global"  # org-wide knowledge (runbooks, policies)


@dataclass
class Provenance:
    """Where a memory came from. Without this you cannot answer "why did the
    agent believe that?", cannot expire records when the source is retracted,
    and cannot stop a hallucination from being written back as a fact."""

    author: str  # agent or human id
    source: str  # tool name, url, incident id, "inference"
    confidence: float = 1.0
    derived_from: list[str] = field(default_factory=list)  # upstream record ids


@dataclass
class MemoryRecord:
    content: str
    tier: Tier = Tier.EPISODIC
    scope: Scope = Scope.THREAD
    key: str | None = None  # stable identity for upsert/dedupe
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: Provenance | None = None
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0
    ttl_seconds: float | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode()).hexdigest()[:16]

    def expired(self, now: float | None = None) -> bool:
        if self.ttl_seconds is None:
            return False
        return (now or time.time()) - self.created_at > self.ttl_seconds

    def touch(self, now: float | None = None) -> None:
        self.last_accessed = now or time.time()
        self.access_count += 1

    def age_seconds(self, now: float | None = None) -> float:
        return (now or time.time()) - self.created_at
