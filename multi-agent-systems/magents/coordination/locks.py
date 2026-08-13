"""Deadlocks and liveness in multi-agent systems.

Classic deadlock needs all four Coffman conditions to hold at once:

  1. mutual exclusion   — a resource is held exclusively
  2. hold and wait      — an agent holds one resource while waiting for another
  3. no preemption      — a resource can't be forcibly taken back
  4. circular wait      — a cycle exists in the wait-for graph

Break any one and deadlock is impossible. This module implements the three
practical breaks, plus detection for when you cannot guarantee prevention:

  * `ResourceManager(ordered=True)` breaks **circular wait** — global lock
    ordering. Cheapest and strongest; use it whenever you can enumerate
    resources. This is the answer to give first.
  * `timeout=` breaks **no preemption** — bounded waits turn a deadlock into a
    retryable failure. Always set one, even with ordering, as a backstop.
  * `acquire_all()` breaks **hold and wait** — all-or-nothing acquisition.
  * `WaitForGraph` **detects** cycles so you can abort a victim, for the cases
    where lock order isn't knowable up front.

But the deadlocks that actually bite an LLM multi-agent system are usually not
about mutexes at all. Four that matter more:

  * **Semantic deadlock.** A waits for B's output, B waits for A's, and neither
    holds a lock — the cycle is in the task graph. Detected here by
    `WaitForGraph` too; prevented by making the dependency DAG explicit and
    validating it before dispatch.
  * **Approval deadlock.** The agent pauses for a human who never comes back.
    This is the most common production hang. Fix: deadline on every gate plus a
    default action (usually "abort", sometimes "proceed read-only").
  * **Livelock.** No one is blocked; two agents just keep undoing each other —
    critic and refiner oscillating, or two remediation agents scaling a
    deployment up and down. Fix: bounded iterations, a monotone progress
    measure, and jittered backoff. Detected here by `LivelockDetector`.
  * **Starvation.** A low-priority agent never gets the lock. Fix: FIFO queueing
    (what `ResourceManager` does) or aging.
"""

from __future__ import annotations

import random
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Hashable, Iterable, Iterator, Sequence


class DeadlockError(RuntimeError):
    def __init__(self, cycle: Sequence[str]):
        super().__init__("deadlock: " + " -> ".join(list(cycle) + [cycle[0]]))
        self.cycle = list(cycle)


class LockTimeout(TimeoutError):
    def __init__(self, agent: str, resource: str, waited: float, holder: str | None):
        super().__init__(
            f"{agent!r} waited {waited:.2f}s for {resource!r} "
            f"(held by {holder!r})" if holder else f"{agent!r} waited {waited:.2f}s for {resource!r}"
        )
        self.agent, self.resource, self.holder = agent, resource, holder


# --------------------------------------------------------------------------
# Wait-for graph — detection
# --------------------------------------------------------------------------
class WaitForGraph:
    """Directed graph of "agent A is blocked on agent B". A cycle is a deadlock.

    Works for resource locks *and* for task dependencies — same algorithm, and
    that generality is the point worth making: semantic deadlock between agents
    is structurally identical to lock deadlock.
    """

    def __init__(self) -> None:
        self._edges: dict[str, set[str]] = {}
        self._lock = threading.RLock()

    def wait(self, waiter: str, holder: str) -> list[str] | None:
        """Record the edge; return the cycle if one is created, else None."""
        with self._lock:
            self._edges.setdefault(waiter, set()).add(holder)
            return self._find_cycle_from(waiter)

    def clear(self, waiter: str, holder: str | None = None) -> None:
        with self._lock:
            if holder is None:
                self._edges.pop(waiter, None)
            elif waiter in self._edges:
                self._edges[waiter].discard(holder)
                if not self._edges[waiter]:
                    del self._edges[waiter]

    def _find_cycle_from(self, start: str) -> list[str] | None:
        # Iterative DFS with an explicit path stack; returns the cycle itself
        # (not just a boolean) so the caller can pick a victim to abort.
        path: list[str] = []
        on_path: set[str] = set()
        stack: list[tuple[str, Iterator[str]]] = [(start, iter(sorted(self._edges.get(start, ()))))]
        path.append(start)
        on_path.add(start)

        while stack:
            node, children = stack[-1]
            advanced = False
            for child in children:
                if child in on_path:
                    return path[path.index(child) :]
                if child in self._edges:
                    stack.append((child, iter(sorted(self._edges.get(child, ())))))
                    path.append(child)
                    on_path.add(child)
                    advanced = True
                    break
            if not advanced:
                stack.pop()
                on_path.discard(path.pop())
        return None

    def snapshot(self) -> dict[str, set[str]]:
        with self._lock:
            return {k: set(v) for k, v in self._edges.items()}


# --------------------------------------------------------------------------
# Resource manager — prevention
# --------------------------------------------------------------------------
@dataclass
class _Resource:
    name: str
    holder: str | None = None
    queue: deque[str] = field(default_factory=deque)  # FIFO -> no starvation
    acquired_at: float = 0.0


class ResourceManager:
    """Named exclusive locks with ordering, timeouts, and cycle detection.

    In the incident system these guard real-world resources: you must not let two
    concurrent remediations both restart `checkout-api`, and you must not let a
    rollback race a scale-up.
    """

    def __init__(self, ordered: bool = True, default_timeout: float = 5.0):
        self.ordered = ordered
        self.default_timeout = default_timeout
        self._resources: dict[str, _Resource] = {}
        self._held: dict[str, set[str]] = {}  # agent -> resources
        self._cond = threading.Condition()
        self.wait_graph = WaitForGraph()
        self.events: list[str] = []

    # -- single lock -------------------------------------------------------
    @contextmanager
    def hold(self, agent: str, resource: str, timeout: float | None = None):
        self.acquire(agent, resource, timeout)
        try:
            yield
        finally:
            self.release(agent, resource)

    def acquire(self, agent: str, resource: str, timeout: float | None = None) -> bool:
        timeout = self.default_timeout if timeout is None else timeout
        deadline = time.monotonic() + timeout
        with self._cond:
            res = self._resources.setdefault(resource, _Resource(resource))

            if self.ordered:
                # Break circular wait: never acquire a resource that sorts before
                # one you already hold. Violating this is a bug in the caller, so
                # it raises rather than blocking.
                held = self._held.get(agent, set())
                if any(resource < h for h in held):
                    raise DeadlockError([agent, resource])

            if res.holder is None:
                self._grant(agent, res)
                return True

            if res.holder == agent:
                return True  # re-entrant

            cycle = self.wait_graph.wait(agent, res.holder)
            if cycle:
                self.wait_graph.clear(agent, res.holder)
                self.events.append(f"cycle detected: {' -> '.join(cycle)}")
                raise DeadlockError(cycle)

            res.queue.append(agent)
            self.events.append(f"{agent} waits for {resource} (held by {res.holder})")
            try:
                while res.holder is not None and res.holder != agent:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise LockTimeout(agent, resource, timeout, res.holder)
                    self._cond.wait(remaining)
                if res.holder != agent:
                    self._grant(agent, res)
                return True
            finally:
                self.wait_graph.clear(agent, res.holder or "")
                if agent in res.queue:
                    res.queue.remove(agent)

    def release(self, agent: str, resource: str) -> None:
        with self._cond:
            res = self._resources.get(resource)
            if res is None or res.holder != agent:
                return
            res.holder = None
            self._held.get(agent, set()).discard(resource)
            self.events.append(f"{agent} released {resource}")
            if res.queue:
                self._grant(res.queue[0], res)
            self._cond.notify_all()

    def _grant(self, agent: str, res: _Resource) -> None:
        res.holder = agent
        res.acquired_at = time.monotonic()
        self._held.setdefault(agent, set()).add(res.name)
        self.events.append(f"{agent} acquired {res.name}")

    # -- multiple locks ----------------------------------------------------
    @contextmanager
    def hold_all(self, agent: str, resources: Iterable[str], timeout: float | None = None):
        """All-or-nothing acquisition in canonical order.

        Two protections at once: sorting removes circular wait, and rolling back
        on partial failure removes hold-and-wait. This is what the incident
        executor uses before touching a set of services.
        """
        ordered = sorted(set(resources))
        acquired: list[str] = []
        try:
            for name in ordered:
                self.acquire(agent, name, timeout)
                acquired.append(name)
            yield ordered
        except Exception:
            for name in reversed(acquired):
                self.release(agent, name)
            raise
        else:
            for name in reversed(acquired):
                self.release(agent, name)

    def holders(self) -> dict[str, str | None]:
        with self._cond:
            return {name: res.holder for name, res in self._resources.items()}


# --------------------------------------------------------------------------
# Livelock
# --------------------------------------------------------------------------
class LivelockDetector:
    """Detect "busy but not progressing" by watching a state signature repeat.

    Nothing is blocked in a livelock, so no wait-for cycle exists and lock-based
    detection sees a perfectly healthy system. What you can observe is that the
    state keeps returning to values it has already visited. Two agents toggling
    a replica count between 3 and 5 produce signatures 3,5,3,5,... — that repeat
    is the alarm.
    """

    def __init__(self, window: int = 6, repeats: int = 2):
        self.window = window
        self.repeats = repeats
        self._seen: deque[Hashable] = deque(maxlen=window)

    def observe(self, signature: Hashable) -> bool:
        """Record a state signature. True means livelock suspected."""
        self._seen.append(signature)
        return list(self._seen).count(signature) > self.repeats

    def reset(self) -> None:
        self._seen.clear()


def backoff(attempt: int, base: float = 0.05, cap: float = 2.0, jitter: bool = True) -> float:
    """Exponential backoff with full jitter.

    Jitter is not a nicety. Without it, agents that collide once retry in lockstep
    and collide again forever — that is the textbook livelock, and randomization
    is the fix.
    """
    delay = min(cap, base * (2**attempt))
    return random.uniform(0, delay) if jitter else delay


# --------------------------------------------------------------------------
# Gate deadlock — the one that actually happens
# --------------------------------------------------------------------------
@dataclass
class Deadline:
    """A gate with a timeout and a default. Every human-in-the-loop pause needs
    one, or "waiting for approval" becomes "hung forever at 3am"."""

    seconds: float
    on_expiry: str = "abort"  # "abort" | "proceed" | "escalate"
    started_at: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.started_at >= self.seconds

    @property
    def remaining(self) -> float:
        return max(0.0, self.seconds - (time.monotonic() - self.started_at))
