"""Working memory: the context window, managed as a budget rather than a list.

The core claim: **context is a scarce, priced resource and must be allocated,
not accumulated.** An agent loop that appends every tool result until something
breaks has three failure modes, in the order you hit them:

  1. Cost — you resend the whole transcript every turn, so an N-turn task is
     O(N²) in tokens.
  2. Quality — "lost in the middle": recall of facts buried in the centre of a
     long context is measurably worse than at the edges.
  3. Hard failure — the request 400s, or `max_tokens` truncates the answer
     mid-thought.

The budget model here splits the window into fixed *segments* with reserved
floors, so a flood of tool output can never evict the system prompt or the
user's actual question. This is the same shape as an OS memory allocator with
per-zone reservations, and it is what "working memory management" means in
practice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .types import MemoryRecord, Scope, Tier

Counter = Callable[[str], int]


def _default_counter(text: str) -> int:
    return max(1, len(text) // 4)


class Segment(str):
    pass


SYSTEM = "system"  # instructions, tool schemas — never evicted
PINNED = "pinned"  # task goal, constraints, approved plan
RETRIEVED = "retrieved"  # RAG / memory hits for this turn
HISTORY = "history"  # rolling conversation + tool results
SCRATCH = "scratch"  # this-turn intermediates, dropped first


@dataclass
class BudgetPolicy:
    """Fractions of the window per segment. They are *floors* for SYSTEM and
    PINNED (guaranteed) and *caps* for the rest (best effort)."""

    total_tokens: int = 180_000
    reserve_output: int = 32_000  # thinking + response share max_tokens with input
    fractions: dict[str, float] = field(
        default_factory=lambda: {
            SYSTEM: 0.10,
            PINNED: 0.10,
            RETRIEVED: 0.25,
            HISTORY: 0.45,
            SCRATCH: 0.10,
        }
    )

    @property
    def input_budget(self) -> int:
        return max(1, self.total_tokens - self.reserve_output)

    def cap(self, segment: str) -> int:
        return int(self.input_budget * self.fractions.get(segment, 0.0))


@dataclass
class Item:
    text: str
    segment: str
    tokens: int
    priority: float = 0.5  # 0..1, higher survives eviction
    pinned: bool = False
    ref: MemoryRecord | None = None
    seq: int = 0


@dataclass
class BudgetReport:
    used: dict[str, int]
    caps: dict[str, int]
    evicted: list[Item]
    summarized: int = 0

    @property
    def total_used(self) -> int:
        return sum(self.used.values())

    def pretty(self) -> str:
        rows = [f"{'segment':<10} {'used':>8} {'cap':>8}  util"]
        for seg in (SYSTEM, PINNED, RETRIEVED, HISTORY, SCRATCH):
            used, cap = self.used.get(seg, 0), self.caps.get(seg, 0)
            pct = (used / cap * 100) if cap else 0.0
            rows.append(f"{seg:<10} {used:>8} {cap:>8}  {pct:5.1f}%")
        rows.append(f"{'TOTAL':<10} {self.total_used:>8}")
        if self.evicted:
            rows.append(f"evicted: {len(self.evicted)} item(s), {sum(i.tokens for i in self.evicted)} tokens")
        return "\n".join(rows)


class WorkingMemory:
    """A token-budgeted context assembler.

    Usage is deliberately narrow: you `add()` candidate content and call
    `render()` to get the messages that fit. Nothing else in the system decides
    what goes into the prompt — one place to reason about, one place to test.
    """

    def __init__(
        self,
        policy: BudgetPolicy | None = None,
        counter: Counter | None = None,
        summarizer: Callable[[list[Item]], str] | None = None,
    ):
        self.policy = policy or BudgetPolicy()
        self.count = counter or _default_counter
        self.summarizer = summarizer
        self.items: list[Item] = []
        self._seq = 0

    # -- writes ------------------------------------------------------------
    def add(
        self,
        text: str,
        segment: str = HISTORY,
        priority: float = 0.5,
        pinned: bool = False,
        ref: MemoryRecord | None = None,
    ) -> Item:
        self._seq += 1
        item = Item(
            text=text,
            segment=segment,
            tokens=self.count(text),
            priority=priority,
            pinned=pinned or segment in (SYSTEM, PINNED),
            ref=ref,
            seq=self._seq,
        )
        self.items.append(item)
        return item

    def add_records(self, records: Iterable[MemoryRecord], segment: str = RETRIEVED) -> None:
        for rec in records:
            # Retrieved memories carry their confidence into eviction priority:
            # a low-confidence inference should be the first thing dropped.
            conf = rec.provenance.confidence if rec.provenance else 0.6
            self.add(rec.content, segment=segment, priority=conf, ref=rec)

    def clear(self, segment: str) -> None:
        self.items = [i for i in self.items if i.segment != segment]

    # -- eviction ----------------------------------------------------------
    def _score(self, item: Item) -> float:
        """Eviction score — lower is dropped first.

        Three signals, matching how humans triage notes: how important it was
        declared to be, how recently it arrived, and how big it is. Size matters
        because evicting one 8k-token tool dump beats evicting twenty useful
        one-liners.
        """
        recency = item.seq / max(1, self._seq)  # 0..1
        size_penalty = min(1.0, item.tokens / 4000)
        return item.priority * 0.6 + recency * 0.4 - size_penalty * 0.2

    def fit(self) -> BudgetReport:
        """Enforce per-segment caps. Returns what was dropped so the caller can
        log it — silent context loss is the single hardest agent bug to debug."""
        caps = {seg: self.policy.cap(seg) for seg in self.policy.fractions}
        kept: list[Item] = []
        evicted: list[Item] = []
        summarized = 0

        by_segment: dict[str, list[Item]] = {}
        for item in self.items:
            by_segment.setdefault(item.segment, []).append(item)

        for segment, items in by_segment.items():
            cap = caps.get(segment, 0)
            used = 0
            # Pinned content is admitted first and unconditionally; it can push a
            # segment over cap, which is correct — better to overrun than to drop
            # the task goal.
            ordered = sorted(items, key=lambda i: (not i.pinned, -self._score(i)))
            overflow: list[Item] = []
            for item in ordered:
                if item.pinned or used + item.tokens <= cap:
                    kept.append(item)
                    used += item.tokens
                else:
                    overflow.append(item)

            if overflow and self.summarizer is not None:
                # Compaction beats deletion: replace N dropped items with one
                # summary, as long as the summary itself fits the remaining room.
                digest = self.summarizer(sorted(overflow, key=lambda i: i.seq))
                tokens = self.count(digest)
                if tokens <= max(0, cap - used):
                    self._seq += 1
                    kept.append(
                        Item(digest, segment, tokens, priority=0.7, seq=self._seq)
                    )
                    summarized = len(overflow)
                    used += tokens
                    overflow = []
            evicted.extend(overflow)

        self.items = sorted(kept, key=lambda i: i.seq)
        used_by_segment: dict[str, int] = {}
        for item in self.items:
            used_by_segment[item.segment] = used_by_segment.get(item.segment, 0) + item.tokens
        return BudgetReport(used_by_segment, caps, evicted, summarized)

    # -- reads -------------------------------------------------------------
    def render(self) -> tuple[str, list[dict[str, Any]]]:
        """Produce (system, messages).

        Ordering is load-bearing for two separate reasons:
          * Cache: the prefix must be byte-stable, so SYSTEM goes first and
            nothing volatile (timestamps, request ids) may precede it.
          * Recall: the task goal is repeated at the end, because instructions at
            the very end of a long context are followed most reliably.
        """
        self.fit()
        system = "\n\n".join(i.text for i in self.items if i.segment == SYSTEM)

        blocks: list[str] = []
        for segment, header in (
            (PINNED, "## Task"),
            (RETRIEVED, "## Retrieved context"),
            (HISTORY, "## History"),
            (SCRATCH, "## Scratch"),
        ):
            texts = [i.text for i in self.items if i.segment == segment]
            if texts:
                blocks.append(header + "\n" + "\n\n".join(texts))

        pinned = [i.text for i in self.items if i.segment == PINNED]
        if pinned:
            blocks.append("## Reminder of the task\n" + "\n".join(pinned))

        return system, [{"role": "user", "content": "\n\n".join(blocks)}]

    def usage(self) -> BudgetReport:
        caps = {seg: self.policy.cap(seg) for seg in self.policy.fractions}
        used: dict[str, int] = {}
        for item in self.items:
            used[item.segment] = used.get(item.segment, 0) + item.tokens
        return BudgetReport(used, caps, [])


def make_working_memory(
    system_prompt: str,
    task: str,
    policy: BudgetPolicy | None = None,
    counter: Counter | None = None,
    summarizer: Callable[[list[Item]], str] | None = None,
) -> WorkingMemory:
    wm = WorkingMemory(policy, counter, summarizer)
    wm.add(system_prompt, segment=SYSTEM, priority=1.0, pinned=True)
    wm.add(task, segment=PINNED, priority=1.0, pinned=True)
    return wm


__all__ = [
    "BudgetPolicy",
    "BudgetReport",
    "HISTORY",
    "Item",
    "PINNED",
    "RETRIEVED",
    "SCRATCH",
    "SYSTEM",
    "Scope",
    "Tier",
    "WorkingMemory",
    "make_working_memory",
]
