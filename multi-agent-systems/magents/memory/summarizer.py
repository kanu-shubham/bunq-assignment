"""Summarization and write strategies — when memory gets written, and how.

Two questions, often conflated:

  1. **When do we compress the transcript?** (summarization / compaction)
  2. **When do we persist a durable memory?** (write strategy)

## 1. Compression

  strategy      what it does                       loses                  use when
  -----------   --------------------------------   --------------------   --------------
  truncate      drop oldest turns                  everything old         cheap chat
  rolling       summarize oldest N, keep tail       detail, keeps gist     long dialogue
  hierarchical  summaries of summaries              fine detail            multi-day runs
  extractive    keep only high-salience spans       narrative flow         tool-heavy loops
  structured    fold turns into a typed state doc   free-form nuance       agentic workflows

`structured` is the one people under-use and the one that works best for agents:
instead of prose, maintain a JSON state document ("what we know / what we tried /
what's left") and rewrite it each turn. It compresses better than prose, is
diffable, and cannot silently drop a field the way a prose summary can.

Note: the Claude API also offers *server-side* compaction (beta
`compact-2026-01-12`), which summarizes earlier context for you. Critical detail
if you use it — append the whole `response.content` back to your messages, not
just the text, or you drop the compaction block and lose the state.

## 2. Write strategies (borrowed from cache design, and the analogy holds)

  write-through   persist immediately on every observation.
                  Durable, no loss on crash; noisy store, high write cost.
  write-back      buffer in working memory, flush at end of turn/task.
                  Cheap and clean; loses the buffer if the process dies.
  write-around    persist only what passes a salience filter.
                  Keeps the store small; risks dropping something needed later.
  reflective      a separate pass reviews the episode and writes distilled facts.
                  Highest quality, highest latency — run it off the hot path.

Production answer for an agent: write-back for episodic (flush at turn end),
reflective for semantic (async job), write-around for anything expensive to
store. Never write-through to semantic memory from inside the agent loop — that
is how you get a knowledge base full of the model's own hallucinations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol, Sequence

from ..llm import LLM, estimate_tokens
from .stores import EpisodicStore, SemanticStore
from .types import MemoryRecord, Provenance, Scope, Tier
from .working import Item, WorkingMemory

SUMMARY_SYSTEM = """You compress agent transcripts for reuse as context.

Preserve, in this order of priority:
1. Decisions made and their stated reasons.
2. Facts discovered, each with where it came from.
3. Actions taken and their outcomes (including failures).
4. Open questions and what was ruled out.

Drop: restated instructions, narration of intent, tool call boilerplate, and
anything the reader can re-derive. Write compact prose, no preamble."""

FACT_SYSTEM = """Extract durable facts from this episode.

A fact is durable if it will still be true and still be useful next week. Skip
anything transient (current CPU%, a one-off request id) and anything you inferred
rather than observed.

Return JSON: {"facts": [{"key": "...", "content": "...", "confidence": 0.0-1.0,
"tags": ["..."]}]}. `key` is a stable slug used for upsert, e.g.
"service:checkout-api:owner"."""


class Summarizer(Protocol):
    def __call__(self, items: Sequence[Item]) -> str: ...


def truncating_summarizer(max_chars: int = 400) -> Summarizer:
    """No model call. Useful as a default and in tests — a summarizer that needs
    the network makes your context-budget tests flaky."""

    def _summarize(items: Sequence[Item]) -> str:
        joined = " | ".join(i.text.replace("\n", " ")[:120] for i in items)
        body = joined[:max_chars]
        return f"[compacted {len(items)} item(s)] {body}"

    return _summarize


def llm_summarizer(llm: LLM, max_tokens: int = 1024) -> Summarizer:
    def _summarize(items: Sequence[Item]) -> str:
        transcript = "\n\n".join(i.text for i in items)
        out = llm.complete(
            SUMMARY_SYSTEM,
            [{"role": "user", "content": transcript}],
            max_tokens=max_tokens,
            effort="low",  # compression is not intelligence-sensitive
        )
        return f"[summary of {len(items)} item(s)]\n{out.text}"

    return _summarize


@dataclass
class StructuredState:
    """The `structured` compression strategy, as a typed document.

    Rendering this is O(state), not O(turns) — which is the whole point. A
    50-turn incident and a 5-turn incident produce the same size context.
    """

    goal: str = ""
    facts: list[str] = field(default_factory=list)
    attempted: list[str] = field(default_factory=list)
    ruled_out: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)

    def render(self) -> str:
        def block(title: str, rows: list[str]) -> str:
            return f"{title}:\n" + ("\n".join(f"  - {r}" for r in rows) if rows else "  (none)")

        return "\n".join(
            [
                f"GOAL: {self.goal}",
                block("KNOWN", self.facts),
                block("TRIED", self.attempted),
                block("RULED OUT", self.ruled_out),
                block("OPEN", self.open_questions),
            ]
        )

    def tokens(self) -> int:
        return estimate_tokens(self.render())


@dataclass
class WriteBackBuffer:
    """Write-back for episodic memory: accumulate during the turn, flush once.

    `flush` is idempotent by content hash so a retried turn does not duplicate
    the episode — the retry-safety property people forget until they see the same
    incident logged four times.
    """

    episodic: EpisodicStore
    author: str
    _pending: list[MemoryRecord] = field(default_factory=list)
    _flushed: set[str] = field(default_factory=set)

    def record(self, content: str, *, source: str = "agent", tags: Sequence[str] = (), **meta):
        self._pending.append(
            MemoryRecord(
                content=content,
                tier=Tier.EPISODIC,
                tags=list(tags),
                metadata=dict(meta),
                provenance=Provenance(author=self.author, source=source),
            )
        )

    def flush(self) -> int:
        written = 0
        for rec in self._pending:
            if rec.content_hash in self._flushed:
                continue
            self.episodic.append(rec)
            self._flushed.add(rec.content_hash)
            written += 1
        self._pending.clear()
        return written


class ReflectiveWriter:
    """Off-the-hot-path consolidation: episode -> durable facts.

    Two guards that keep the semantic store trustworthy:
      * a confidence floor, so speculative extractions are dropped, and
      * provenance on every write, so a fact can be traced back to the episode
        and revoked if that episode turns out to be wrong.
    """

    def __init__(
        self,
        llm: LLM,
        semantic: SemanticStore,
        min_confidence: float = 0.6,
        salience: Callable[[str], bool] | None = None,
    ):
        self.llm = llm
        self.semantic = semantic
        self.min_confidence = min_confidence
        self.salience = salience

    def reflect(self, episode: str, author: str, source: str, tags: Sequence[str] = ()) -> list[MemoryRecord]:
        out = self.llm.complete(
            FACT_SYSTEM, [{"role": "user", "content": episode}], max_tokens=2048, effort="medium"
        )
        payload = out.json({"facts": []}) or {"facts": []}
        written: list[MemoryRecord] = []
        for fact in payload.get("facts", []):
            content = str(fact.get("content", "")).strip()
            confidence = float(fact.get("confidence", 0.0))
            if not content or confidence < self.min_confidence:
                continue
            if self.salience and not self.salience(content):
                continue  # write-around: filter before persisting
            written.append(
                self.semantic.upsert(
                    MemoryRecord(
                        content=content,
                        tier=Tier.SEMANTIC,
                        scope=Scope.GLOBAL,
                        key=fact.get("key") or None,
                        tags=list(fact.get("tags", [])) + list(tags),
                        provenance=Provenance(
                            author=author, source=source, confidence=confidence
                        ),
                    )
                )
            )
        return written


def compact_working_memory(wm: WorkingMemory, summarizer: Summarizer, keep_tail: int = 4) -> int:
    """Rolling compaction: fold everything but the last `keep_tail` history items
    into one summary. Returns tokens reclaimed.

    Keeping a verbatim tail matters — the model needs the exact text of the most
    recent tool result to act on it, and a summary of "the API returned an error"
    is not something you can retry against.
    """
    from .working import HISTORY  # local import keeps the module import graph flat

    history = [i for i in wm.items if i.segment == HISTORY]
    if len(history) <= keep_tail:
        return 0
    old, tail = history[:-keep_tail], history[-keep_tail:]
    before = sum(i.tokens for i in old)
    digest = summarizer(old)
    wm.items = [i for i in wm.items if i.segment != HISTORY]
    # Reuse the first compacted item's sequence number so the digest keeps its
    # chronological position — a summary of turns 1-9 rendered after turn 12
    # tells the model the wrong story about ordering.
    wm.items.append(Item(digest, HISTORY, wm.count(digest), priority=0.8, seq=old[0].seq))
    wm.items.extend(tail)
    wm.items.sort(key=lambda i: i.seq)
    return before - wm.count(digest)
