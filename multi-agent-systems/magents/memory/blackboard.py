"""Shared memory between agents — the blackboard.

Once more than one agent writes to the same state, you have a distributed
systems problem wearing a trench coat. Three concrete hazards:

  1. **Lost update.** Two agents read version 3, both write, one write vanishes.
     Fix: compare-and-set on a version number (optimistic concurrency).
  2. **Context poisoning.** One agent writes a hallucination; every other agent
     reads it as fact and the error compounds across the whole run. Fix: scope +
     provenance + confidence on every entry, and never let an inference be
     indistinguishable from an observation.
  3. **Unbounded growth.** Every agent appends its full reasoning, and the
     blackboard becomes larger than any agent's context window. Fix: agents
     publish *conclusions*, not transcripts; enforce a per-key size cap.

Message passing vs blackboard, since it always comes up: message passing gives
you isolation and clear causality, but N agents needing the same fact means N
copies and N chances to diverge. A blackboard gives one source of truth and
cheap fan-out, at the cost of needing concurrency control. Most real systems use
both — blackboard for shared task state, messages for handoffs.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .types import Provenance, Scope


class ConflictError(RuntimeError):
    """Raised on a failed compare-and-set. The caller must re-read and retry —
    surfacing the conflict beats silently clobbering another agent's work."""


@dataclass
class Entry:
    key: str
    value: Any
    version: int
    author: str
    scope: Scope = Scope.THREAD
    confidence: float = 1.0
    provenance: Provenance | None = None
    updated_at: float = field(default_factory=time.time)


@dataclass
class Event:
    key: str
    version: int
    author: str
    at: float = field(default_factory=time.time)


class Blackboard:
    """Versioned key/value shared state with scoping and change notification."""

    def __init__(self, max_value_chars: int = 8000):
        self._entries: dict[str, Entry] = {}
        self._lock = threading.RLock()
        self._history: list[Event] = []
        self._subscribers: list[tuple[Callable[[str], bool], Callable[[Event], None]]] = []
        self.max_value_chars = max_value_chars

    # -- reads -------------------------------------------------------------
    def get(self, key: str, reader_scopes: Iterable[Scope] | None = None) -> Entry | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if reader_scopes is not None and entry.scope not in set(reader_scopes):
                return None  # scope check is a read filter, not a write filter
            return entry

    def view(
        self, reader_scopes: Iterable[Scope] | None = None, min_confidence: float = 0.0
    ) -> dict[str, Any]:
        """A read-only snapshot for injecting into an agent's context."""
        scopes = set(reader_scopes) if reader_scopes is not None else None
        with self._lock:
            return {
                k: e.value
                for k, e in self._entries.items()
                if (scopes is None or e.scope in scopes) and e.confidence >= min_confidence
            }

    # -- writes ------------------------------------------------------------
    def put(
        self,
        key: str,
        value: Any,
        *,
        author: str,
        expected_version: int | None = None,
        scope: Scope = Scope.THREAD,
        confidence: float = 1.0,
        provenance: Provenance | None = None,
    ) -> Entry:
        """Compare-and-set write.

        `expected_version=None` means blind write (last-write-wins) — acceptable
        for a key with a single known writer, wrong for anything contended.
        Pass the version you read to get conflict detection.
        """
        text = value if isinstance(value, str) else repr(value)
        if len(text) > self.max_value_chars:
            raise ValueError(
                f"blackboard value for {key!r} is {len(text)} chars "
                f"(cap {self.max_value_chars}) — publish a conclusion, not a transcript"
            )
        with self._lock:
            current = self._entries.get(key)
            current_version = current.version if current else 0
            if expected_version is not None and expected_version != current_version:
                raise ConflictError(
                    f"{key!r}: expected version {expected_version}, found {current_version} "
                    f"(last written by {current.author if current else 'nobody'})"
                )
            entry = Entry(
                key=key,
                value=value,
                version=current_version + 1,
                author=author,
                scope=scope,
                confidence=confidence,
                provenance=provenance,
            )
            self._entries[key] = entry
            event = Event(key, entry.version, author)
            self._history.append(event)
        self._notify(event)
        return entry

    def update(
        self, key: str, fn: Callable[[Any], Any], *, author: str, retries: int = 3, **kwargs
    ) -> Entry:
        """Read-modify-write with automatic CAS retry — the safe default for
        counters, accumulating lists, and anything two agents touch."""
        for attempt in range(retries + 1):
            current = self.get(key)
            version = current.version if current else 0
            try:
                return self.put(
                    key,
                    fn(current.value if current else None),
                    author=author,
                    expected_version=version,
                    **kwargs,
                )
            except ConflictError:
                if attempt == retries:
                    raise
                time.sleep(0.001 * (2**attempt))  # backoff; contention is usually brief
        raise AssertionError("unreachable")

    # -- notification ------------------------------------------------------
    def subscribe(self, predicate: Callable[[str], bool], handler: Callable[[Event], None]) -> None:
        """Event-driven coordination: an agent wakes when a key it depends on
        changes, instead of polling. This is also where deadlocks are born — see
        coordination/locks.py for the "everyone waits for a key nobody will
        write" case."""
        with self._lock:
            self._subscribers.append((predicate, handler))

    def _notify(self, event: Event) -> None:
        for predicate, handler in list(self._subscribers):
            if predicate(event.key):
                handler(event)

    def history(self, key: str | None = None) -> list[Event]:
        with self._lock:
            return [e for e in self._history if key is None or e.key == key]
